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
SELECTED = Path("/etc/net-router/selected-wan")
RUNTIME_DIR = Path("/run/net-router")

def run(argv, timeout=180, check=True):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "command failed").strip())
    return p.stdout.strip()

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
    Path("/etc/dnsmasq.d/net-router.conf").write_text(
        f"interface={bridge}\n"
        "bind-dynamic\n"
        f"dhcp-range={c['dhcp_start']},{c['dhcp_end']},{net.network.netmask},{lease}\n"
        f"dhcp-option=3,{net.ip}\n"
        f"dhcp-option=6,{','.join(dns)}\n"
    )
    run(["systemctl", "enable", "dnsmasq"], check=False)
    run(["systemctl", "restart", "dnsmasq"])

def setup_forwarding_no_nat():
    subprocess.run(["nft", "delete", "table", "inet", "net_router"], capture_output=True, text=True)
    run(["nft", "add", "table", "inet", "net_router"])
    run(["nft", "add", "chain", "inet", "net_router", "forward", "{", "type", "filter", "hook", "forward", "priority", "0", ";", "policy", "accept", ";", "}"])
    Path("/etc/sysctl.d/99-net-router.conf").write_text("net.ipv4.ip_forward=1\n")
    run(["sysctl", "-w", "net.ipv4.ip_forward=1"])

def setup_nm(c, net):
    wan1, wan2, lan, bridge = c["wan1_interface"], c["wan2_interface"], c["lan_interface"], c["bridge_name"]
    for name in ("net-router-wan1", "net-router-wan2", "net-router-bridge", "net-router-lan-port"):
        nm_delete(name)

    run(["nmcli","connection","add","type","ethernet","ifname",wan1,"con-name","net-router-wan1",
         "ipv4.method","auto","ipv4.route-metric","100","ipv6.method","auto"])
    run(["nmcli","connection","add","type","ethernet","ifname",wan2,"con-name","net-router-wan2",
         "ipv4.method","auto","ipv4.route-metric","500","ipv6.method","auto"])
    run(["nmcli","connection","add","type","bridge","ifname",bridge,"con-name","net-router-bridge",
         "ipv4.method","manual","ipv4.addresses",str(net),"ipv4.never-default","yes","ipv6.method","disabled"])
    run(["nmcli","connection","add","type","ethernet","ifname",lan,"con-name","net-router-lan-port",
         "master",bridge,"slave-type","bridge"])

    for name in ("net-router-bridge","net-router-lan-port","net-router-wan1","net-router-wan2"):
        run(["nmcli","connection","up",name], check=False)

def apply_network_manual(c):
    net = validate_network(c)
    PERSIST.parent.mkdir(parents=True, exist_ok=True)
    PERSIST.write_text(json.dumps(c, indent=2))
    setup_nm(c, net)
    write_dnsmasq(c, net)
    setup_forwarding_no_nat()
    return status(c)

def load_persisted():
    if PERSIST.exists():
        return json.loads(PERSIST.read_text())
    return None

def active_wan(c):
    if not c:
        return None
    wan1, wan2 = c["wan1_interface"], c["wan2_interface"]
    out = run(["ip","-4","route","show","default"], check=False)
    best, best_metric = None, 2**31
    for line in out.splitlines():
        p = line.split()
        if "dev" not in p:
            continue
        dev = p[p.index("dev")+1]
        if dev not in (wan1, wan2):
            continue
        metric = int(p[p.index("metric")+1]) if "metric" in p else 0
        if metric < best_metric:
            best_metric = metric
            best = "wan1" if dev == wan1 else "wan2"
    return best

def status(c=None):
    c = c or load_persisted()
    data = {
        "ok": True,
        "active": active_wan(c),
        "routes": run(["ip","-4","route","show","default"], check=False),
        "network": c,
        "interfaces": interfaces()["interfaces"],
    }
    if c:
        data["wan1"] = {"interface": c["wan1_interface"], "ip": ipv4_for(c["wan1_interface"]), "gateway": gateway_for(c["wan1_interface"])}
        data["wan2"] = {"interface": c["wan2_interface"], "ip": ipv4_for(c["wan2_interface"]), "gateway": gateway_for(c["wan2_interface"])}
        data["lan_network"] = str(ipaddress.ip_interface(c["lan_cidr"]).network)
    return data

def switch(which, c=None):
    c = c or load_persisted()
    if not c:
        raise RuntimeError("network has not been applied yet")
    selected = c[f"{which}_interface"]
    other = c["wan2_interface"] if which == "wan1" else c["wan1_interface"]

    gw = gateway_for(selected)
    src = ipv4_for(selected)
    if not gw or not src:
        raise RuntimeError(f"{selected} has no usable IPv4/gateway")

    run(["ip","route","replace","default","via",gw,"dev",selected,"src",src,"metric","5"])
    SELECTED.parent.mkdir(parents=True, exist_ok=True)
    SELECTED.write_text(which + "\n")
    ogw, osrc = gateway_for(other), ipv4_for(other)
    if ogw and osrc:
        run(["ip","route","replace","default","via",ogw,"dev",other,"src",osrc,"metric","500"], check=False)
    return status(c)

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
        elif action == "apply_network_manual":
            res = apply_network_manual(req["config"])
        elif action == "switch" and req.get("wan") in ("wan1","wan2"):
            res = switch(req["wan"])
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

try:
    persisted = load_persisted()
    if persisted:
        apply_network_manual(persisted)
        wanted = SELECTED.read_text().strip() if SELECTED.exists() else "wan1"
        if wanted in ("wan1", "wan2"):
            switch(wanted, persisted)
except Exception:
    pass

while True:
    c, _ = s.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
