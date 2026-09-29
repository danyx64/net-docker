#!/usr/bin/env python3
import ipaddress
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

SOCK="/run/net-router/router.sock"
STATE=Path("/etc/net-router/wan-state.json")
MAPS=Path("/etc/net-router/mappings.json")

LAN="enp3s0"
LAN_NET=ipaddress.ip_network("192.168.100.0/24")
LAN_IP="192.168.100.1"

WAN1="enp4s0"
WAN1_NET=ipaddress.ip_network("192.168.1.0/24")
WAN1_IP="192.168.1.100"
GW1="192.168.1.1"

WAN2="enx00e04c680270"
WAN2_NET=ipaddress.ip_network("192.168.10.0/24")
WAN2_IP="192.168.10.100"
GW2="192.168.10.1"

NFT_TABLE="homelab_map"

def run(argv, check=True, input_text=None):
    p=subprocess.run(argv,text=True,input=input_text,capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "command failed").strip())
    return p.stdout.strip()

def iface_exists(name):
    return subprocess.run(["ip","link","show","dev",name],capture_output=True).returncode == 0

def load_maps():
    if not MAPS.exists():
        return []
    try:
        data=json.loads(MAPS.read_text())
        return data if isinstance(data,list) else []
    except Exception:
        return []

def save_maps(items):
    MAPS.parent.mkdir(parents=True,exist_ok=True)
    MAPS.write_text(json.dumps(items,indent=2)+"\n")

def validate_mapping(lan_ip, wan1_ip, wan2_ip, current_items=None):
    try:
        l=ipaddress.ip_address(lan_ip)
        a=ipaddress.ip_address(wan1_ip)
        b=ipaddress.ip_address(wan2_ip)
    except ValueError:
        raise RuntimeError("Uno degli IP non e valido")

    if l not in LAN_NET or l == ipaddress.ip_address(LAN_IP):
        raise RuntimeError("IP homelab deve essere 192.168.100.x e non 192.168.100.1")
    if a not in WAN1_NET or a in (ipaddress.ip_address(WAN1_IP),ipaddress.ip_address(GW1)):
        raise RuntimeError("IP Deco deve essere 192.168.1.x e non .1 o .100")
    if b not in WAN2_NET or b in (ipaddress.ip_address(WAN2_IP),ipaddress.ip_address(GW2)):
        raise RuntimeError("IP Vodafone deve essere 192.168.10.x e non .1 o .100")

    for m in current_items or []:
        if lan_ip==m["lan_ip"] or wan1_ip==m["wan1_ip"] or wan2_ip==m["wan2_ip"]:
            raise RuntimeError("IP gia usato da un altro mapping")

def ensure_ip_forward():
    run(["sysctl","-w","net.ipv4.ip_forward=1"],check=False)

def add_alias(dev, addr):
    run(["ip","address","replace",addr+"/32","dev",dev])

def del_alias(dev, addr):
    run(["ip","address","del",addr+"/32","dev",dev],check=False)

def rebuild_nft(items):
    run(["nft","delete","table","ip",NFT_TABLE],check=False)

    lines=[
        f"table ip {NFT_TABLE} {{",
        " chain prerouting {",
        "  type nat hook prerouting priority dstnat; policy accept;",
        " }",
        " chain postrouting {",
        "  type nat hook postrouting priority srcnat; policy accept;",
        " }",
        " chain forward {",
        "  type filter hook forward priority filter; policy accept;",
        " }",
        "}"
    ]
    run(["nft","-f","-"],input_text="\n".join(lines)+"\n")

    for m in items:
        # Full-server 1:1 mapping from each modem-side LAN to the real homelab IP.
        run(["nft","add","rule","ip",NFT_TABLE,"prerouting",
             "iifname",WAN1,"ip","daddr",m["wan1_ip"],"dnat","to",m["lan_ip"]])
        run(["nft","add","rule","ip",NFT_TABLE,"prerouting",
             "iifname",WAN2,"ip","daddr",m["wan2_ip"],"dnat","to",m["lan_ip"]])

        # Preserve the matching virtual source IP on replies/outbound traffic.
        run(["nft","add","rule","ip",NFT_TABLE,"postrouting",
             "oifname",WAN1,"ip","saddr",m["lan_ip"],"snat","to",m["wan1_ip"]])
        run(["nft","add","rule","ip",NFT_TABLE,"postrouting",
             "oifname",WAN2,"ip","saddr",m["lan_ip"],"snat","to",m["wan2_ip"]])

def apply_maps():
    ensure_ip_forward()
    items=load_maps()
    for m in items:
        add_alias(WAN1,m["wan1_ip"])
        add_alias(WAN2,m["wan2_ip"])
    rebuild_nft(items)

def add_mapping(lan_ip,wan1_ip,wan2_ip,label=""):
    items=load_maps()
    validate_mapping(lan_ip,wan1_ip,wan2_ip,items)
    item={
        "lan_ip":lan_ip,
        "wan1_ip":wan1_ip,
        "wan2_ip":wan2_ip,
        "label":str(label or "").strip()[:64]
    }
    items.append(item)
    save_maps(items)
    add_alias(WAN1,wan1_ip)
    add_alias(WAN2,wan2_ip)
    rebuild_nft(items)
    return status()

def delete_mapping(lan_ip):
    items=load_maps()
    target=next((m for m in items if m["lan_ip"]==lan_ip),None)
    if not target:
        raise RuntimeError("Mapping non trovato")
    items=[m for m in items if m["lan_ip"]!=lan_ip]
    save_maps(items)
    del_alias(WAN1,target["wan1_ip"])
    del_alias(WAN2,target["wan2_ip"])
    rebuild_nft(items)
    return status()

def current():
    routes=run(["ip","-4","route","show","default"],check=False)
    lines=[x.strip() for x in routes.splitlines() if x.strip()]
    # ip route normally prints the preferred metric first.
    if lines:
        first=lines[0]
        if ("via "+GW1) in first and ("dev "+WAN1) in first:
            return "wan1"
        if ("via "+GW2) in first and ("dev "+WAN2) in first:
            return "wan2"
    return None

def switch(which):
    if which not in ("wan1","wan2"):
        raise RuntimeError("WAN non valida")
    for i in (LAN,WAN1,WAN2):
        if not iface_exists(i):
            raise RuntimeError(f"Interfaccia mancante: {i}")

    if which=="wan1":
        gw,dev,other_gw,other_dev=GW1,WAN1,GW2,WAN2
    else:
        gw,dev,other_gw,other_dev=GW2,WAN2,GW1,WAN1

    run(["ip","route","replace","default","via",gw,"dev",dev,"metric","5"])
    run(["ip","route","replace","default","via",other_gw,"dev",other_dev,"metric","500"])

    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps({"selected":which})+"\n")
    return status()

def status():
    return {
        "ok":True,
        "selected":current(),
        "lan":{"interface":LAN,"ip":LAN_IP},
        "wan1":{"interface":WAN1,"ip":WAN1_IP,"gateway":GW1},
        "wan2":{"interface":WAN2,"ip":WAN2_IP,"gateway":GW2},
        "mappings":load_maps(),
        "default_routes":run(["ip","-4","route","show","default"],check=False),
        "addresses":run(["ip","-br","-4","addr"],check=False)
    }

def restore():
    ensure_ip_forward()
    try:
        apply_maps()
    except Exception:
        pass
    if STATE.exists():
        try:
            which=json.loads(STATE.read_text()).get("selected")
            if which in ("wan1","wan2"):
                switch(which)
        except Exception:
            pass

def handle(conn):
    try:
        buf=b""
        while not buf.endswith(b"\n"):
            chunk=conn.recv(65536)
            if not chunk: break
            buf+=chunk
        req=json.loads(buf.decode().strip())
        action=req.get("action")
        if action=="status":
            res=status()
        elif action=="switch":
            res=switch(req.get("wan"))
        elif action=="mapping_add":
            res=add_mapping(req.get("lan_ip",""),req.get("wan1_ip",""),req.get("wan2_ip",""),req.get("label",""))
        elif action=="mapping_delete":
            res=delete_mapping(req.get("lan_ip",""))
        elif action=="mapping_apply":
            apply_maps(); res=status()
        else:
            raise RuntimeError("Comando non valido")
    except Exception as e:
        res={"ok":False,"error":str(e)}
    conn.sendall(json.dumps(res).encode())
    conn.close()

os.makedirs("/run/net-router",exist_ok=True)
try: os.unlink(SOCK)
except FileNotFoundError: pass

restore()

s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.bind(SOCK)
os.chmod(SOCK,0o660)
s.listen(20)
while True:
    c,_=s.accept()
    threading.Thread(target=handle,args=(c,),daemon=True).start()
