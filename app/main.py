import json
import os
import socket
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/config/config.yaml"))
ROUTER_SOCKET = os.getenv("ROUTER_SOCKET", "/run/net-router/router.sock")
WEB_PORT = int(os.getenv("WEB_PORT", "8787"))

app = FastAPI(title="Net Router Manual")

def load_config():
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f)

def save_config(cfg):
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))

def routerctl(payload, timeout=30):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(ROUTER_SOCKET)
    s.sendall((json.dumps(payload) + "\n").encode())
    out = b""
    while True:
        part = s.recv(65536)
        if not part:
            break
        out += part
    s.close()
    data = json.loads(out.decode())
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "helper error"))
    return data

@app.get("/api/config")
def get_config():
    return load_config()

@app.put("/api/config")
def put_config(cfg: dict):
    try:
        if "network" not in cfg:
            raise ValueError("missing network section")
        save_config(cfg)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/interfaces")
def api_interfaces():
    try:
        return routerctl({"action": "interfaces"})
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/state")
def api_state():
    try:
        return routerctl({"action": "status"})
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/network/apply")
def apply_network():
    try:
        cfg = load_config()
        return routerctl({"action": "apply_network_manual", "config": cfg["network"]})
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/wan/{which}")
def set_wan(which: str):
    if which not in ("wan1", "wan2"):
        raise HTTPException(400, "invalid wan")
    try:
        result = routerctl({"action": "switch", "wan": which})
        cfg = load_config()
        cfg.setdefault("manual", {})["selected_wan"] = which
        save_config(cfg)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/", response_class=HTMLResponse)
def index():
    return UI

UI = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Net Router</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0b1020;color:#eef2ff;font-family:system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:900px;margin:auto;padding:22px}.card{background:#151c31;border:1px solid #26304d;border-radius:16px;padding:18px;margin:14px 0}
h1,h2{margin-top:0}.buttons{display:grid;grid-template-columns:1fr 1fr;gap:12px}
button{border:0;border-radius:13px;padding:18px;font-size:18px;font-weight:800;color:white;background:#334155;cursor:pointer}
button.active{background:#16a34a}.apply{font-size:15px;padding:12px;background:#2563eb}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
label{display:block;color:#aab3c8;font-size:13px;margin:10px 0 5px}input,select{width:100%;background:#0e1528;color:white;border:1px solid #35405d;border-radius:9px;padding:10px}
.muted{color:#9ca3af}.ok{color:#34d399}.bad{color:#fb7185}pre{white-space:pre-wrap;word-break:break-word;font-size:12px}
@media(max-width:650px){.row,.buttons{grid-template-columns:1fr}}
</style>
</head>
<body><main>
<h1>Net Router</h1>
<div class="muted">Selezione manuale della linea Internet. Nessun failover automatico.</div>

<div class="card">
<h2>Linea Internet attiva</h2>
<div class="buttons">
<button id="b1" onclick="selectWan('wan1')">WAN1</button>
<button id="b2" onclick="selectWan('wan2')">WAN2</button>
</div>
<div id="switchStatus" class="muted" style="margin-top:10px"></div>
</div>

<div class="card">
<h2>Porte</h2>
<label>WAN1</label><select id="wan1"></select>
<label>WAN2</label><select id="wan2"></select>
<label>LAN verso switch</label><select id="lan"></select>
<div class="row">
<div><label>Bridge LAN</label><input id="bridge"></div>
<div><label>IP LAN</label><input id="cidr"></div>
</div>
<div class="row">
<div><label>DHCP da</label><input id="start"></div>
<div><label>DHCP a</label><input id="end"></div>
</div>
<button class="apply" onclick="saveApply()">SALVA E APPLICA RETE</button>
<div id="applyStatus" class="muted" style="margin-top:10px"></div>
</div>

<div class="card">
<h2>Nota per vedere tutti i dispositivi da entrambi i modem</h2>
<div class="muted">
Questa modalità usa routing puro tra LAN e i due modem, senza NAT sul traffico LAN.
Ogni modem deve avere una route statica verso la LAN tramite l'IP WAN del Node-2.
La UI mostra gli IP WAN da usare come gateway della route.
</div>
</div>

<div class="card"><h2>Stato</h2><pre id="state">Caricamento...</pre></div>
<script>
let cfg, names=[];
async function req(url,opt){const r=await fetch(url,opt);const t=await r.text();let j;try{j=JSON.parse(t)}catch{j={detail:t}}if(!r.ok)throw new Error(j.detail||j.error||t);return j}
function opts(id,val){const e=document.getElementById(id);e.innerHTML=names.map(n=>'<option>'+n+'</option>').join('');e.value=val}
async function load(){
 cfg=await req('/api/config');
 try{const x=await req('/api/interfaces');names=x.interfaces.map(i=>i.name)}catch(e){}
 opts('wan1',cfg.network.wan1_interface);opts('wan2',cfg.network.wan2_interface);opts('lan',cfg.network.lan_interface);
 bridge.value=cfg.network.bridge_name;cidr.value=cfg.network.lan_cidr;start.value=cfg.network.dhcp_start;end.value=cfg.network.dhcp_end;
 refresh();
}
async function selectWan(w){
 try{switchStatus.textContent='Cambio linea...';await req('/api/wan/'+w,{method:'POST'});switchStatus.innerHTML='<span class="ok">Attiva '+w.toUpperCase()+'</span>';refresh()}
 catch(e){switchStatus.innerHTML='<span class="bad">'+e.message+'</span>'}
}
async function saveApply(){
 cfg.network.wan1_interface=wan1.value;cfg.network.wan2_interface=wan2.value;cfg.network.lan_interface=lan.value;cfg.network.bridge_name=bridge.value.trim();cfg.network.lan_cidr=cidr.value.trim();cfg.network.dhcp_start=start.value.trim();cfg.network.dhcp_end=end.value.trim();
 try{
  await req('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
  applyStatus.textContent='Applico...';
  const r=await req('/api/network/apply',{method:'POST'});
  applyStatus.innerHTML='<span class="ok">Rete applicata</span>';
  state.textContent=JSON.stringify(r,null,2);
 }catch(e){applyStatus.innerHTML='<span class="bad">'+e.message+'</span>'}
}
async function refresh(){
 try{
  const s=await req('/api/state');
  b1.classList.toggle('active',s.active==='wan1');b2.classList.toggle('active',s.active==='wan2');
  state.textContent=JSON.stringify(s,null,2);
 }catch(e){state.textContent=e.message}
}
load();setInterval(refresh,5000);
</script>
</main></body></html>"""

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=WEB_PORT)
