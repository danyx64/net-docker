import asyncio
import json
import os
import socket
import time
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/config/config.yaml"))
STATE_PATH = Path(os.getenv("STATE_PATH", "/data/state.json"))
ROUTER_SOCKET = os.getenv("ROUTER_SOCKET", "/run/net-router/router.sock")

app = FastAPI(title="Net Router")

def load_config():
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f)

def load_state():
    if not STATE_PATH.exists():
        return {
            "mode": "auto",
            "active": None,
            "wan1": {},
            "wan2": {},
            "consecutive_failures": 0,
            "wan1_successes": 0,
            "last_error": None,
        }
    return json.loads(STATE_PATH.read_text())

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))

def routerctl(payload):
    data = (json.dumps(payload) + "\n").encode()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
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

async def run_speedtest(which, cfg):
    wan = cfg[which]
    req = {
        "action": "speedtest",
        "interface": wan["interface"],
        "server_id": int(wan.get("speedtest_server_id", 0)),
        "timeout": int(cfg["policy"].get("speedtest_timeout_seconds", 120)),
    }
    return await asyncio.to_thread(routerctl, req)

async def switch(which):
    return await asyncio.to_thread(routerctl, {"action": "switch", "wan": which})

async def evaluator():
    while True:
        try:
            cfg = load_config()
            state = load_state()
            state["mode"] = cfg["policy"].get("mode", state.get("mode", "auto"))

            for which in ("wan1", "wan2"):
                try:
                    r = await run_speedtest(which, cfg)
                    state[which] = {
                        "download_mbps": r["download_mbps"],
                        "upload_mbps": r["upload_mbps"],
                        "ping_ms": r.get("ping_ms"),
                        "server": r.get("server"),
                        "timestamp": int(time.time()),
                        "ok": True,
                    }
                except Exception as e:
                    state[which] = {"ok": False, "error": str(e), "timestamp": int(time.time())}

            if state["mode"] == "auto":
                current = routerctl({"action": "status"}).get("active")
                state["active"] = current
                w1 = state["wan1"]
                c1 = cfg["wan1"]
                good1 = (
                    w1.get("ok")
                    and w1.get("download_mbps", 0) >= float(c1["min_download_mbps"])
                    and w1.get("upload_mbps", 0) >= float(c1["min_upload_mbps"])
                )

                if current in (None, "wan1"):
                    if good1:
                        state["consecutive_failures"] = 0
                    else:
                        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
                        if state["consecutive_failures"] >= int(cfg["policy"]["failures_before_switch"]):
                            await switch("wan2")
                            state["active"] = "wan2"
                            state["consecutive_failures"] = 0
                elif current == "wan2" and cfg["policy"].get("return_to_wan1", True):
                    if good1:
                        state["wan1_successes"] = state.get("wan1_successes", 0) + 1
                        if state["wan1_successes"] >= int(cfg["policy"]["successes_before_return"]):
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

        await asyncio.sleep(int(load_config()["policy"].get("test_interval_seconds", 300)))

@app.on_event("startup")
async def startup():
    asyncio.create_task(evaluator())

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
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
    if mode in ("wan1", "wan2"):
        routerctl({"action": "switch", "wan": mode})
    return {"ok": True, "mode": mode}

@app.get("/", response_class=HTMLResponse)
def index():
    return """<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Net Router</title>
<style>
body{font-family:system-ui;background:#111827;color:#f9fafb;margin:0;padding:24px}.wrap{max-width:1000px;margin:auto}
h1{margin:0 0 20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.card{background:#1f2937;padding:20px;border-radius:16px;box-shadow:0 10px 30px #0004}.metric{font-size:30px;font-weight:700}
small{color:#9ca3af}button{border:0;border-radius:10px;padding:12px 16px;margin:5px;background:#374151;color:white;font-weight:700;cursor:pointer}
button:hover{background:#4b5563}.active{outline:2px solid #fff}.bad{color:#f87171}.good{color:#34d399}
</style></head>
<body><div class='wrap'><h1>Dual WAN Controller</h1>
<div class='card'><b>Modalità</b><div><button onclick="mode('auto')">AUTO</button><button onclick="mode('wan1')">WAN1</button><button onclick="mode('wan2')">WAN2</button></div><div id='active'></div></div>
<br><div class='grid'><div class='card'><h2>WAN1</h2><div id='w1'></div></div><div class='card'><h2>WAN2</h2><div id='w2'></div></div></div>
<br><div class='card'><h2>Stato</h2><pre id='raw'></pre></div></div>
<script>
async function mode(m){await fetch('/api/mode/'+m,{method:'POST'});await refresh()}
function wanHtml(w){if(!w||!w.ok)return "<div class='bad'>Test non disponibile</div><small>"+((w&&w.error)||"")+"</small>";
return "<div class='metric'>"+w.download_mbps.toFixed(1)+" Mbps ↓</div><div class='metric'>"+w.upload_mbps.toFixed(1)+" Mbps ↑</div><small>Ping "+(w.ping_ms??"-")+" ms · "+(w.server??"")+"</small>"}
async function refresh(){const s=await (await fetch('/api/state')).json();document.getElementById('w1').innerHTML=wanHtml(s.wan1);document.getElementById('w2').innerHTML=wanHtml(s.wan2);document.getElementById('active').innerHTML="<h2>Attiva: "+((s.router&&s.router.active)||s.active||"-")+"</h2><small>Mode: "+s.mode+"</small>";document.getElementById('raw').textContent=JSON.stringify(s,null,2)}
refresh();setInterval(refresh,10000)
</script></body></html>"""
