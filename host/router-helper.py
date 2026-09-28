#!/usr/bin/env python3
import ipaddress
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

SOCK = "/run/net-router/router.sock"
PERSIST = Path("/etc/net-router/network.json")
RUNTIME_DIR = Path("/run/net-router")

def run(argv, timeout=180, check=True):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "command failed").strip())
    return p.stdout.strip()

def nm_exists(name):
    p = subprocess.run(["nmcli", "-t", "-f", "NAME", "connection", "show", name], text=True, capture_output=True)
    return p.returncode == 0

def nm_delete(name):
    subprocess.run(["nmcli", "connection", "delete", name], text=True, capture_output=True)

def ipv4_for(dev):
    out = run(["ip", "-4", "-o", "addr", "show", "dev", dev], check=False)
    for line in out.splitlines():
        parts = line.split()
        if "inet" in parts:
            return parts[parts.index("inet") + 1].split("/")[0]
    return None

def gateway_for(dev):
    out = run(["ip", "-4", "route", "show", "default", "dev", dev], check=False)
    for line in out.splitlines():
        parts = line.split()
        if "via" in parts:
            return parts[parts.index("via") + 1]
    return None

def interfaces():
    raw = run(["ip", "-j", "link", "show"])
    links = json.loads(raw)
    result = []
    for x in links:
        name = x.get("ifname")
        if not name or name in ("lo", "docker0", "tailscale0"):
            continue
        result.append({
            "name": name,
            "mac": x.get("address"),
            "state": x.get("operstate"),
            "master": x.get("master"),
            "ipv4": ipv4_for(name),
            "gateway": gateway_for(name),
        })
    return {"ok": True, "interfaces": result}

def validate_network(c):
    required = ["wan1_interface", "wan2_interface", "lan_interface", "bridge_name", "lan_cidr", "dhcp_start", "dhcp_end"]
    for k in required:
        if not c.get(k):
            raise RuntimeError(f"missing {k}")
    ports = [c["wan1_interface"], c["wan2_interface"], c["lan_interface"]]
    if len(set(ports)) != 3:
        raise RuntimeError("WAN1, WAN2 and LAN must be three different interfaces")
    if not c["bridge_name"].replace("-", "").replace("_", "").isalnum():
        raise RuntimeError("invalid bridge name")
    net = ipaddress.ip_interface(c["lan_cidr"])
    start = ipaddress.ip_address(c["dhcp_start"])
    end = ipaddress.ip_address(c["dhcp_end"])
    if start not in net.network or end not in net.network or int(start) > int(end):
        raise RuntimeError("DHCP range must be inside LAN subnet")
    return net

def write_dnsmasq(c, net):
    dns = c.get("dns_servers") or ["1.1.1.1", "8.8.8.8"]
    lease = c.get("dhcp_lease", "12h")
    bridge = c["bridge_name"]
    conf = f"""interface={bridge}
bind-dynamic
dhcp-range={c['dhcp_start']},{c['dhcp_end']},{net.network.netmask},{lease}
dhcp-option=3,{net.ip}
dhcp-option=6,{','.join(dns)}
"""
    Path("/etc/dnsmasq.d/net-router.conf").write_text(conf)
    run(["systemctl", "enable", "dnsmasq"], check=False)
    run(["systemctl", "restart", "dnsmasq"])

def setup_firewall(c):
    wan1 = c["wan1_interface"]
    wan2 = c["wan2_interface"]
    subprocess.run(["nft", "delete", "table", "inet", "net_router"], capture_output=True, text=True)
    cmds = [
        ["nft", "add", "table", "inet", "net_router"],
        ["nft", "add", "chain", "inet", "net_router", "forward", "{", "type", "filter", "hook", "forward", "priority", "0", ";", "policy", "accept", ";", "}"],
        ["nft", "add", "chain", "inet", "net_router", "postrouting", "{", "type", "nat", "hook", "postrouting", "priority", "100", ";", "policy", "accept", ";", "}"],
        ["nft", "add", "rule", "inet", "net_router", "postrouting", "oifname", wan1, "masquerade"],
        ["nft", "add", "rule", "inet", "net_router", "postrouting", "oifname", wan2, "masquerade"],
    ]
    for cmd in cmds:
        run(cmd)
    Path("/etc/sysctl.d/99-net-router.conf").write_text("net.ipv4.ip_forward=1\n")
    run(["sysctl", "-w", "net.ipv4.ip_forward=1"])

def setup_nm(c, net):
    wan1 = c["wan1_interface"]
    wan2 = c["wan2_interface"]
    lan = c["lan_interface"]
    bridge = c["bridge_name"]

    for name in ("net-router-wan1", "net-router-wan2", "net-router-bridge", "net-router-lan-port"):
        nm_delete(name)

    run(["nmcli", "connection", "add", "type", "ethernet", "ifname", wan1, "con-name", "net-router-wan1",
         "ipv4.method", "auto", "ipv4.route-metric", "100", "ipv6.method", "auto"])
    run(["nmcli", "connection", "add", "type", "ethernet", "ifname", wan2, "con-name", "net-router-wan2",
         "ipv4.method", "auto", "ipv4.route-metric", "500", "ipv6.method", "auto"])
    run(["nmcli", "connection", "add", "type", "bridge", "ifname", bridge, "con-name", "net-router-bridge",
         "ipv4.method", "manual", "ipv4.addresses", str(net), "ipv4.never-default", "yes", "ipv6.method", "disabled"])
    run(["nmcli", "connection", "add", "type", "ethernet", "ifname", lan, "con-name", "net-router-lan-port",
         "master", bridge, "slave-type", "bridge"])

    run(["nmcli", "connection", "up", "net-router-bridge"], check=False)
    run(["nmcli", "connection", "up", "net-router-lan-port"], check=False)
    run(["nmcli", "connection", "up", "net-router-wan1"], check=False)
    run(["nmcli", "connection", "up", "net-router-wan2"], check=False)

def apply_network(c):
    net = validate_network(c)
    PERSIST.parent.mkdir(parents=True, exist_ok=True)
    PERSIST.write_text(json.dumps(c, indent=2))
    setup_nm(c, net)
    write_dnsmasq(c, net)
    setup_firewall(c)
    return status(c)

def load_persisted():
    if PERSIST.exists():
        return json.loads(PERSIST.read_text())
    return None

def active_wan(c):
    wan1 = c["wan1_interface"]
    wan2 = c["wan2_interface"]
    out = run(["ip", "-4", "route", "show", "default"], check=False)
    best = None
    best_metric = 2**31
    for line in out.splitlines():
        p = line.split()
        if "dev" not in p:
            continue
        dev = p[p.index("dev") + 1]
        if dev not in (wan1, wan2):
            continue
        metric = 0
        if "metric" in p:
            try:
                metric = int(p[p.index("metric") + 1])
            except Exception:
                pass
        if metric < best_metric:
            best_metric = metric
            best = "wan1" if dev == wan1 else "wan2"
    return best

def status(c=None):
    c = c or load_persisted()
    routes = run(["ip", "-4", "route", "show", "default"], check=False)
    return {
        "ok": True,
        "active": active_wan(c) if c else None,
        "routes": routes,
        "network": c,
        "interfaces": interfaces()["interfaces"],
    }

def switch(which, c=None):
    c = c or load_persisted()
    if not c:
        raise RuntimeError("network has not been applied yet")
    if which not in ("wan1", "wan2"):
        raise RuntimeError("invalid wan")
    selected = c[f"{which}_interface"]
    other_key = "wan2" if which == "wan1" else "wan1"
    other = c[f"{other_key}_interface"]
    gw = gateway_for(selected)
    if not gw:
        raise RuntimeError(f"no gateway on {selected}")
    src = ipv4_for(selected)
    if not src:
        raise RuntimeError(f"no IPv4 on {selected}")

    run(["ip", "route", "replace", "default", "via", gw, "dev", selected, "src", src, "metric", "5"])
    ogw = gateway_for(other)
    osrc = ipv4_for(other)
    if ogw and osrc:
        run(["ip", "route", "replace", "default", "via", ogw, "dev", other, "src", osrc, "metric", "500"], check=False)
    return {"ok": True, "active": which}

def speedtest(interface, server_id, timeout):
    args = ["speedtest", "--accept-license", "--accept-gdpr", "--progress=no", "--format=json", "--interface", interface]
    if int(server_id or 0):
        args += ["--server-id", str(int(server_id))]
    raw = run(args, timeout=timeout)
    j = json.loads(raw)
    return {
        "ok": True,
        "download_mbps": round((j["download"]["bandwidth"] * 8) / 1_000_000, 2),
        "upload_mbps": round((j["upload"]["bandwidth"] * 8) / 1_000_000, 2),
        "ping_ms": j.get("ping", {}).get("latency"),
        "packet_loss": j.get("packetLoss"),
        "server": j.get("server", {}).get("name"),
        "server_id": j.get("server", {}).get("id"),
        "isp": j.get("isp"),
        "external_ip": j.get("interface", {}).get("externalIp"),
    }

def list_servers(interface, timeout=60):
    raw = run(["speedtest", "--accept-license", "--accept-gdpr", "--servers", "--interface", interface], timeout=timeout)
    servers = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or not line[0].isdigit():
            continue
        first = line.split()[0]
        try:
            sid = int(first)
        except Exception:
            continue
        servers.append({"id": sid, "label": line})
    return {"ok": True, "servers": servers}

def handle(conn):
    try:
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        req = json.loads(buf.decode().strip())
        action = req.get("action")
        if action == "interfaces":
            res = interfaces()
        elif action == "status":
            res = status()
        elif action == "apply_network":
            res = apply_network(req["config"])
        elif action == "switch":
            res = switch(req["wan"])
        elif action == "speedtest":
            res = speedtest(req["interface"], req.get("server_id", 0), int(req.get("timeout", 120)))
        elif action == "servers":
            res = list_servers(req["interface"], int(req.get("timeout", 60)))
        else:
            raise RuntimeError("invalid request")
    except Exception as e:
        res = {"ok": False, "error": str(e)}
    conn.sendall(json.dumps(res).encode())
    conn.close()

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.unlink(SOCK)
except FileNotFoundError:
    pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(SOCK)
os.chmod(SOCK, 0o666)
s.listen(20)

# Re-apply persisted router configuration after reboot.
try:
    persisted = load_persisted()
    if persisted:
        apply_network(persisted)
except Exception:
    pass

while True:
    c, _ = s.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
