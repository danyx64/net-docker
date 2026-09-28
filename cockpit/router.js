/* global cockpit */
"use strict";

const $ = id => document.getElementById(id);

function run(args) {
  return cockpit.spawn(["/usr/local/sbin/net-routerctl", ...args], {
    superuser: "require",
    err: "message"
  }).then(out => JSON.parse(out));
}

function fillSelect(id, interfaces, selected) {
  const el = $(id);
  el.innerHTML = "";
  for (const i of interfaces) {
    const o = document.createElement("option");
    o.value = i.name;
    o.textContent = i.name + (i.ipv4 ? " - " + i.ipv4 : "");
    el.appendChild(o);
  }
  if (selected) el.value = selected;
}

function configFromForm() {
  return {
    wan1_interface: $("wan1").value,
    wan2_interface: $("wan2").value,
    lan_interface: $("lan").value,
    bridge_name: $("bridge").value.trim(),
    lan_cidr: $("lanCidr").value.trim(),
    dhcp_start: $("dhcpStart").value.trim(),
    dhcp_end: $("dhcpEnd").value.trim(),
    dns_servers: $("dns").value.split(",").map(x => x.trim()).filter(Boolean)
  };
}

function showRoutes(s) {
  if (!s.network) {
    $("route1").textContent = "-";
    $("route2").textContent = "-";
    return;
  }
  const net = s.lan_network || "LAN";
  const w1 = s.wan1 || {};
  const w2 = s.wan2 || {};
  $("route1").textContent =
    "Destinazione: " + net + "\nGateway: " + (w1.ip || "IP WAN1 non disponibile");
  $("route2").textContent =
    "Destinazione: " + net + "\nGateway: " + (w2.ip || "IP WAN2 non disponibile");
}

async function refresh(firstLoad=false) {
  try {
    const s = await run(["status"]);
    $("activeBadge").textContent = "WAN attiva: " + (s.active ? s.active.toUpperCase() : "-");
    $("wan1Btn").classList.toggle("active", s.active === "wan1");
    $("wan2Btn").classList.toggle("active", s.active === "wan2");
    $("status").textContent = JSON.stringify(s, null, 2);
    showRoutes(s);

    if (firstLoad) {
      const ifs = await run(["interfaces"]);
      const n = s.network || {
        wan1_interface: "enp3s0",
        wan2_interface: "enx00e04c680270",
        lan_interface: "enp4s0",
        bridge_name: "br0",
        lan_cidr: "192.168.100.1/24",
        dhcp_start: "192.168.100.50",
        dhcp_end: "192.168.100.200",
        dns_servers: ["1.1.1.1","8.8.8.8"]
      };
      fillSelect("wan1", ifs.interfaces, n.wan1_interface);
      fillSelect("wan2", ifs.interfaces, n.wan2_interface);
      fillSelect("lan", ifs.interfaces, n.lan_interface);
      $("bridge").value = n.bridge_name;
      $("lanCidr").value = n.lan_cidr;
      $("dhcpStart").value = n.dhcp_start;
      $("dhcpEnd").value = n.dhcp_end;
      $("dns").value = (n.dns_servers || []).join(",");
    }
  } catch (e) {
    $("status").textContent = String(e);
  }
}

async function selectWan(which) {
  $("switchMessage").textContent = "Cambio linea...";
  try {
    await run(["switch", which]);
    $("switchMessage").innerHTML = '<span class="ok">Linea cambiata.</span>';
    await refresh();
  } catch (e) {
    $("switchMessage").innerHTML = '<span class="error">' + String(e) + '</span>';
  }
}

async function applyNetwork() {
  const cfg = configFromForm();
  if (!confirm("Applicare la rete può interrompere temporaneamente la sessione. Continuare?")) return;
  $("applyMessage").textContent = "Applicazione...";
  try {
    const result = await run(["apply", JSON.stringify(cfg)]);
    $("applyMessage").innerHTML = '<span class="ok">Configurazione applicata.</span>';
    $("status").textContent = JSON.stringify(result, null, 2);
    showRoutes(result);
    setTimeout(() => refresh(), 2500);
  } catch (e) {
    $("applyMessage").innerHTML = '<span class="error">' + String(e) + '</span>';
  }
}

$("wan1Btn").addEventListener("click", () => selectWan("wan1"));
$("wan2Btn").addEventListener("click", () => selectWan("wan2"));
$("applyBtn").addEventListener("click", applyNetwork);

refresh(true);
setInterval(() => refresh(), 5000);
