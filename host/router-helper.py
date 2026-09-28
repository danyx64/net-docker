#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import threading

SOCK = "/run/net-router/router.sock"
WAN1 = os.getenv("NET_ROUTER_WAN1", "enp3s0")
WAN2 = os.getenv("NET_ROUTER_WAN2", "enx00e04c680270")

ALLOWED = {WAN1: "wan1", WAN2: "wan2"}

def cmd(argv, timeout=130):
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=True).stdout

def gateway(dev):
    out = cmd(["ip", "-4", "route", "show", "default", "dev", dev])
    for line in out.splitlines():
        p = line.split()
        if "via" in p:
            return p[p.index("via") + 1]
    raise RuntimeError(f"no default gateway on {dev}")

def source_ip(dev):
    out = cmd(["ip", "-4", "-o", "addr", "show", "dev", dev])
    for line in out.splitlines():
        p = line.split()
        if "inet" in p:
            return p[p.index("inet") + 1].split("/")[0]
    raise RuntimeError(f"no IPv4 address on {dev}")

def status():
    out = cmd(["ip", "-4", "route", "show", "default"])
    active = None
    for line in out.splitlines():
        if " dev " + WAN1 + " " in " " + line + " ":
            active = "wan1"
            break
        if " dev " + WAN2 + " " in " " + line + " ":
            active = "wan2"
            break
    return {"ok": True, "active": active, "routes": out.strip()}

def do_switch(which):
    dev = WAN1 if which == "wan1" else WAN2
    gw = gateway(dev)
    src = source_ip(dev)
    subprocess.run(["ip", "route", "replace", "default", "via", gw, "dev", dev, "src", src, "metric", "5"], check=True)
    other = WAN2 if dev == WAN1 else WAN1
    try:
        ogw = gateway(other)
        osrc = source_ip(other)
        subprocess.run(["ip", "route", "replace", "default", "via", ogw, "dev", other, "src", osrc, "metric", "500"], check=True)
    except Exception:
        pass
    return {"ok": True, "active": which}

def speedtest(interface, server_id, timeout):
    if interface not in ALLOWED:
        raise RuntimeError("interface not allowed")
    src = source_ip(interface)
    args = ["speedtest", "--accept-license", "--accept-gdpr", "--format=json", "--interface", src]
    if int(server_id):
        args += ["--server-id", str(int(server_id))]
    raw = cmd(args, timeout=timeout)
    j = json.loads(raw)
    return {
        "ok": True,
        "download_mbps": (j["download"]["bandwidth"] * 8) / 1_000_000,
        "upload_mbps": (j["upload"]["bandwidth"] * 8) / 1_000_000,
        "ping_ms": j.get("ping", {}).get("latency"),
        "server": j.get("server", {}).get("name"),
    }

def handle(conn):
    try:
        req = json.loads(conn.recv(65536).decode().strip())
        action = req.get("action")
        if action == "status":
            res = status()
        elif action == "switch" and req.get("wan") in ("wan1", "wan2"):
            res = do_switch(req["wan"])
        elif action == "speedtest":
            res = speedtest(req["interface"], req.get("server_id", 0), int(req.get("timeout", 120)))
        else:
            raise RuntimeError("invalid request")
    except Exception as e:
        res = {"ok": False, "error": str(e)}
    conn.sendall(json.dumps(res).encode())
    conn.close()

os.makedirs(os.path.dirname(SOCK), exist_ok=True)
try:
    os.unlink(SOCK)
except FileNotFoundError:
    pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(SOCK)
os.chmod(SOCK, 0o666)
s.listen(20)
while True:
    c, _ = s.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
