#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Esegui come root"; exit 1; }

apt-get update
apt-get install -y cockpit cockpit-networkmanager network-manager iproute2 nftables python3

for c in net-bridge net-lan net-wan1 net-wan2; do
  nmcli -t -f NAME con show | grep -Fxq "$c" && nmcli con delete "$c" || true
done

install -d /usr/local/lib/net-router /usr/local/sbin /usr/local/share/cockpit/router /run/net-router /etc/net-router
install -m 0755 host/router-helper.py /usr/local/lib/net-router/router-helper.py
install -m 0755 bin/net-routerctl /usr/local/sbin/net-routerctl
install -m 0644 host/net-router-helper.service /etc/systemd/system/net-router-helper.service
install -m 0644 cockpit/manifest.json /usr/local/share/cockpit/router/manifest.json
install -m 0644 cockpit/index.html /usr/local/share/cockpit/router/index.html
install -m 0644 cockpit/router.js /usr/local/share/cockpit/router/router.js
install -m 0644 cockpit/router.css /usr/local/share/cockpit/router/router.css

printf '%s\n' 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-net-router.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null

systemctl daemon-reload
systemctl enable --now nftables
systemctl enable --now cockpit.socket
systemctl enable --now net-router-helper.service
systemctl restart net-router-helper.service

echo
echo "Router Cockpit aggiornato: dual-WAN + mapping 1:1 + tema scuro."
echo "Apri Cockpit e fai Ctrl+Shift+R."
