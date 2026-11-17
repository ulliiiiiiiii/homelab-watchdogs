#!/usr/bin/env bash
# Check that a local DNS server (for example AdGuard Home in Docker) still answers, and restart its
# container when it does not. This catches what Docker's restart policy misses: the container keeps
# running but has stopped answering.
# Runs from cron or a systemd timer.
set -u

SERVER=${DNS_WATCH_SERVER:-127.0.0.1}
NAME=${DNS_WATCH_NAME:-example.com}
CONTAINER=${DNS_WATCH_CONTAINER:-adguard}
LOG=${DNS_WATCH_LOG:-${XDG_STATE_HOME:-$HOME/.local/state}/dns-watch.log}
NOTIFY=${DNS_WATCH_NOTIFY:-}           # optional command; the message is appended as its last argument
RETRY_PAUSE=${DNS_WATCH_RETRY_PAUSE:-3}  # seconds between the two attempts
WAIT=${DNS_WATCH_WAIT:-20}             # seconds to give the container after the restart

answers() {
  # two attempts, so a single hiccup does not trigger a restart
  for _ in 1 2; do
    if dig +short +time=3 +tries=1 "$NAME" "@$SERVER" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$RETRY_PAUSE"
  done
  return 1
}

notify() {
  [ -n "$NOTIFY" ] || return 0
  local cmd
  read -ra cmd <<< "$NOTIFY"
  "${cmd[@]}" "$1" >/dev/null 2>&1 || true
}

if answers; then
  exit 0
fi

mkdir -p "$(dirname "$LOG")"
echo "$(date '+%F %H:%M')  DNS does not answer, restarting $CONTAINER" >> "$LOG"
docker restart "$CONTAINER" >/dev/null 2>&1
sleep "$WAIT"

if answers; then
  echo "$(date '+%F %H:%M')  answering again" >> "$LOG"
else
  echo "$(date '+%F %H:%M')  still down after the restart, needs a look" >> "$LOG"
  logger -t dns-watch "$CONTAINER does not answer DNS after a restart"
  notify "DNS down: $CONTAINER does not answer after a restart. Emergency: point the router's DNS back to the provider."
fi

# keep the log short
tail -200 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
