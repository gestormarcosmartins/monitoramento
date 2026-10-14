#!/usr/bin/env bash
# Roda NA MÁQUINA DE BACKUP (como root, a cada 15 min). A produção só consegue ENVIAR para /backups/incoming
# (chave restrita, write-only); aqui cada backup recebido é verificado e só então vira "arquivo", de onde a produção
# não alcança nada. Assim, quem invadir a produção não consegue ler, apagar nem adulterar backups já arquivados.
#
#   incoming/  -> recebido, ainda não confiável   (dono: nexorabkp, 0700)
#   archive/   -> verificado e imutável para a produção (dono: root, grupo opc, 0750)
#   rejected/  -> falhou na verificação (guardado para análise)
#
# Sai com erro (e o systemd marca a unidade como falha) se: algum backup for rejeitado, ou se o backup válido
# mais recente tiver mais de MAX_AGE_HOURS horas (alerta de "backup parou de chegar").
set -euo pipefail
BASE="${BACKUP_BASE:-/backups}"
KEEP="${KEEP:-30}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-36}"
SETTLE_MINUTES="${SETTLE_MINUTES:-5}"       # só considera pronto o que não muda há N minutos (upload terminou)
status=0
log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }

shopt -s nullglob
for dir in "$BASE"/incoming/nexora-*; do
  [ -d "$dir" ] || continue
  name="$(basename "$dir")"
  # ainda em transferência? (arquivos temporários do rsync começam com ".") ou modificado recentemente
  if find "$dir" -name '.*' -type f | grep -q . || [ -n "$(find "$dir" -mmin "-$SETTLE_MINUTES" -print -quit)" ]; then
    log "aguardando fim do envio: $name"; continue
  fi
  if [ ! -f "$dir/manifest.sha256" ]; then
    log "REJEITADO (sem manifesto): $name"; mv "$dir" "$BASE/rejected/$name"; status=1; continue
  fi
  # o manifesto precisa listar tudo o que existe na pasta (nada a mais, nada a menos)
  listed="$(awk '{print $2}' "$dir/manifest.sha256" | sed 's#^\./##' | sort)"
  present="$(cd "$dir" && find . -type f ! -name manifest.sha256 | sed 's#^\./##' | sort)"
  if [ "$listed" != "$present" ]; then
    log "REJEITADO (arquivos diferentes do manifesto): $name"; mv "$dir" "$BASE/rejected/$name"; status=1; continue
  fi
  if (cd "$dir" && sha256sum -c manifest.sha256 >/dev/null 2>&1); then
    chown -R root:opc "$dir"; chmod -R u=rwX,g=rX,o= "$dir"
    mv "$dir" "$BASE/archive/$name"
    log "ARQUIVADO e verificado: $name ($(du -sh "$BASE/archive/$name" | cut -f1))"
  else
    log "REJEITADO (hash não confere): $name"; mv "$dir" "$BASE/rejected/$name"; status=1
  fi
done

# Retenção: só depois de processar o que chegou; nunca apaga se o arquivo estiver vazio ou desatualizado.
newest="$(ls -1dt "$BASE"/archive/nexora-* 2>/dev/null | head -1 || true)"
if [ -n "$newest" ]; then
  ls -1dt "$BASE"/archive/nexora-* | tail -n +$((KEEP + 1)) | xargs -r rm -rf
  age_h=$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 3600 ))
  printf '{"last_ok":"%s","age_hours":%s,"archived":%s,"rejected":%s}\n' "$(basename "$newest")" "$age_h" \
    "$(ls -1d "$BASE"/archive/nexora-* | wc -l)" "$(ls -1d "$BASE"/rejected/nexora-* 2>/dev/null | wc -l)" > "$BASE/status.json"
  if [ "$age_h" -gt "$MAX_AGE_HOURS" ]; then log "ALERTA: último backup válido tem ${age_h}h (limite ${MAX_AGE_HOURS}h)"; status=1; fi
else
  log "ALERTA: ainda não há backup arquivado"; status=1
fi
exit $status
