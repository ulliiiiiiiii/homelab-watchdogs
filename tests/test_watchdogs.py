import os
import subprocess
import time
from pathlib import Path

import monitor

ROOT = Path(__file__).resolve().parent.parent


def stub(bindir: Path, name: str, body: str) -> None:
    bindir.mkdir(exist_ok=True)
    (bindir / name).write_text("#!/bin/sh\n" + body + "\n")
    (bindir / name).chmod(0o755)


def run(script: str, tmp_path: Path, **env) -> subprocess.CompletedProcess:
    full = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", **env}
    return subprocess.run(["bash", str(ROOT / script)], env=full, capture_output=True, text=True, check=False)


# ------------------------------------------------------------------ monitor.py

def test_gateway_from_route_table(tmp_path):
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\tFlags\n"
                     "eth0\t0001A8C0\t00000000\t0001\n"
                     "eth0\t00000000\t0101A8C0\t0003\n")
    assert monitor.gateway(str(route)) == "192.168.1.1"


def test_event_is_logged_every_time_but_notifies_once(tmp_path, monkeypatch):
    stub(tmp_path / "bin", "capture", 'echo "$@" >> "$CAPTURE"')
    monkeypatch.setattr(monitor, "DIR", tmp_path)
    monkeypatch.setattr(monitor, "EVENTS", tmp_path / "events.log")
    monkeypatch.setenv("HOMELAB_WATCH_NOTIFY", str(tmp_path / "bin" / "capture"))
    monkeypatch.setenv("CAPTURE", str(tmp_path / "captured.txt"))
    monitor.event("TEMP", "91.0 C")
    monitor.event("TEMP", "92.0 C")
    for _ in range(40):  # the notify command runs in the background
        if (tmp_path / "captured.txt").exists():
            break
        time.sleep(0.05)
    assert len((tmp_path / "events.log").read_text().splitlines()) == 2
    assert (tmp_path / "captured.txt").read_text().splitlines() == ["TEMP 91.0 C"]


def readings_setup(tmp_path, monkeypatch, last, booted):
    monkeypatch.setattr(monitor, "DIR", tmp_path)
    monkeypatch.setattr(monitor, "CSV", tmp_path / "readings.csv")
    monkeypatch.setattr(monitor, "EVENTS", tmp_path / "events.log")
    monkeypatch.delenv("HOMELAB_WATCH_NOTIFY", raising=False)
    monkeypatch.setattr(monitor, "last_reading", lambda: last)
    monkeypatch.setattr(monitor, "boot_time", lambda: booted)
    monkeypatch.setattr(monitor, "temperature", lambda: 45.0)
    monkeypatch.setattr(monitor, "network_ok", lambda: 1)
    monkeypatch.setattr(monitor, "disk_pct", lambda: 40)
    monkeypatch.setattr(monitor, "failed_logins", lambda since: 0)


def test_power_cut_is_told_apart_from_a_gap(tmp_path, monkeypatch):
    now = time.time()
    readings_setup(tmp_path, monkeypatch, last=now - 3600, booted=now - 120)
    monitor.cmd_log(None)
    assert "[POWER] restart without a clean shutdown, 60 min gap" in (tmp_path / "events.log").read_text()
    header, row = (tmp_path / "readings.csv").read_text().splitlines()
    assert header == "ts,temp_c,fan_rpm,load1,mem_pct,disk_pct,net,auth_fails"
    assert row.split(",")[1] == "45.0"

    readings_setup(tmp_path, monkeypatch, last=now - 3600, booted=now - 86400)
    monitor.cmd_log(None)
    assert "[GAP] 60 min without readings" in (tmp_path / "events.log").read_text()


# ---------------------------------------------------------------- dns-watch.sh

def dns_run(tmp_path, failures):
    (tmp_path / "fails").write_text(str(failures))
    stub(tmp_path / "bin", "dig", 'n=$(cat "$FAILS"); if [ "$n" -gt 0 ]; then echo $((n-1)) > "$FAILS"; exit 9; fi')
    stub(tmp_path / "bin", "docker", 'echo "$@" >> "$DOCKER"')
    stub(tmp_path / "bin", "logger", 'echo "$@" >> "$LOGGER"')
    stub(tmp_path / "bin", "capture", 'echo "$@" >> "$CAPTURE"')
    return run("dns-watch.sh", tmp_path, FAILS=str(tmp_path / "fails"), DOCKER=str(tmp_path / "docker.txt"),
               LOGGER=str(tmp_path / "logger.txt"), CAPTURE=str(tmp_path / "captured.txt"),
               DNS_WATCH_LOG=str(tmp_path / "dns.log"), DNS_WATCH_RETRY_PAUSE="0", DNS_WATCH_WAIT="0",
               DNS_WATCH_NOTIFY="capture")


def test_dns_answering_does_nothing(tmp_path):
    assert dns_run(tmp_path, failures=0).returncode == 0
    assert not (tmp_path / "docker.txt").exists() and not (tmp_path / "dns.log").exists()


def test_dns_down_restarts_the_container_once(tmp_path):
    dns_run(tmp_path, failures=2)
    assert (tmp_path / "docker.txt").read_text().splitlines() == ["restart adguard"]
    assert "answering again" in (tmp_path / "dns.log").read_text()
    assert not (tmp_path / "captured.txt").exists()


def test_dns_still_down_after_restart_alerts(tmp_path):
    dns_run(tmp_path, failures=10)
    assert "still down after the restart" in (tmp_path / "dns.log").read_text()
    assert "dns-watch" in (tmp_path / "logger.txt").read_text()
    assert (tmp_path / "captured.txt").read_text().startswith("DNS down: adguard")


# ------------------------------------------------------------ storage-check.sh

def storage_run(tmp_path, used, root_free, **extra):
    data = tmp_path / "data"
    for sub in ("photos", "files", "backup"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    stub(tmp_path / "bin", "du", 'echo "$@" >> "$DU_ARGS"\nfor a in "$@"; do case "$a" in -*) ;; '
         '"$DATA") printf "%sG\\t%s\\n" "$USED" "$a" ;; *) printf "1G\\t%s\\n" "$a" ;; esac; done')
    stub(tmp_path / "bin", "df", 'printf "Avail\\n %sG\\n" "$ROOT_FREE"')
    stub(tmp_path / "bin", "logger", 'echo "$@" >> "$LOGGER"')
    return run("storage-check.sh", tmp_path, STORAGE_DIR=str(data), DATA=str(data), USED=str(used),
               ROOT_FREE=str(root_free), DU_ARGS=str(tmp_path / "du.txt"), LOGGER=str(tmp_path / "logger.txt"),
               STORAGE_STATUS=str(tmp_path / "status.txt"), **extra)


def test_storage_states_and_exit_status(tmp_path):
    ok = storage_run(tmp_path, used=100, root_free=100)
    assert ok.returncode == 0 and "State:             OK" in ok.stdout
    assert "photos/" in (tmp_path / "status.txt").read_text()
    warning = storage_run(tmp_path, used=450, root_free=100)
    assert warning.returncode == 1 and "WARNING" in warning.stdout
    assert storage_run(tmp_path, used=520, root_free=100).returncode == 1  # budget reached
    critical = storage_run(tmp_path, used=100, root_free=10)
    assert critical.returncode == 2 and "CRITICAL" in critical.stdout
    assert "storage-check" in (tmp_path / "logger.txt").read_text()


def test_storage_exclude_uses_the_full_path(tmp_path):
    storage_run(tmp_path, used=100, root_free=100, STORAGE_EXCLUDE="backup")
    assert f"--exclude={tmp_path / 'data' / 'backup'}" in (tmp_path / "du.txt").read_text()
