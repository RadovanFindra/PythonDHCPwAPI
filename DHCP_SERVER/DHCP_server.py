"""
DHCP_server.py
==============
Hlavný vstupný bod DHCP servera.

Spúšťa súčasne dva servery v rámci jedného procesu:
  - UDP DHCP server na porte 67  (spracúva reálnych DHCP klientov)
  - REST API server  na porte 8080 (správa cez HTTP)

Oba servery zdieľajú ten istý objekt DHCPPool – lease pridelené
cez REST API sú viditeľné pre UDP klientov a naopak.

Port 67 vyžaduje root/sudo práva. REST API je dostupné bez root.

Použitie:
  sudo python3 DHCP_server.py
  sudo python3 DHCP_server.py --server-ip 192.168.1.1 --gateway 192.168.1.1
  sudo python3 DHCP_server.py --udp-port 1067
  sudo python3 DHCP_server.py --static-file /etc/dhcp/static_leases.json
  sudo python3 DHCP_server.py --interface ens19

Parametre:
  --port          Port REST API (predvolene 8080)
  --host          Host REST API (predvolene 0.0.0.0)
  --udp-host      Host UDP servera (predvolene 0.0.0.0)
  --udp-port      Port UDP servera (predvolene 67)
  --server-ip     IP adresa servera v DHCP paketoch (predvolene 192.168.1.1)
  --gateway       Predvolená brána pre klientov (predvolene 192.168.1.1)
  --dns           DNS server pre klientov (predvolene 8.8.8.8)
  --start         Začiatok adresného poolu (predvolene 192.168.1.100)
  --end           Koniec adresného poolu (predvolene 192.168.1.200)
  --lease-time    Doba platnosti lease v sekundách (predvolene 3600)
  --static-file   Cesta k JSON súboru so statickými lease
  --interface     Sieťové rozhranie pre UDP server (nepovinné, napr. ens19)
"""

import sys

from DHCP_config   import DHCPConfig
from DHCP_pool     import DHCPPool
from REST_API      import DHCPRestAPI
from DHCP_protocol import DHCPUDPServer


def parse_args(argv: list) -> dict:
    """
    Parsuje argumenty príkazového riadku bez použitia argparse.

    Podporuje formát --nazov-parametra hodnota.
    Pomlčky v názvoch parametrov sú nahradené podčiarkovníkmi.

    Returns:
        Slovník s hodnotami všetkých parametrov.
    """
    args = {
        "port":        8080,
        "host":        "0.0.0.0",
        "udp_host":    "0.0.0.0",
        "udp_port":    67,
        "server_ip":   "192.168.1.1",
        "gateway":     "192.168.1.1",
        "dns":         "8.8.8.8",
        "start":       "192.168.1.100",
        "end":         "192.168.1.200",
        "lease_time":  3600,
        "static_file": "static_leases.json",
        "interface":   "ens19",
    }
    i = 1
    while i < len(argv):
        key = argv[i].lstrip("-").replace("-", "_")
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            args[key] = argv[i + 1]
            i += 2
        else:
            i += 1
    for k in ("port", "lease_time", "udp_port"):
        try:
            args[k] = int(args[k])
        except (ValueError, TypeError):
            pass
    return args


def print_banner(config, pool, udp_host: str, udp_port: int,
                 static_file: str, interface: str):
    """Vypíše informačný banner so zhrnutím konfigurácie pri štarte servera."""
    stats = pool.pool_stats()
    print("=" * 58)
    print("   DHCP Server s REST API  –  Python (bez frameworkov)")
    print("=" * 58)
    print(f"  REST API:       http://127.0.0.1:{config.server_port}")
    print(f"  DHCP UDP:       {udp_host}:{udp_port}/UDP")
    if interface:
        print(f"  Rozhranie:      {interface}")
    print(f"  Server IP:      {config.server_ip}")
    print(f"  Gateway:        {config.gateway}")
    print(f"  DNS servery:    {', '.join(config.dns_servers)}")
    print(f"  Subnet maska:   {config.subnet_mask}")
    print(f"  Pool:           {stats['start_ip']} – {stats['end_ip']}")
    print(f"  Celkový pool:   {stats['total']} adries")
    print(f"  Statické lease: {stats['static']} (súbor: {static_file})")
    print(f"  Lease time:     {config.default_lease_time}s")
    print("=" * 58)
    print("  REST API endpointy:")
    print("  GET    /health               – stav servera")
    print("  GET    /config               – konfigurácia")
    print("  POST   /config               – zmena konfigurácie")
    print("  GET    /leases               – aktívne lease záznamy")
    print("  GET    /pool                 – štatistiky poolu")
    print("  POST   /lease/assign         – pridelenie adresy")
    print("  POST   /lease/release        – uvoľnenie adresy")
    print("  GET    /options              – DHCP options")
    print("  POST   /options              – nastavenie option")
    print("  DELETE /options/<code>       – odstránenie option")
    print("  GET    /leases/static        – statické lease")
    print("  POST   /leases/static        – pridanie statického lease")
    print("  DELETE /leases/static/<id>   – odstránenie statického lease")
    print("=" * 58)
    print("  DHCP UDP: DISCOVER → OFFER → REQUEST → ACK/NAK")
    print("  Stlačte Ctrl+C pre ukončenie.")
    print("=" * 58)


def main():
    """
    Inicializuje konfiguráciu, pool, UDP server a REST API, potom spustí server.

    UDP DHCP server beží v daemon vlákne.
    REST API blokuje hlavné vlákno až do stlačenia Ctrl+C.
    """
    args = parse_args(sys.argv)

    config = DHCPConfig()
    config.server_port        = args["port"]
    config.server_ip          = args["server_ip"]
    config.gateway            = args["gateway"]
    config.dns_servers        = [args["dns"]]
    config.pool_start         = args["start"]
    config.pool_end           = args["end"]
    config.default_lease_time = args["lease_time"]

    pool = DHCPPool(
        start_ip=config.pool_start,
        end_ip=config.pool_end,
        default_lease_time=config.default_lease_time,
        static_leases_file=args["static_file"],
    )

    udp_server = DHCPUDPServer(config=config, pool=pool)
    udp_server.start_in_thread(
        host=args["udp_host"],
        port=args["udp_port"],
        interface=args["interface"],
    )

    api = DHCPRestAPI(config=config, pool=pool)
    print_banner(config, pool, args["udp_host"], args["udp_port"],
                 args["static_file"], args["interface"])

    try:
        api.start(host=args["host"], port=args["port"])
    except KeyboardInterrupt:
        print("\n[Server] Zastavujem...")
        api.stop()
        udp_server.stop()


if __name__ == "__main__":
    main()