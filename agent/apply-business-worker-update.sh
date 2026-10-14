#!/usr/bin/env bash
# Applies a staged Business Worker update BEFORE the service starts (systemd: ExecStartPre=+), with probation and rollback.
#
# Trust: the package is never trusted because of where it sits. The currently installed (root-owned) binary re-verifies the
# central's signed manifest, the package size and its SHA-256 (`--verify-staged`); only then is the package unpacked and swapped
# in. If the new version does not report healthy within MAX_BOOTS starts, the previous version is restored.
#
# Layout (all overridable for tests):
#   INSTALL_DIR   /opt/nexora-business-worker           root-owned program directory (swapped by this script)
#   UPDATES_DIR   /var/lib/nexora-business-worker/updates   written by the service: ready/{pending.json,package.pkg}, confirmed, last-applied.json
#   ROOT_STATE    /var/lib/nexora-business-worker-apply  root-only: probation marker and the unpacking area
# Never fails the unit: on any problem it logs, leaves the installed version alone and exits 0 so the service still starts.
set -u

INSTALL_DIR="${INSTALL_DIR:-/opt/nexora-business-worker}"
UPDATES_DIR="${UPDATES_DIR:-/var/lib/nexora-business-worker/updates}"
ROOT_STATE="${ROOT_STATE:-/var/lib/nexora-business-worker-apply}"
BINARY="${BINARY:-Nexora.BusinessWorker}"
SERVICE_USER="${SERVICE_USER:-nexora-bw}"
MAX_BOOTS="${MAX_BOOTS:-3}"
PREVIOUS_DIR="${INSTALL_DIR}.previous"
PROBATION="$ROOT_STATE/probation"

log() { echo "nexora-apply-update: $*" >&2; }

write_outcome() {  # version result error  -> read (and deleted) by the worker, which reports it to the central
  local version="$1" result="$2" error="${3:-}"
  mkdir -p "$UPDATES_DIR"
  printf '{"version":"%s","result":"%s","error":"%s"}\n' "$version" "$result" "$error" > "$UPDATES_DIR/last-applied.json"
  chown "$SERVICE_USER" "$UPDATES_DIR/last-applied.json" 2>/dev/null || true
}

installed_version() { "$1/$BINARY" --version 2>/dev/null | head -n1 | tr -cd '0-9.'; }

reject_staged() {  # code
  local code="$1" stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  log "staged update rejected: $code"
  mkdir -p "$UPDATES_DIR/rejected"
  mv "$UPDATES_DIR/ready" "$UPDATES_DIR/rejected/$stamp" 2>/dev/null || rm -rf "$UPDATES_DIR/ready"
  write_outcome "$(sed -n 's/.*"version" *: *"\([0-9.]*\)".*/\1/p' "$UPDATES_DIR/rejected/$stamp/pending.json" 2>/dev/null | head -n1)" FAILED "$code"
}

# ---------------------------------------------------------------- probation of a freshly applied version
probation_check() {
  [ -f "$PROBATION" ] || return 0
  local version previous boots confirmed=""
  version="$(sed -n 's/^version=//p' "$PROBATION")"; previous="$(sed -n 's/^previous=//p' "$PROBATION")"; boots="$(sed -n 's/^boots=//p' "$PROBATION")"
  [ -f "$UPDATES_DIR/confirmed" ] && confirmed="$(tr -cd '0-9.' < "$UPDATES_DIR/confirmed")"
  if [ "$confirmed" = "$version" ]; then
    log "version $version confirmed healthy"
    rm -f "$PROBATION"
    return 0
  fi
  boots=$(( ${boots:-0} + 1 ))
  if [ "$boots" -le "$MAX_BOOTS" ]; then
    sed -i "s/^boots=.*/boots=$boots/" "$PROBATION"
    log "version $version not confirmed yet (start $boots of $MAX_BOOTS)"
    return 0
  fi
  if [ -d "$PREVIOUS_DIR" ]; then
    log "version $version never became healthy; rolling back to ${previous:-previous}"
    rm -rf "$INSTALL_DIR.failed" && mv "$INSTALL_DIR" "$INSTALL_DIR.failed" && mv "$PREVIOUS_DIR" "$INSTALL_DIR" || { log "rollback failed"; return 0; }
    rm -rf "$INSTALL_DIR.failed"
    write_outcome "$version" ROLLED_BACK "never_healthy"
  fi
  rm -f "$PROBATION"
}

# ---------------------------------------------------------------- apply a staged package
unsafe_archive() {  # returns 0 (true) when the archive must not be unpacked
  local names kinds
  names="$(tar -tzf "$1" 2>/dev/null)" || return 0
  kinds="$(tar -tzvf "$1" 2>/dev/null | cut -c1)" || return 0
  [ -n "$names" ] || return 0
  echo "$kinds" | grep -Eq '[lh]' && return 0                          # symlinks / hard links
  echo "$names" | grep -Eq '(^/|(^|/)\.\.(/|$))' && return 0           # absolute or parent paths
  echo "$names" | grep -Eq "^(\./)?${BINARY}$" || return 0            # must contain the worker binary
  return 1
}

apply_staged() {
  local ready="$UPDATES_DIR/ready"
  [ -f "$ready/pending.json" ] || return 0
  local verdict version
  verdict="$(cd "$INSTALL_DIR" && "./$BINARY" --verify-staged "$UPDATES_DIR" 2>/dev/null | tail -n1)"   # appsettings.json is read from the working directory
  version="$(sed -n 's/.*"version" *: *"\([0-9.]*\)".*/\1/p' "$ready/pending.json" | head -n1)"
  [ "$verdict" = "ok" ] || { reject_staged "verify_${verdict:-failed}"; return 0; }
  if unsafe_archive "$ready/package.pkg"; then reject_staged "unsafe_archive"; return 0; fi

  local fresh="$ROOT_STATE/new" previous_version
  rm -rf "$fresh"; mkdir -p "$fresh"
  tar -xzf "$ready/package.pkg" -C "$fresh" --no-same-owner --no-same-permissions || { rm -rf "$fresh"; reject_staged "unpack_failed"; return 0; }
  [ -f "$fresh/$BINARY" ] || { rm -rf "$fresh"; reject_staged "binary_missing"; return 0; }
  [ -f "$INSTALL_DIR/appsettings.json" ] && cp -p "$INSTALL_DIR/appsettings.json" "$fresh/appsettings.json"   # local settings are not part of a release
  chmod 0755 "$fresh/$BINARY"; chown -R root:root "$fresh" 2>/dev/null || true

  previous_version="$(installed_version "$INSTALL_DIR")"
  rm -rf "$PREVIOUS_DIR"
  if mv "$INSTALL_DIR" "$PREVIOUS_DIR"; then
    if mv "$fresh" "$INSTALL_DIR"; then
      mkdir -p "$ROOT_STATE"
      printf 'version=%s\nprevious=%s\nboots=0\n' "$version" "$previous_version" > "$PROBATION"
      rm -rf "$UPDATES_DIR/ready" "$UPDATES_DIR/confirmed"
      write_outcome "$version" APPLIED ""
      log "applied $version (previous ${previous_version:-unknown}); on probation until it reports healthy"
    else
      mv "$PREVIOUS_DIR" "$INSTALL_DIR"; reject_staged "swap_failed"
    fi
  else
    reject_staged "swap_failed"
  fi
}

mkdir -p "$ROOT_STATE"; chmod 0700 "$ROOT_STATE" 2>/dev/null || true
if [ ! -x "$INSTALL_DIR/$BINARY" ]; then log "installed binary not found; nothing to do"; exit 0; fi
probation_check
apply_staged
exit 0
