# homelab-watchdogs

Three small watchdogs for a home server. They run from cron or systemd timers and need neither root
nor a mail server.

| Script | Watches | Does |
|---|---|---|
| `monitor.py` | CPU temperature, fan, load, memory, disk, network to the gateway, power cuts, failed logins | CSV time series, event log, optional notification |
| `dns-watch.sh` | a local DNS server such as AdGuard Home in Docker | restarts the container when it stops answering |
| `storage-check.sh` | a storage budget for one data folder and the free space on the system disk | status file, exit status 0, 1 or 2, optional notification |

## monitor.py

Reads everything from /proc and /sys: no `sensors` call and, in the normal case, no subprocess, so a
run takes a few milliseconds. It tells a power cut from a gap: when the readings stopped and the
machine booted after the last one, it was a restart without a clean shutdown.

```sh
monitor.py log       # one reading, for a timer every minute
monitor.py status    # current state and the latest events
```

Failed logins (sshd, PAM) are counted from the journal every ten minutes. It warns at 85 °C, 90 %
disk, five failed logins and five minutes without a reading. Each kind of event notifies at most
every 15 minutes.

| Variable | Default |
|---|---|
| `HOMELAB_WATCH_DIR` | `~/.local/share/homelab-watch` |
| `HOMELAB_WATCH_NOTIFY` | none; a command that gets the kind and the text appended, for example `notify-send -u critical` |

## dns-watch.sh

Docker's restart policy only helps when a container exits. A DNS server that hangs keeps running, and
the whole network loses name resolution. dns-watch asks the server twice, restarts the container,
checks again and raises an alert when it is still down.

| Variable | Default |
|---|---|
| `DNS_WATCH_SERVER` | `127.0.0.1` |
| `DNS_WATCH_NAME` | `example.com` |
| `DNS_WATCH_CONTAINER` | `adguard` |
| `DNS_WATCH_LOG` | `~/.local/state/dns-watch.log` |
| `DNS_WATCH_NOTIFY` | none |

Needs `dig` and the right to run `docker restart`.

## storage-check.sh

```sh
STORAGE_DIR=/srv/cloud STORAGE_LIMIT_GB=500 STORAGE_EXCLUDE=backup ./storage-check.sh
```

Warns at 80 % of the budget, reports when the budget is reached and turns critical below 40 GB free
on `/`. Further variables: `STORAGE_WARN_PCT`, `STORAGE_ROOT_FREE_MIN_GB`, `STORAGE_STATUS` and
`STORAGE_NOTIFY`.

## Schedule

```
* * * * *    /opt/homelab-watchdogs/monitor.py log
*/5 * * * *  /opt/homelab-watchdogs/dns-watch.sh
0 * * * *    STORAGE_DIR=/srv/cloud /opt/homelab-watchdogs/storage-check.sh
```

## Tests

`pytest` runs all three against stand-ins for dig, docker, du, df and logger, so nothing real gets
restarted.

## License

MIT
