#!/usr/bin/env bash
# Tests for apply-business-worker-update.sh on temporary directories, with a fake worker binary (no service, no root).
#   bash agent/tests/test_apply_update.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../apply-business-worker-update.sh"
ROOT="$(mktemp -d)"; trap 'rm -rf "$ROOT"' EXIT
PY="${PYTHON:-python}"
fails=0
check() { if [ "$1" = "$2" ]; then echo "PASS $3"; else echo "FAIL $3 (got '$1', want '$2')"; fails=$((fails+1)); fi; }

fake_binary() {  # dir version
  mkdir -p "$1"
  printf '#!/bin/sh\ncase "$1" in\n  --version) echo %s;;\n  --verify-staged) cat "$VERDICT_FILE" 2>/dev/null || echo ok;;\nesac\n' "$2" > "$1/Nexora.BusinessWorker"
  chmod +x "$1/Nexora.BusinessWorker"
}
package() {  # out version [extra python snippet]
  "$PY" - "$1" "$2" "${3:-}" <<'PYEOF'
import io, sys, tarfile
out, version, extra = sys.argv[1], sys.argv[2], sys.argv[3]
body = ('#!/bin/sh\ncase "$1" in\n  --version) echo %s;;\n  --verify-staged) echo ok;;\nesac\n' % version).encode()
with tarfile.open(out, "w:gz") as tar:
    if extra != "no-binary":
        info = tarfile.TarInfo("Nexora.BusinessWorker"); info.size = len(body); info.mode = 0o755
        tar.addfile(info, io.BytesIO(body))
    if extra == "symlink":
        link = tarfile.TarInfo("evil"); link.type = tarfile.SYMTYPE; link.linkname = "/etc/passwd"; tar.addfile(link)
    if extra == "traversal":
        data = b"x"; info = tarfile.TarInfo("../escape.txt"); info.size = 1; tar.addfile(info, io.BytesIO(data))
    if extra == "absolute":
        data = b"x"; info = tarfile.TarInfo("/tmp/abs.txt"); info.size = 1; tar.addfile(info, io.BytesIO(data))
PYEOF
}
stage() {  # updates_dir package version
  mkdir -p "$1/ready"; cp "$2" "$1/ready/package.pkg"; printf '{"version":"%s","platform":"linux-x64"}' "$3" > "$1/ready/pending.json"
}
fresh_env() {
  T="$(mktemp -d -p "$ROOT")"
  export INSTALL_DIR="$T/opt" UPDATES_DIR="$T/updates" ROOT_STATE="$T/state" VERDICT_FILE="$T/verdict" SERVICE_USER="nobody-such-user" MAX_BOOTS=3
  fake_binary "$INSTALL_DIR" 1.0.0; echo '{"Nexora":{"ServerUrl":"https://x"}}' > "$INSTALL_DIR/appsettings.json"; mkdir -p "$UPDATES_DIR"
}
version() { "$INSTALL_DIR/Nexora.BusinessWorker" --version; }
outcome() { sed -n 's/.*"result":"\([A-Z_]*\)".*/\1/p' "$UPDATES_DIR/last-applied.json" 2>/dev/null; }
run() { bash "$SCRIPT" 2>/dev/null; echo $?; }

# 1. a verified package is applied; settings survive; the previous version is kept for rollback
fresh_env; package "$T/p.tgz" 1.1.0; stage "$UPDATES_DIR" "$T/p.tgz" 1.1.0
check "$(run)" 0 "apply exits 0"
check "$(version)" 1.1.0 "new version installed"
check "$(cat "$INSTALL_DIR/appsettings.json" | tr -d '\n')" '{"Nexora":{"ServerUrl":"https://x"}}' "local settings preserved"
check "$("${INSTALL_DIR}.previous/Nexora.BusinessWorker" --version)" 1.0.0 "previous version kept"
check "$(outcome)" APPLIED "outcome APPLIED reported to the worker"
check "$([ -d "$UPDATES_DIR/ready" ] && echo present || echo gone)" gone "staged package consumed"
check "$(sed -n 's/^boots=//p' "$ROOT_STATE/probation")" 0 "probation started"

# 2. never healthy -> rolled back after MAX_BOOTS unconfirmed starts
for i in 1 2 3; do run >/dev/null; check "$(version)" 1.1.0 "start $i of probation keeps the new version"; done
check "$(run)" 0 "rollback run exits 0"
check "$(version)" 1.0.0 "rolled back to the previous version"
check "$(outcome)" ROLLED_BACK "outcome ROLLED_BACK"
check "$([ -f "$ROOT_STATE/probation" ] && echo present || echo gone)" gone "probation cleared after rollback"

# 3. healthy (confirmed by the worker) -> probation ends, nothing is rolled back
fresh_env; package "$T/p.tgz" 1.1.0; stage "$UPDATES_DIR" "$T/p.tgz" 1.1.0; run >/dev/null
echo 1.1.0 > "$UPDATES_DIR/confirmed"; run >/dev/null; run >/dev/null; run >/dev/null; run >/dev/null
check "$(version)" 1.1.0 "confirmed version is never rolled back"
check "$([ -f "$ROOT_STATE/probation" ] && echo present || echo gone)" gone "probation cleared once confirmed"

# 4. the installed binary refuses the staged package -> nothing changes
fresh_env; package "$T/p.tgz" 1.1.0; stage "$UPDATES_DIR" "$T/p.tgz" 1.1.0; echo hash_mismatch > "$VERDICT_FILE"
check "$(run)" 0 "refused update still exits 0 (the service must start)"
check "$(version)" 1.0.0 "installed version untouched"
check "$(outcome)" FAILED "outcome FAILED"
check "$(ls "$UPDATES_DIR/rejected" | wc -l | tr -d ' ')" 1 "rejected package kept for inspection"

# 5. unsafe archives are never unpacked
for kind in symlink traversal absolute no-binary; do
  fresh_env; package "$T/p.tgz" 1.1.0 "$kind"; stage "$UPDATES_DIR" "$T/p.tgz" 1.1.0; run >/dev/null
  check "$(version)" 1.0.0 "$kind archive refused"
  check "$(outcome)" FAILED "$kind archive reported as failed"
done
check "$([ -e "$ROOT/escape.txt" ] || [ -e /tmp/abs.txt ] && echo escaped || echo contained)" contained "no file escaped the unpack area"

# 6. nothing staged, or no binary at all -> no-op
fresh_env; check "$(run)" 0 "nothing staged: no-op"; check "$(version)" 1.0.0 "nothing staged: untouched"
rm -rf "$INSTALL_DIR"; check "$(run)" 0 "missing install: no-op, exit 0"

echo; [ "$fails" -eq 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
exit "$fails"
