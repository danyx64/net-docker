/* global cockpit */
"use strict";
const $=id=>document.getElementById(id);

function run(args){
  return cockpit.spawn(["/usr/local/sbin/net-routerctl",...args],{superuser:"require",err:"message"})
    .then(out=>{
      const data=JSON.parse(out);
      if(!data.ok) throw new Error(data.error||"Errore");
      return data;
    });
}

function esc(s){
  return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

function renderMappings(items){
  const body=$("mappingRows");
  if(!items || !items.length){
    body.innerHTML='<tr><td colspan="5" class="empty">Nessun mapping configurato.</td></tr>';
    return;
  }
  body.innerHTML=items.map(m=>`
    <tr>
      <td><b>${esc(m.label||"Server")}</b></td>
      <td><code>${esc(m.lan_ip)}</code></td>
      <td><code>${esc(m.wan1_ip)}</code></td>
      <td><code>${esc(m.wan2_ip)}</code></td>
      <td class="actions"><button class="danger delete-map" data-ip="${esc(m.lan_ip)}">Elimina</button></td>
    </tr>`).join("");
  document.querySelectorAll(".delete-map").forEach(btn=>btn.addEventListener("click",()=>removeMap(btn.dataset.ip)));
}

function render(s){
  $("badge").textContent="Internet: "+(s.selected==="wan1"?"MODEM 1":s.selected==="wan2"?"MODEM 2":"-");
  $("w1").classList.toggle("active",s.selected==="wan1");
  $("w2").classList.toggle("active",s.selected==="wan2");
  renderMappings(s.mappings);
  $("status").textContent=JSON.stringify(s,null,2);
}

async function refresh(){
  try{render(await run(["status"]))}
  catch(e){$("status").textContent=String(e)}
}

async function sw(w){
  $("w1").disabled=true;$("w2").disabled=true;$("msg").textContent="Cambio linea...";
  try{render(await run(["switch",w]));$("msg").innerHTML='<span class="ok">Linea Internet cambiata.</span>'}
  catch(e){$("msg").innerHTML='<span class="error">'+esc(e.message||e)+'</span>'}
  finally{$("w1").disabled=false;$("w2").disabled=false}
}

async function addMap(ev){
  ev.preventDefault();
  const args=["mapping-add",$("lanIp").value.trim(),$("wan1Ip").value.trim(),$("wan2Ip").value.trim(),$("label").value.trim()];
  $("msg").textContent="Creo mapping...";
  try{
    render(await run(args));
    $("addForm").reset();
    $("addForm").classList.add("hidden");
    $("msg").innerHTML='<span class="ok">Mapping creato e applicato.</span>';
  }catch(e){$("msg").innerHTML='<span class="error">'+esc(e.message||e)+'</span>'}
}

async function removeMap(ip){
  if(!window.confirm("Eliminare il mapping di "+ip+"?")) return;
  try{render(await run(["mapping-del",ip]));$("msg").innerHTML='<span class="ok">Mapping eliminato.</span>'}
  catch(e){$("msg").innerHTML='<span class="error">'+esc(e.message||e)+'</span>'}
}

function setTheme(dark){
  document.body.classList.toggle("dark",dark);
  document.body.classList.toggle("light",!dark);
  $("theme").textContent=dark?"☀ Modalità chiara":"☾ Modalità scura";
  localStorage.setItem("routerTheme",dark?"dark":"light");
}

$("w1").addEventListener("click",()=>sw("wan1"));
$("w2").addEventListener("click",()=>sw("wan2"));
$("showAdd").addEventListener("click",()=>$("addForm").classList.remove("hidden"));
$("cancelAdd").addEventListener("click",()=>$("addForm").classList.add("hidden"));
$("addForm").addEventListener("submit",addMap);
$("theme").addEventListener("click",()=>setTheme(!document.body.classList.contains("dark")));
setTheme(localStorage.getItem("routerTheme")!=="light");
refresh();
setInterval(refresh,5000);
