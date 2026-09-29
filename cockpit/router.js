/* global cockpit */
"use strict";
const $=id=>document.getElementById(id);
function run(args){
  return cockpit.spawn(["/usr/local/sbin/net-routerctl",...args],{superuser:"require",err:"message"})
    .then(out=>JSON.parse(out));
}
function render(s){
  $("badge").textContent="Internet: "+(s.selected==="wan1"?"MODEM 1":s.selected==="wan2"?"MODEM 2":"-");
  $("w1").classList.toggle("active",s.selected==="wan1");
  $("w2").classList.toggle("active",s.selected==="wan2");
  $("status").textContent=JSON.stringify(s,null,2);
}
async function refresh(){
  try{render(await run(["status"]))}
  catch(e){$("status").textContent=String(e)}
}
async function sw(w){
  $("w1").disabled=true;$("w2").disabled=true;
  $("msg").textContent="Cambio linea...";
  try{
    const s=await run(["switch",w]); render(s);
    $("msg").innerHTML='<span class="ok">Linea Internet cambiata.</span>';
  }catch(e){
    $("msg").innerHTML='<span class="error">'+String(e)+'</span>';
  }finally{
    $("w1").disabled=false;$("w2").disabled=false;
  }
}
$("w1").addEventListener("click",()=>sw("wan1"));
$("w2").addEventListener("click",()=>sw("wan2"));
refresh(); setInterval(refresh,5000);
