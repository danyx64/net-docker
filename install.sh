#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./install.sh"
  exit 1
fi

apt-get update
apt-get install -y curl ca-certificates iproute2 network-manager dnsmasq nftables

if ! command -v speedtest >/dev/null 2>&1; then
  echo "Installing official Ookla Speedtest CLI..."
  curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash
  apt-get install -y speedtest
fi

install -d /usr/local/lib/net-router /run/net-router /etc/net-router
install -m 0755 host/router-helper.py /usr/local/lib/net-router/router-helper.py
install -m 0644 host/net-router-helper.service /etc/systemd/system/net-router-helper.service

systemctl enable --now NetworkManager
systemctl daemon-reload
systemctl enable --now net-router-helper.service

mkdir -p data config
chmod 0777 data

echo
echo "Net Router host helper installed."
echo "Next:"
echo "  docker compose up -d --build"
echo
echo "Open the UI on:"
echo "  http://HOST_IP:8787"
echo
echo "Use the UI to choose WAN1, WAN2 and LAN ports, bridge address,"
echo "DHCP range, Speedtest server IDs and failover thresholds."
