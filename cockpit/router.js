/* global cockpit */
"use strict";
const $ = id => document.getElementById(id);

function run(args) {
  return cockpit.spawn(["/usr/local/sbin/net-routerctl", ...args], {
    superuser: "require",
    err: "message"
  }).then(out => JSON.parse(out));
}

function render(s) {
  const active = s.active || null;
  $("activeBadge").textContent = "Attiva: " + (active ? active.toUpperCase() : "-");

  $("wan1Btn").classList.toggle("active", active === "wan1");
  $("wan2Btn").classList.toggle("active", active === "wan2");

  const w1 = s.wan1 || {};
  const w2 = s.wan2 || {};

  $("wan1Btn").classList.toggle("online", !!w1.ip && !!w1.gateway);
  $("wan2Btn").classList.toggle("online", !!w2.ip && !!w2.gateway);

  $("wan1Ip").textContent = w1.ip || "-";
  $("wan1Gw").textContent = w1.gateway || "-";
  $("wan2Ip").textContent = w2.ip || "-";
  $("wan2Gw").textContent = w2.gateway || "-";

  $("lanNetwork").textContent = s.lan_network || "192.168.100.0/24";

  const net = s.lan_network || "192.168.100.0/24";
  $("route1").textContent = "Destinazione: " + net + "\nGateway: " + (w1.ip || "IP WAN1 non disponibile");
  $("route2").textContent = "Destinazione: " + net + "\nGateway: " + (w2.ip || "IP WAN2 non disponibile");

  $("status").textContent = JSON.stringify({
    active: s.active,
    routes: s.routes,
    wan1: s.wan1,
    wan2: s.wan2
  }, null, 2);
}

async function refresh() {
  try {
    render(await run(["status"]));
  } catch (e) {
    $("status").textContent = String(e);
  }
}

async function selectWan(which) {
  $("switchMessage").textContent = "Cambio linea...";
  $("wan1Btn").disabled = true;
  $("wan2Btn").disabled = true;
  try {
    const result = await run(["switch", which]);
    render(result);
    $("switchMessage").innerHTML = '<span class="ok">' + which.toUpperCase() + ' impostata come route Internet.</span>';
  } catch (e) {
    $("switchMessage").innerHTML = '<span class="error">' + String(e) + '</span>';
  } finally {
    $("wan1Btn").disabled = false;
    $("wan2Btn").disabled = false;
  }
}

$("wan1Btn").addEventListener("click", () => selectWan("wan1"));
$("wan2Btn").addEventListener("click", () => selectWan("wan2"));

refresh();
setInterval(refresh, 5000);
