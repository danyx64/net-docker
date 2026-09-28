#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

SOCK="/run/net-router/router.sock"
STATE=Path("/etc/net-router/bridge-state.json")
BRIDGE="br0"
WAN1="enp3s0"
WAN2="enx00e04c680270"
LAN="enp4s0"

def run(argv, check=True):
    p=subprocess.run(argv,text=True,capture_output=True)
    if check and p.returncode!=0:
        raise RuntimeError((p.stderr or p.stdout or "command failed").strip())
    return p.stdout.strip()

def nm_delete(name):
    subprocess.run(["nmcli","connection","delete",name],text=True,capture_output=True)

def ensure_profiles():
    for name in ("net-bridge","net-lan","net-wan1","net-wan2"):
        nm_delete(name)

    run(["nmcli","connection","add","type","bridge","ifname",BRIDGE,"con-name","net-bridge",
         "ipv4.method","disabled","ipv6.method","disabled"])
    run(["nmcli","connection","modify","net-bridge","connection.autoconnect","yes"])

    run(["nmcli","connection","add","type","ethernet","ifname",LAN,"con-name","net-lan",
         "master",BRIDGE,"slave-type","bridge"])
    run(["nmcli","connection","modify","net-lan","connection.autoconnect","yes"])

    run(["nmcli","connection","add","type","ethernet","ifname",WAN1,"con-name","net-wan1",
         "master",BRIDGE,"slave-type","bridge"])
    run(["nmcli","connection","modify","net-wan1","connection.autoconnect","no"])

    run(["nmcli","connection","add","type","ethernet","ifname",WAN2,"con-name","net-wan2",
         "master",BRIDGE,"slave-type","bridge"])
    run(["nmcli","connection","modify","net-wan2","connection.autoconnect","no"])

    run(["nmcli","connection","up","net-bridge"],check=False)
    run(["nmcli","connection","up","net-lan"],check=False)

def current_bridge_wan():
    out=run(["bridge","link"],check=False)
    if f"{WAN1}:" in out and "master br0" in out:
        return "wan1"
    if f"{WAN2}:" in out and "master br0" in out:
        return "wan2"
    return None

def switch(which):
    if which not in ("wan1","wan2"):
        raise RuntimeError("invalid wan")

    run(["nmcli","connection","down","net-wan1"],check=False)
    run(["nmcli","connection","down","net-wan2"],check=False)

    # Clear stale bridge membership before activating the selected side.
    run(["ip","link","set",WAN1,"nomaster"],check=False)
    run(["ip","link","set",WAN2,"nomaster"],check=False)

    target="net-wan1" if which=="wan1" else "net-wan2"
    run(["nmcli","connection","up",target])

    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps({"selected":which}))
    return status()

def iface_state(name):
    ip=run(["ip","-4","-o","addr","show","dev",name],check=False)
    link=run(["ip","-j","link","show","dev",name],check=False)
    return {"name":name,"ipv4":ip,"link":json.loads(link)[0] if link else {}}

def status():
    selected=current_bridge_wan()
    default_route=run(["ip","-4","route","show","default"],check=False)
    return {
        "ok":True,
        "selected":selected,
        "bridge":BRIDGE,
        "lan":LAN,
        "wan1":WAN1,
        "wan2":WAN2,
        "bridge_links":run(["bridge","link"],check=False),
        "default_route":default_route,
        "management_note":"Node-2 management/default Internet should stay on a separate interface such as wlp2s0."
    }

def restore():
    ensure_profiles()
    selected="wan1"
    if STATE.exists():
        try:
            selected=json.loads(STATE.read_text()).get("selected","wan1")
        except Exception:
            pass
    switch(selected)

def handle(conn):
    try:
        buf=b""
        while not buf.endswith(b"\n"):
            chunk=conn.recv(65536)
            if not chunk:
                break
            buf+=chunk
        req=json.loads(buf.decode().strip())
        action=req.get("action")
        if action=="status":
            res=status()
        elif action=="switch":
            res=switch(req.get("wan"))
        elif action=="rebuild":
            restore(); res=status()
        else:
            raise RuntimeError("invalid action")
    except Exception as e:
        res={"ok":False,"error":str(e)}
    conn.sendall(json.dumps(res).encode())
    conn.close()

os.makedirs("/run/net-router",exist_ok=True)
try: os.unlink(SOCK)
except FileNotFoundError: pass

try:
    restore()
except Exception:
    pass

s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.bind(SOCK)
os.chmod(SOCK,0o666)
s.listen(20)
while True:
    c,_=s.accept()
    threading.Thread(target=handle,args=(c,),daemon=True).start()
