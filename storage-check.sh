#!/usr/bin/env bash
# Watch a storage budget for one data folder and the free space on the system disk.
# Needs no root and no mail server. Exit status: 0 fine, 1 warning, 2 critical.
set -u

DATA_DIR=${STORAGE_DIR:?set STORAGE_DIR to the folder to watch}
LIMIT_GB=${STORAGE_LIMIT_GB:-500}                  # soft budget for DATA_DIR
WARN_PCT=${STORAGE_WARN_PCT:-80}                   # warn from here
ROOT_FREE_MIN_GB=${STORAGE_ROOT_FREE_MIN_GB:-40}   # below this the system disk gets critical
EXCLUDE=${STORAGE_EXCLUDE:-}                       # a subfolder that does not count, for example backup
STATUS=${STORAGE_STATUS:-${XDG_STATE_HOME:-$HOME/.local/state}/storage-status.txt}
NOTIFY=${STORAGE_NOTIFY:-}                         # optional command; the message is appended

notify() {
  [ -n "$NOTIFY" ] || return 0
  local cmd
  read -ra cmd <<< "$NOTIFY"
  "${cmd[@]}" "$1" >/dev/null 2>&1 || true
}

DATA_DIR=${DATA_DIR%/}
du_args=(-sBG)
[ -n "$EXCLUDE" ] && du_args+=("--exclude=$DATA_DIR/$EXCLUDE")
used_gb=$(du "${du_args[@]}" "$DATA_DIR" 2>/dev/null | awk '{gsub("G","",$1); print $1}')
used_gb=${used_gb:-0}
root_free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
pct=$(( used_gb * 100 / LIMIT_GB ))

state="OK"
(( pct >= WARN_PCT ))                  && state="WARNING"
(( used_gb >= LIMIT_GB ))              && state="BUDGET REACHED"
(( root_free_gb <= ROOT_FREE_MIN_GB )) && state="CRITICAL: system disk running out"

mkdir -p "$(dirname "$STATUS")"
{
  echo "As of:             $(date '+%Y-%m-%d %H:%M')"
  echo "Data used:         ${used_gb} GB of ${LIMIT_GB} GB  (${pct} %)"
  echo "System disk free:  ${root_free_gb} GB"
  echo "State:             ${state}"
  echo
  echo "By folder:"
  du -sBG "$DATA_DIR"/*/ 2>/dev/null | sed "s|$DATA_DIR/|  |" | sort -rh
} > "$STATUS"

cat "$STATUS"

if [ "$state" != "OK" ]; then
  logger -t storage-check "$state, ${used_gb}/${LIMIT_GB} GB used, / has ${root_free_gb} GB free"
  notify "Storage: $state (${used_gb}/${LIMIT_GB} GB)"
fi

case "$state" in
  OK) exit 0 ;;
  CRITICAL*) exit 2 ;;
  *) exit 1 ;;
esac
