#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Esegui come root"; exit 1; }

apt-get update
apt-get install -y cockpit cockpit-networkmanager network-manager iproute2 python3

install -d /usr/local/lib/net-router /usr/local/sbin /usr/local/share/cockpit/router /run/net-router /etc/net-router
install -m 0755 host/router-helper.py /usr/local/lib/net-router/router-helper.py
install -m 0755 bin/net-routerctl /usr/local/sbin/net-routerctl
install -m 0644 host/net-router-helper.service /etc/systemd/system/net-router-helper.service
install -m 0644 cockpit/manifest.json /usr/local/share/cockpit/router/manifest.json
install -m 0644 cockpit/index.html /usr/local/share/cockpit/router/index.html
install -m 0644 cockpit/router.js /usr/local/share/cockpit/router/router.js
install -m 0644 cockpit/router.css /usr/local/share/cockpit/router/router.css

systemctl enable --now NetworkManager
systemctl daemon-reload
systemctl enable --now net-router-helper.service
systemctl enable --now cockpit.socket
systemctl restart net-router-helper.service

echo "Installato. Apri Cockpit :9090 e fai Ctrl+Shift+R."
