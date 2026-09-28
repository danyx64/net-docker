# net-docker

Web UI Docker per trasformare un host Debian/CasaOS in un router dual-WAN semplice da gestire.

## Topologia consigliata

```text
Modem WAN1 ──> enp3s0
Modem WAN2 ──> enx00e04c680270
GS308/LAN   <─ enp4s0
                │
              Node-2
```

La UI gira in Docker, mentre un piccolo helper sul sistema host applica le modifiche reali a NetworkManager, bridge, DHCP, NAT e route.

Questo evita di dare al container il controllo completo del sistema tramite `--privileged`.

## Funzioni

- scelta da UI di WAN1, WAN2 e porta LAN;
- creazione bridge Linux LAN, ad esempio `br0`;
- IP LAN e range DHCP configurabili;
- dnsmasq configurato automaticamente;
- NAT nftables su entrambe le WAN;
- forwarding IPv4;
- WAN via DHCP;
- priorità WAN1/WAN2;
- modalità AUTO, WAN1 forzata, WAN2 forzata;
- Ookla Speedtest CLI;
- server Speedtest fisso per ogni WAN tramite server ID;
- ricerca dei server Ookla vicini;
- test manuale da UI;
- test automatici periodici;
- soglia minima download e upload separata per WAN;
- passaggio a WAN2 dopo N test WAN1 sotto soglia;
- ritorno a WAN1 dopo N test validi;
- storico degli ultimi test conservato nel file di stato.

## Immagine Docker precompilata

Ogni push su `main` pubblica automaticamente:

```text
ghcr.io/danyx64/net-docker:latest
```

Per scaricare solo l'immagine:

```bash
docker pull ghcr.io/danyx64/net-docker:latest
```

Il `docker-compose.yml` del repository usa già questa immagine, quindi CasaOS non deve compilarla.

> Il container gestisce la UI e la logica. Per modificare davvero bridge, route, DHCP, NAT e interfacce Ethernet serve anche l'helper host incluso nel repository. È una installazione una tantum sul Debian/CasaOS host.

## Installazione Node-2 / CasaOS

Sul Node-2:

```bash
git clone https://github.com/danyx64/net-docker.git
cd net-docker
chmod +x install.sh
sudo ./install.sh
docker compose pull
docker compose up -d
```

Dopo questo CasaOS/Docker userà direttamente `ghcr.io/danyx64/net-docker:latest`.

Apri:

```text
http://IP-DEL-NODE-2:8787
```

## Prima configurazione dalla UI

Per l'hardware attuale:

```text
WAN1: enp3s0
WAN2: enx00e04c680270
LAN : enp4s0
Bridge: br0
LAN: 192.168.100.1/24
DHCP: 192.168.100.50 - 192.168.100.200
```

Poi premi **SALVA + APPLICA RETE**.

Il pulsante può interrompere temporaneamente la connessione al Node-2, perché NetworkManager ricrea le connessioni delle tre porte.

## Speedtest

Ogni WAN ha:

- Server ID Ookla. `0` significa selezione automatica.
- Download minimo in Mbps.
- Upload minimo in Mbps.

Dalla UI puoi anche premere **Mostra server vicini** e selezionare uno degli ID trovati.

Il client Ookla supporta il binding alla specifica interfaccia con `--interface` e la selezione fissa del server con `--server-id`. Questo permette di testare WAN1 e WAN2 separatamente.

## Logica AUTO

Esempio:

```text
WAN1 minimo: 50 Mbps down / 10 Mbps up
WAN2 minimo: 20 Mbps down / 5 Mbps up
Test ogni: 300 secondi
KO prima del failover: 2
OK WAN1 prima del ritorno: 3
```

Se WAN1 resta sotto una delle sue soglie per il numero configurato di test consecutivi e WAN2 è valida, il router sposta la route principale su WAN2.

Quando WAN1 torna sopra soglia per il numero configurato di test consecutivi, viene ripristinata come WAN principale.

## Nota importante sullo switch

Il Netgear GS308 unmanaged continua a fare semplicemente switching Ethernet. Tutto il routing, DHCP, NAT e failover viene fatto dal Node-2.

I dispositivi LAN vanno collegati al GS308; il GS308 va collegato solo alla porta LAN scelta sul Node-2.

## Wi-Fi

Se il Node-2 è collegato anche via Wi-Fi a uno degli stessi modem, può comparire una route predefinita aggiuntiva. Per un router stabile conviene lasciare attive come uplink solo le due WAN configurate, oppure usare il Wi-Fi esclusivamente come gestione senza route predefinita.

## File principali

```text
app/main.py                       UI + API + logica failover
host/router-helper.py             operazioni privilegiate host
host/net-router-helper.service    servizio helper
config/config.yaml                configurazione
data/state.json                   risultati e stato runtime
docker-compose.yml                container UI/controller
```

## Porte

- UI Net Router: TCP `8787`
- CasaOS resta sulla sua porta attuale.
- Non viene esposto nessun socket privilegiato via TCP: il container comunica con l'helper tramite `/run/net-router/router.sock`.
