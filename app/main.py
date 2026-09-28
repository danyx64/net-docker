import asyncio
import json
import os
import socket
import time
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/config/config.yaml"))
STATE_PATH = Path(os.getenv("STATE_PATH", "/data/state.json"))
ROUTER_SOCKET = os.getenv("ROUTER_SOCKET", "/run/net-router/router.sock")
WEB_PORT = int(os.getenv("WEB_PORT", "8787"))

app = FastAPI(title="Net Router")

DEFAULT_STATE = {
    "mode": "auto",
    "active": None,
    "wan1": {},
    "wan2": {},
    "history": [],
    "consecutive_failures": 0,
    "wan1_successes": 0,
    "last_error": None,
}

def load_config():
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f)

def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))

def load_state():
    if not STATE_PATH.exists():
        return dict(DEFAULT_STATE)
    try:
        s = json.loads(STATE_PATH.read_text())
        for k, v in DEFAULT_STATE.items():
            s.setdefault(k, v if not isinstance(v, list) else [])
        return s
    except Exception:
        return dict(DEFAULT_STATE)

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["history"] = state.get("history", [])[-100:]
    STATE_PATH.write_text(json.dumps(state, indent=2))

def routerctl(payload, timeout=190):
    data = (json.dumps(payload) + "\n").encode()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(ROUTER_SOCKET)
    s.sendall(data)
    out = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        out += chunk
    s.close()
    result = json.loads(out.decode())
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "router helper error"))
    return result

def wan_interface(cfg, which):
    return cfg["network"][f"{which}_interface"]

async def run_speedtest(which, cfg):
    wan = cfg[which]
    req = {
        "action": "speedtest",
        "interface": wan_interface(cfg, which),
        "server_id": int(wan.get("speedtest_server_id", 0)),
        "timeout": int(cfg["policy"].get("speedtest_timeout_seconds", 120)),
    }
    return await asyncio.to_thread(routerctl, req, req["timeout"] + 20)

async def switch(which):
    return await asyncio.to_thread(routerctl, {"action": "switch", "wan": which})

def is_good(result, wan_cfg):
    return (
        result.get("ok")
        and float(result.get("download_mbps", 0)) >= float(wan_cfg.get("min_download_mbps", 0))
        and float(result.get("upload_mbps", 0)) >= float(wan_cfg.get("min_upload_mbps", 0))
    )

async def test_and_store(which, cfg, state):
    try:
        r = await run_speedtest(which, cfg)
        r["timestamp"] = int(time.time())
        state[which] = r
        state["history"].append({"wan": which, **r})
        return r
    except Exception as e:
        r = {"ok": False, "error": str(e), "timestamp": int(time.time())}
        state[which] = r
        state["history"].append({"wan": which, **r})
        return r

async def evaluator():
    await asyncio.sleep(8)
    while True:
        interval = 300
        try:
            cfg = load_config()
            interval = max(60, int(cfg["policy"].get("test_interval_seconds", 300)))
            state = load_state()
            state["mode"] = cfg["policy"].get("mode", "auto")

            # Test both links independently using Ookla interface binding.
            await test_and_store("wan1", cfg, state)
            await test_and_store("wan2", cfg, state)

            if state["mode"] == "auto":
                current = routerctl({"action": "status"}).get("active")
                state["active"] = current
                good1 = is_good(state["wan1"], cfg["wan1"])
                good2 = is_good(state["wan2"], cfg["wan2"])

                if current in (None, "wan1"):
                    if good1:
                        state["consecutive_failures"] = 0
                    else:
                        state["consecutive_failures"] += 1
                        if (
                            state["consecutive_failures"] >= int(cfg["policy"].get("failures_before_switch", 2))
                            and good2
                        ):
                            await switch("wan2")
                            state["active"] = "wan2"
                            state["consecutive_failures"] = 0
                            state["wan1_successes"] = 0
                elif current == "wan2" and cfg["policy"].get("return_to_wan1", True):
                    if good1:
                        state["wan1_successes"] += 1
                        if state["wan1_successes"] >= int(cfg["policy"].get("successes_before_return", 3)):
                            await switch("wan1")
                            state["active"] = "wan1"
                            state["wan1_successes"] = 0
                    else:
                        state["wan1_successes"] = 0

            state["last_error"] = None
            save_state(state)
        except Exception as e:
            state = load_state()
            state["last_error"] = str(e)
            save_state(state)
        await asyncio.sleep(interval)

@app.on_event("startup")
async def startup():
    asyncio.create_task(evaluator())

@app.get("/api/config")
def get_config():
    return load_config()

@app.put("/api/config")
def put_config(cfg: dict):
    try:
        # Minimal validation; host helper performs strict network validation.
        for section in ("network", "wan1", "wan2", "policy"):
            if section not in cfg:
                raise ValueError(f"missing section {section}")
        mode = cfg["policy"].get("mode", "auto")
        if mode not in ("auto", "wan1", "wan2"):
            raise ValueError("invalid mode")
        cfg["policy"]["test_interval_seconds"] = max(60, int(cfg["policy"].get("test_interval_seconds", 300)))
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

@app.post("/api/network/apply")
def api_apply_network():
    try:
        cfg = load_config()
        return routerctl({"action": "apply_network", "config": cfg["network"]})
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/servers/{which}")
def api_servers(which: str):
    if which not in ("wan1", "wan2"):
        raise HTTPException(400, "invalid WAN")
    try:
        cfg = load_config()
        return routerctl({
            "action": "servers",
            "interface": wan_interface(cfg, which),
            "timeout": 60,
        }, 80)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/test/{which}")
async def api_test(which: str):
    if which not in ("wan1", "wan2"):
        raise HTTPException(400, "invalid WAN")
    cfg = load_config()
    state = load_state()
    r = await test_and_store(which, cfg, state)
    save_state(state)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "test failed"))
    return r

@app.get("/api/state")
def api_state():
    state = load_state()
    try:
        state["router"] = routerctl({"action": "status"})
    except Exception as e:
        state["router"] = {"error": str(e)}
    return state

@app.post("/api/mode/{mode}")
def set_mode(mode: str):
    if mode not in ("auto", "wan1", "wan2"):
        raise HTTPException(400, "invalid mode")
    cfg = load_config()
    cfg["policy"]["mode"] = mode
    save_config(cfg)
    if mode in ("wan1", "wan2"):
        routerctl({"action": "switch", "wan": mode})
    return {"ok": True, "mode": mode}

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
main{max-width:1120px;margin:auto;padding:22px}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
h1{margin:0;font-size:28px}h2{font-size:18px;margin:0 0 14px}.muted{color:#9ca3af}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:14px;margin-top:14px}
.card{background:#151c31;border:1px solid #26304d;border-radius:15px;padding:17px}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.field{margin:9px 0}
label{display:block;color:#aab3c8;font-size:13px;margin-bottom:5px}input,select{width:100%;background:#0e1528;color:#fff;border:1px solid #35405d;border-radius:9px;padding:10px}
button{background:#334155;color:#fff;border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;margin:3px}button.primary{background:#2563eb}button.warn{background:#b45309}
.metric{font-size:27px;font-weight:800;margin:4px 0}.ok{color:#34d399}.bad{color:#fb7185}.pill{display:inline-block;padding:5px 9px;border-radius:999px;background:#26304d}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px}.status{min-height:22px;margin-top:8px}.sep{height:1px;background:#26304d;margin:14px 0}
@media(max-width:620px){.row{grid-template-columns:1fr}}
</style>
</head>
<body><main>
<div class="top"><div><h1>Net Router</h1><div class="muted">Dual WAN + Speedtest + failover</div></div><div><span class="pill" id="active">WAN: -</span></div></div>

<div class="grid">
<section class="card"><h2>Modalità</h2>
<button onclick="setMode('auto')">AUTO</button><button onclick="setMode('wan1')">WAN1</button><button onclick="setMode('wan2')">WAN2</button>
<div id="modeStatus" class="status muted"></div>
<div class="sep"></div>
<div class="row"><div><b>WAN1</b><div id="w1metric" class="metric">-</div><div id="w1sub" class="muted"></div></div>
<div><b>WAN2</b><div id="w2metric" class="metric">-</div><div id="w2sub" class="muted"></div></div></div>
<button onclick="testWan('wan1')">Test WAN1 ora</button><button onclick="testWan('wan2')">Test WAN2 ora</button>
</section>

<section class="card"><h2>Porte fisiche e bridge LAN</h2>
<div class="field"><label>WAN1</label><select id="wan1_interface"></select></div>
<div class="field"><label>WAN2</label><select id="wan2_interface"></select></div>
<div class="field"><label>LAN verso switch</label><select id="lan_interface"></select></div>
<div class="row"><div class="field"><label>Bridge</label><input id="bridge_name"></div><div class="field"><label>IP LAN/CIDR</label><input id="lan_cidr"></div></div>
<div class="row"><div class="field"><label>DHCP da</label><input id="dhcp_start"></div><div class="field"><label>DHCP a</label><input id="dhcp_end"></div></div>
<div class="row"><div class="field"><label>Lease DHCP</label><input id="dhcp_lease"></div><div class="field"><label>DNS (virgola)</label><input id="dns_servers"></div></div>
<div class="muted">La LAN viene creata come bridge Linux (es. br0) con la porta scelta come membro. WAN1 e WAN2 restano separate.</div>
<button class="warn" onclick="saveAndApply()">SALVA + APPLICA RETE</button>
<div id="netStatus" class="status"></div>
</section>

<section class="card"><h2>WAN1 Speedtest</h2>
<div class="field"><label>Server ID Ookla (0 = automatico)</label><input type="number" id="w1_server"></div>
<div class="row"><div class="field"><label>Download minimo Mbps</label><input type="number" step="0.1" id="w1_down"></div>
<div class="field"><label>Upload minimo Mbps</label><input type="number" step="0.1" id="w1_up"></div></div>
<button onclick="loadServers('wan1')">Mostra server vicini</button>
<div id="servers1" class="muted"></div>
</section>

<section class="card"><h2>WAN2 Speedtest</h2>
<div class="field"><label>Server ID Ookla (0 = automatico)</label><input type="number" id="w2_server"></div>
<div class="row"><div class="field"><label>Download minimo Mbps</label><input type="number" step="0.1" id="w2_down"></div>
<div class="field"><label>Upload minimo Mbps</label><input type="number" step="0.1" id="w2_up"></div></div>
<button onclick="loadServers('wan2')">Mostra server vicini</button>
<div id="servers2" class="muted"></div>
</section>

<section class="card"><h2>Failover</h2>
<div class="row"><div class="field"><label>Test ogni (secondi, minimo 60)</label><input type="number" id="interval"></div>
<div class="field"><label>Timeout Speedtest</label><input type="number" id="timeout"></div></div>
<div class="row"><div class="field"><label>Test KO prima di passare a WAN2</label><input type="number" id="failures"></div>
<div class="field"><label>Test OK prima di tornare a WAN1</label><input type="number" id="successes"></div></div>
<div class="field"><label><input style="width:auto" type="checkbox" id="return_wan1"> Torna automaticamente a WAN1 quando torna sopra soglia</label></div>
<button class="primary" onclick="saveConfig()">SALVA CONFIGURAZIONE</button>
<div id="cfgStatus" class="status"></div>
</section>

<section class="card"><h2>Stato host</h2><pre id="raw">Caricamento...</pre></section>
</div>
</main>
<script>
let cfg=null, ifaceNames=[];
async function jfetch(url,opt){const r=await fetch(url,opt);const t=await r.text();let j;try{j=JSON.parse(t)}catch{j={detail:t}}if(!r.ok)throw new Error(j.detail||j.error||t);return j}
function setOptions(id,value){const e=document.getElementById(id);e.innerHTML=ifaceNames.map(n=>'<option value="'+n+'">'+n+'</option>').join('');if(value)e.value=value}
async function load(){
  cfg=await jfetch('/api/config');
  try{const x=await jfetch('/api/interfaces');ifaceNames=x.interfaces.map(i=>i.name)}catch(e){ifaceNames=[]}
  setOptions('wan1_interface',cfg.network.wan1_interface);setOptions('wan2_interface',cfg.network.wan2_interface);setOptions('lan_interface',cfg.network.lan_interface);
  bridge_name.value=cfg.network.bridge_name;lan_cidr.value=cfg.network.lan_cidr;dhcp_start.value=cfg.network.dhcp_start;dhcp_end.value=cfg.network.dhcp_end;dhcp_lease.value=cfg.network.dhcp_lease||'12h';dns_servers.value=(cfg.network.dns_servers||[]).join(',');
  w1_server.value=cfg.wan1.speedtest_server_id||0;w1_down.value=cfg.wan1.min_download_mbps;w1_up.value=cfg.wan1.min_upload_mbps;
  w2_server.value=cfg.wan2.speedtest_server_id||0;w2_down.value=cfg.wan2.min_download_mbps;w2_up.value=cfg.wan2.min_upload_mbps;
  interval.value=cfg.policy.test_interval_seconds;timeout.value=cfg.policy.speedtest_timeout_seconds;failures.value=cfg.policy.failures_before_switch;successes.value=cfg.policy.successes_before_return;return_wan1.checked=!!cfg.policy.return_to_wan1;
  refresh();
}
function collect(){
  cfg.network={wan1_interface:wan1_interface.value,wan2_interface:wan2_interface.value,lan_interface:lan_interface.value,bridge_name:bridge_name.value.trim(),lan_cidr:lan_cidr.value.trim(),dhcp_start:dhcp_start.value.trim(),dhcp_end:dhcp_end.value.trim(),dhcp_lease:dhcp_lease.value.trim(),dns_servers:dns_servers.value.split(',').map(x=>x.trim()).filter(Boolean)};
  cfg.wan1.speedtest_server_id=+w1_server.value;cfg.wan1.min_download_mbps=+w1_down.value;cfg.wan1.min_upload_mbps=+w1_up.value;
  cfg.wan2.speedtest_server_id=+w2_server.value;cfg.wan2.min_download_mbps=+w2_down.value;cfg.wan2.min_upload_mbps=+w2_up.value;
  cfg.policy.test_interval_seconds=+interval.value;cfg.policy.speedtest_timeout_seconds=+timeout.value;cfg.policy.failures_before_switch=+failures.value;cfg.policy.successes_before_return=+successes.value;cfg.policy.return_to_wan1=return_wan1.checked;
  return cfg;
}
async function saveConfig(){
  try{await jfetch('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});cfgStatus.innerHTML='<span class="ok">Salvato</span>'}catch(e){cfgStatus.innerHTML='<span class="bad">'+e.message+'</span>'}
}
async function saveAndApply(){
  if(!confirm('Applicare la rete può interrompere temporaneamente SSH/UI. Continuare?'))return;
  try{await saveConfig();netStatus.textContent='Applicazione rete...';const r=await jfetch('/api/network/apply',{method:'POST'});netStatus.innerHTML='<span class="ok">Rete applicata</span>';raw.textContent=JSON.stringify(r,null,2);setTimeout(refresh,2500)}catch(e){netStatus.innerHTML='<span class="bad">'+e.message+'</span>'}
}
async function setMode(m){try{await jfetch('/api/mode/'+m,{method:'POST'});cfg.policy.mode=m;modeStatus.textContent='Modalità '+m.toUpperCase();refresh()}catch(e){modeStatus.innerHTML='<span class="bad">'+e.message+'</span>'}}
async function testWan(w){const id=w==='wan1'?'w1sub':'w2sub';document.getElementById(id).textContent='Speedtest in corso...';try{await jfetch('/api/test/'+w,{method:'POST'});refresh()}catch(e){document.getElementById(id).innerHTML='<span class="bad">'+e.message+'</span>'}}
async function loadServers(w){const out=document.getElementById(w==='wan1'?'servers1':'servers2');out.textContent='Ricerca...';try{const r=await jfetch('/api/servers/'+w);out.innerHTML=r.servers.slice(0,12).map(s=>'<div><button onclick="pickServer(\''+w+'\','+s.id+')">'+s.id+'</button> '+s.label+'</div>').join('')||'Nessun server trovato'}catch(e){out.innerHTML='<span class="bad">'+e.message+'</span>'}}
function pickServer(w,id){document.getElementById(w==='wan1'?'w1_server':'w2_server').value=id}
function metric(w,prefix){const m=document.getElementById(prefix+'metric'),s=document.getElementById(prefix+'sub');if(!w||!w.ok){m.innerHTML='<span class="bad">KO</span>';s.textContent=(w&&w.error)||'Nessun test';return}m.innerHTML=w.download_mbps.toFixed(1)+'↓ / '+w.upload_mbps.toFixed(1)+'↑';s.textContent='Mbps · '+(w.ping_ms??'-')+' ms · '+(w.server||'')+' #'+(w.server_id||'')}
async function refresh(){try{const s=await jfetch('/api/state');metric(s.wan1,'w1');metric(s.wan2,'w2');const a=(s.router&&s.router.active)||s.active||'-';active.textContent='WAN: '+a.toUpperCase();modeStatus.textContent='Modalità '+(s.mode||cfg.policy.mode).toUpperCase();raw.textContent=JSON.stringify({last_error:s.last_error,routes:s.router&&s.router.routes,interfaces:s.router&&s.router.interfaces},null,2)}catch(e){raw.textContent=e.message}}
load();setInterval(refresh,10000);
</script>
</body></html>"""

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=WEB_PORT, log_level="info")
