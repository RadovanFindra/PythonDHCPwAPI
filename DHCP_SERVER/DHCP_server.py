"""
server.py – Hlavný vstupný bod DHCP servera.
Spustenie: python server.py [--port 8080] [--start 192.168.1.100] [--end 192.168.1.200]
"""

import sys
import time

from DHCP_config import DHCPConfig
from DHCP_pool import DHCPPool
from REST_API import DHCPRestAPI

def parse_args(argv: list) -> dict:
    """Jednoduchý parser argumentov príkazového riadku bez argparse."""
    args = {
        "port": 8080,
        "host": "0.0.0.0",
        "pool_start": "192.168.1.100",
        "pool_end":   "192.168.1.200",
        "gateway":    "192.168.1.1",
        "dns":        "8.8.8.8",
        "lease_time": 3600,
    }
    i = 1
    while i < len(argv):
        key = argv[i].lstrip("-")
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            args[key] = argv[i + 1]
            i += 2
        else:
            i += 1
    for k in ("port", "lease_time"):
        try:
            args[k] = int(args[k])
        except (ValueError, TypeError):
            pass
    return args


def print_banner(config, pool):
    stats = pool.pool_stats()
    print("=" * 55)
    print("   DHCP Server s REST API  –  Python (bez frameworkov)")
    print("=" * 55)
    print(f"  REST API:       http://127.0.0.1:{config.server_port}")
    print(f"  Gateway:        {config.gateway}")
    print(f"  DNS servery:    {', '.join(config.dns_servers)}")
    print(f"  Subnet maska:   {config.subnet_mask}")
    print(f"  Pool:           {stats['start_ip']} – {stats['end_ip']}")
    print(f"  Celkový pool:   {stats['total']} adries")
    print(f"  Lease time:     {config.default_lease_time}s")
    print("=" * 55)
    print("  Dostupné endpointy:")
    print("  GET    /health          – stav servera")
    print("  GET    /config          – aktuálna konfigurácia")
    print("  POST   /config          – zmena konfigurácie")
    print("  GET    /leases          – aktívne lease záznamy")
    print("  GET    /pool            – štatistiky poolu")
    print("  POST   /lease/assign    – pridelenie adresy")
    print("  POST   /lease/release   – uvoľnenie adresy")
    print("  GET    /options         – DHCP options")
    print("  POST   /options         – nastavenie option")
    print("  DELETE /options/<code>  – odstránenie option")
    print("=" * 55)
    print("  Stlačte Ctrl+C pre ukončenie.")
    print("=" * 55)


def main():
    
    args = parse_args(sys.argv)

    config = DHCPConfig()
    config.server_port        = args["port"]
    config.gateway            = args["gateway"]
    config.dns_servers        = [args["dns"]]
    config.pool_start         = args["pool_start"]
    config.pool_end           = args["pool_end"]
    config.default_lease_time = args["lease_time"]

    pool = DHCPPool(
        start_ip=config.pool_start,
        end_ip=config.pool_end,
        default_lease_time=config.default_lease_time,
    )

    api = DHCPRestAPI(config=config, pool=pool)

    print_banner(config, pool)

    try:
        api.start(host=args["host"], port=args["port"])
    except KeyboardInterrupt:
        print("\n[Server] Zastavujem...")
        api.stop()


if __name__ == "__main__":
    main()