#!/usr/bin/env python3
"""Lean watchdog for a home server: temperature, network, power cuts and login attacks.

    monitor.py log      write one reading (for a timer, every minute)
    monitor.py status   current state and the latest events

Reads everything straight from /proc and /sys: no sensors call and, in the normal case, no
subprocess. A run takes a few milliseconds.

    $HOMELAB_WATCH_DIR/readings.csv   time series
    $HOMELAB_WATCH_DIR/events.log     only what stood out

HOMELAB_WATCH_DIR defaults to ~/.local/share/homelab-watch. HOMELAB_WATCH_NOTIFY is an optional
command that gets the kind and the text of an event as its last two arguments.
"""
import argparse
import csv
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

DIR = Path(os.environ.get("HOMELAB_WATCH_DIR", Path.home() / ".local/share/homelab-watch"))
CSV = DIR / "readings.csv"
EVENTS = DIR / "events.log"

# thresholds
TEMP_WARN = 85.0  # °C
DISK_WARN = 90  # percent
AUTH_WARN = 5  # failed logins per check
GAP_MIN = 5  # minutes without a reading are suspicious
CPU_SENSORS = ("coretemp", "k10temp", "zenpower")  # Intel, AMD


# ------------------------------------------------------------------ measure ---
def _hwmon():
    """Find the files for CPU temperature and fan speed once."""
    temp = fan = None
    for h in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        name = (h / "name").read_text().strip() if (h / "name").exists() else ""
        if temp is None and name in CPU_SENSORS and (h / "temp1_input").exists():
            temp = h / "temp1_input"
        if fan is None:
            # some drivers (applesmc) put the fans under device/, not in the hwmon folder itself
            for base in (h, h / "device"):
                found = sorted(base.glob("fan*_input")) if base.is_dir() else []
                if found:
                    fan = found[0]
                    break
    if temp is None:
        zone = Path("/sys/class/thermal/thermal_zone0/temp")
        if zone.exists():
            temp = zone
    return temp, fan


TEMP_PATH, FAN_PATH = _hwmon()


def temperature():
    try:
        return int(TEMP_PATH.read_text().strip()) / 1000.0
    except Exception:
        return None


def fan_rpm():
    try:
        return int(FAN_PATH.read_text().strip())
    except Exception:
        return None


def load1():
    return float(open("/proc/loadavg").read().split()[0])


def memory_pct():
    m = {}
    for line in open("/proc/meminfo"):
        key, value = line.split(":", 1)
        m[key] = int(value.split()[0])
    total, available = m["MemTotal"], m.get("MemAvailable", m["MemFree"])
    return round((total - available) / total * 100)


def disk_pct():
    s = os.statvfs("/")
    return round((s.f_blocks - s.f_bfree) / s.f_blocks * 100)


def gateway(route_file="/proc/net/route"):
    try:
        for line in open(route_file):
            f = line.split()
            if len(f) > 2 and f[1] == "00000000":
                h = f[2]
                return ".".join(str(int(h[i:i + 2], 16)) for i in (6, 4, 2, 0))
    except Exception:
        pass
    return None


def network_ok():
    """TCP connect to the gateway, no ping subprocess."""
    gw = gateway()
    if not gw:
        return 0
    for port in (80, 53):
        try:
            with socket.create_connection((gw, port), timeout=1.5):
                return 1
        except OSError:
            continue
    return 0


def boot_time():
    for line in open("/proc/stat"):
        if line.startswith("btime"):
            return float(line.split()[1])
    return 0.0


def failed_logins(since):
    """Failed logins since <since>. Only called every ten minutes."""
    try:
        p = subprocess.run(["journalctl", "-q", "--since", f"@{int(since)}", "--no-pager"],
                           capture_output=True, text=True, timeout=15)
        return sum(1 for line in p.stdout.splitlines()
                   if "Failed password" in line or "authentication failure" in line or "Invalid user" in line)
    except Exception:
        return 0


# -------------------------------------------------------------------- write ---
def event(kind, text):
    """Write to the event log and run the notify command. Debounced: the same kind notifies at
    most every 15 minutes."""
    DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS.open("a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  [{kind}] {text}\n")
    lock = DIR / f".notified_{kind}"
    if lock.exists() and (time.time() - lock.stat().st_mtime) < 900:
        return
    lock.write_text(str(time.time()))
    command = os.environ.get("HOMELAB_WATCH_NOTIFY", "").split()
    if command:
        try:
            subprocess.Popen([*command, kind, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def last_reading():
    if not CSV.exists():
        return None
    try:
        with CSV.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            lines = f.read().decode(errors="replace").splitlines()
        for line in reversed(lines):
            t = line.split(",")
            if t and t[0].isdigit():
                return float(t[0])
    except Exception:
        pass
    return None


def cmd_log(_args):
    now = time.time()
    DIR.mkdir(parents=True, exist_ok=True)

    # power cut or hard reset: a gap in our own log plus a fresh boot
    before = last_reading()
    booted = boot_time()
    if before and (now - before) > GAP_MIN * 60:
        minutes = int((now - before) / 60)
        if booted > before:
            event("POWER", f"restart without a clean shutdown, {minutes} min gap, "
                           f"booted at {datetime.fromtimestamp(booted):%H:%M}")
        else:
            event("GAP", f"{minutes} min without readings (the system kept running)")

    t, f = temperature(), fan_rpm()
    net = network_ok()
    disk = disk_pct()

    if t and t >= TEMP_WARN:
        event("TEMP", f"{t:.1f} °C")
    if not net:
        event("NET", f"gateway {gateway()} not reachable")
    if disk >= DISK_WARN:
        event("DISK", f"{disk} % used")

    # attacks: expensive, so only every ten minutes
    fails = ""
    if int(now // 60) % 10 == 0:
        n = failed_logins(now - 600)
        fails = n
        if n >= AUTH_WARN:
            event("ATTACK", f"{n} failed logins in 10 min")

    new = not CSV.exists()
    with CSV.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["ts", "temp_c", "fan_rpm", "load1", "mem_pct", "disk_pct", "net", "auth_fails"])
        w.writerow([f"{now:.0f}", f"{t:.1f}" if t else "", f or "", f"{load1():.2f}", memory_pct(), disk, net, fails])


def cmd_status(_args):
    t, f = temperature(), fan_rpm()
    print(f"\n  temperature  {t:.1f} °C" if t else "\n  temperature  ?")
    if f:
        print(f"  fan          {f} RPM")
    print(f"  load         {load1():.2f}")
    print(f"  memory       {memory_pct()} %")
    print(f"  disk         {disk_pct()} %")
    print(f"  network      {'ok' if network_ok() else 'DOWN'}  (gateway {gateway()})")
    n = sum(1 for _ in CSV.open()) - 1 if CSV.exists() else 0
    print(f"  readings     {n}")
    if EVENTS.exists():
        lines = EVENTS.read_text().splitlines()
        print(f"\n  events ({len(lines)}):")
        for e in lines[-10:]:
            print("   ", e)
    else:
        print("\n  no events")
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("log").set_defaults(fn=cmd_log)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
