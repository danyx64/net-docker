#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

SOCK="/run/net-router/router.sock"
STATE=Path("/etc/net-router/wan-state.json")

LAN="enp3s0"
LAN_IP="192.168.100.1"
WAN1="enp4s0"
WAN1_IP="192.168.1.100"
GW1="192.168.1.1"
WAN2="enx00e04c680270"
WAN2_IP="192.168.10.100"
GW2="192.168.10.1"

def run(argv, check=True):
    p=subprocess.run(argv,text=True,capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "command failed").strip())
    return p.stdout.strip()

def iface_exists(name):
    return subprocess.run(["ip","link","show","dev",name],capture_output=True).returncode == 0

def current():
    routes=run(["ip","-4","route","show","default"],check=False)
    lines=[x.strip() for x in routes.splitlines() if x.strip()]
    if any(("via "+GW1) in x and ("dev "+WAN1) in x for x in lines):
        first=lines[0] if lines else ""
        if ("via "+GW1) in first and ("dev "+WAN1) in first:
            return "wan1"
    if any(("via "+GW2) in x and ("dev "+WAN2) in x for x in lines):
        first=lines[0] if lines else ""
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

    # Mantiene entrambe le WAN connesse ma rende una sola default preferita.
    run(["ip","route","replace","default","via",gw,"dev",dev,"metric","5"])
    run(["ip","route","replace","default","via",other_gw,"dev",other_dev,"metric","500"])

    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps({"selected":which}))
    return status()

def status():
    return {
        "ok": True,
        "selected": current(),
        "lan": {"interface":LAN,"ip":LAN_IP},
        "wan1": {"interface":WAN1,"ip":WAN1_IP,"gateway":GW1},
        "wan2": {"interface":WAN2,"ip":WAN2_IP,"gateway":GW2},
        "default_routes": run(["ip","-4","route","show","default"],check=False),
        "addresses": run(["ip","-br","-4","addr"],check=False)
    }

def restore():
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
