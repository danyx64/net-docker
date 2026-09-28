/* global cockpit */
"use strict";
const $ = id => document.getElementById(id);

function run(args) {
  return cockpit.spawn(["/usr/local/sbin/net-routerctl", ...args], {
    superuser: "require",
    err: "message"
  }).then(out => JSON.parse(out));
}

function configFromForm() {
  return {
    wan1_interface: $("wan1").value.trim(),
    wan2_interface: $("wan2").value.trim(),
    lan_interface: $("lan").value.trim(),
    bridge_name: $("bridge").value.trim(),
    lan_cidr: $("lanCidr").value.trim(),
    dhcp_start: $("dhcpStart").value.trim(),
    dhcp_end: $("dhcpEnd").value.trim(),
    dhcp_lease: "12h",
    dns_servers: $("dns").value.split(",").map(x => x.trim()).filter(Boolean)
  };
}

function loadForm(n) {
  $("wan1").value = n.wan1_interface || "enp3s0";
  $("wan2").value = n.wan2_interface || "enx00e04c680270";
  $("lan").value = n.lan_interface || "enp4s0";
  $("bridge").value = n.bridge_name || "br0";
  $("lanCidr").value = n.lan_cidr || "192.168.100.1/24";
  $("dhcpStart").value = n.dhcp_start || "192.168.100.50";
  $("dhcpEnd").value = n.dhcp_end || "192.168.100.200";
  $("dns").value = (n.dns_servers || ["1.1.1.1","8.8.8.8"]).join(",");
}

function showState(s, first=false) {
  const active = s.active || null;
  $("activeBadge").textContent = "WAN attiva: " + (active ? active.toUpperCase() : "-");
  $("wan1Btn").classList.toggle("active", active === "wan1");
  $("wan2Btn").classList.toggle("active", active === "wan2");

  const w1 = s.wan1 || {};
  const w2 = s.wan2 || {};
  $("wan1Ip").textContent = "IP: " + (w1.ip || "-");
  $("wan1Gw").textContent = "Gateway: " + (w1.gateway || "-");
  $("wan2Ip").textContent = "IP: " + (w2.ip || "-");
  $("wan2Gw").textContent = "Gateway: " + (w2.gateway || "-");

  $("lanNet").textContent = s.lan_network || "192.168.100.0/24";
  $("route1").textContent =
    "Destinazione: " + (s.lan_network || "192.168.100.0/24") +
    "\nGateway: " + (w1.ip || "IP WAN1 non disponibile");
  $("route2").textContent =
    "Destinazione: " + (s.lan_network || "192.168.100.0/24") +
    "\nGateway: " + (w2.ip || "IP WAN2 non disponibile");

  $("status").textContent = JSON.stringify({
    active: s.active,
    routes: s.routes,
    wan1: s.wan1,
    wan2: s.wan2
  }, null, 2);

  if (first) loadForm(s.network || {});
}

async function refresh(first=false) {
  try {
    const s = await run(["status"]);
    showState(s, first);
  } catch (e) {
    $("status").textContent = String(e);
  }
}

async function selectWan(which) {
  $("switchMessage").textContent = "Cambio linea...";
  try {
    const s = await run(["switch", which]);
    $("switchMessage").innerHTML = '<span class="ok">Linea ' + which.toUpperCase() + ' attiva.</span>';
    showState(s);
  } catch (e) {
    $("switchMessage").innerHTML = '<span class="error">' + String(e) + '</span>';
  }
}

async function applyNetwork() {
  if (!confirm("Applicare la rete può interrompere temporaneamente la sessione. Continuare?")) return;
  $("applyMessage").textContent = "Applicazione...";
  try {
    const s = await run(["apply", JSON.stringify(configFromForm())]);
    $("applyMessage").innerHTML = '<span class="ok">Configurazione applicata.</span>';
    showState(s, true);
  } catch (e) {
    $("applyMessage").innerHTML = '<span class="error">' + String(e) + '</span>';
  }
}

$("wan1Btn").addEventListener("click", () => selectWan("wan1"));
$("wan2Btn").addEventListener("click", () => selectWan("wan2"));
$("applyBtn").addEventListener("click", applyNetwork);

refresh(true);
setInterval(() => refresh(false), 5000);
