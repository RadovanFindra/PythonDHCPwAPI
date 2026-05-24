"""
DHCP_server.py – Hlavný vstupný bod DHCP servera.
Spúšťa súčasne:
  - UDP DHCP server na porte 67  (vyžaduje root/sudo)
  - REST API server  na porte 8080

Spustenie:
  sudo python DHCP_server.py
  sudo python DHCP_server.py --static-file /etc/dhcp/static_leases.json
  python DHCP_server.py --udp-port 1067    # testovanie bez sudo
"""

import sys

from DHCP_config   import DHCPConfig
from DHCP_pool     import DHCPPool
from REST_API      import DHCPRestAPI
from DHCP_protocol import DHCPUDPServer


def parse_args(argv: list) -> dict:
    args = {
        "port":        8080,
        "host":        "192.168.1.1",
        "pool_start":  "192.168.1.100",
        "pool_end":    "192.168.1.200",
        "gateway":     "192.168.1.1",
        "dns":         "8.8.8.8",
        "lease_time":  3600,
        "udp_host":    "0.0.0.0",
        "udp_port":    67,
        "server_ip":   "192.168.1.1", #API
        "static_file": "static_leases.json",   # ← nové
        "interface":   "ens19",   # ← nové (nepovinné)
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


def print_banner(config, pool, udp_host: str, udp_port: int, static_file: str):
    stats = pool.pool_stats()
    print("=" * 58)
    print("   DHCP Server s REST API  –  Python (bez frameworkov)")
    print("=" * 58)
    print(f"  REST API:       http://127.0.0.1:{config.server_port}")
    print(f"  DHCP UDP:       {udp_host}:{udp_port}/UDP")
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
    print("  DELETE /leases/static/<mac>  – odstránenie statického lease")
    print("=" * 58)
    print("  DHCP UDP: DISCOVER → OFFER → REQUEST → ACK/NAK")
    print("  Stlačte Ctrl+C pre ukončenie.")
    print("=" * 58)


def main():
    args = parse_args(sys.argv)

    # --- Konfigurácia ---
    config = DHCPConfig()
    config.server_port        = args["port"]
    config.server_ip          = args["server_ip"]
    config.gateway            = args["gateway"]
    config.dns_servers        = [args["dns"]]
    config.pool_start         = args["pool_start"]
    config.pool_end           = args["pool_end"]
    config.default_lease_time = args["lease_time"]

    # --- Pool – zdieľaný medzi UDP aj REST API ---
    pool = DHCPPool(
        start_ip=config.pool_start,
        end_ip=config.pool_end,
        default_lease_time=config.default_lease_time,
        static_leases_file=args["static_file"],   # ← nové
    )

    # --- UDP DHCP server ---
    udp_server = DHCPUDPServer(config=config, pool=pool)
    udp_server.start_in_thread(
        host=args["udp_host"],
        port=args["udp_port"],
        interface=args["interface"],   # ← nové (nepovinné)
    )

    # --- REST API server (blokuje hlavné vlákno) ---
    api = DHCPRestAPI(config=config, pool=pool)

    print_banner(config, pool, args["udp_host"], args["udp_port"], args["static_file"])

    try:
        api.start(host=args["host"], port=args["port"])
    except KeyboardInterrupt:
        print("\n[Server] Zastavujem...")
        api.stop()
        udp_server.stop()


if __name__ == "__main__":
    main()