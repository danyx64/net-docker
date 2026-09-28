# net-docker

Dual-WAN router appliance for Debian/CasaOS hosts.

## What it does

- Uses three host Ethernet interfaces:
  - WAN1: `enp3s0`
  - WAN2: `enx00e04c680270`
  - LAN: `enp4s0`
- Runs a web UI in Docker.
- Periodically runs Ookla Speedtest CLI against a configured server ID per WAN.
- Lets you define minimum download/upload thresholds.
- Automatically switches the host default route to the other WAN when the active WAN falls below thresholds for a configurable number of consecutive tests.
- Supports manual AUTO / WAN1 / WAN2 selection.
- Keeps the switching logic on the host through a very small privileged helper instead of giving the whole web container full host privileges.

## Important

Docker cannot safely "own" the physical NICs while the host is also using them. This project therefore keeps routing/NAT on the Debian host and runs the controller/UI in Docker. The controller talks to a restricted host helper through a bind-mounted Unix socket.

Before installation, make sure WAN1 and WAN2 already obtain an IPv4 address and gateway via DHCP.

## Quick start

```bash
git clone https://github.com/danyx64/net-docker.git
cd net-docker
sudo ./install.sh
docker compose up -d --build
```

Open:

```text
http://HOST_IP:8787
```

Default configuration is in `config/config.yaml`.
