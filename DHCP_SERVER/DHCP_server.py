"""
DHCP_server.py – Hlavný vstupný bod.
Spúšťa súčasne UDP DHCP server (port 67) + REST API (port 8080).

Spustenie:
  sudo python DHCP_server.py
  sudo python DHCP_server.py --port 8080 --start 192.168.1.100 --end 192.168.1.200
  python DHCP_server.py --no-udp   ← bez root práv, len REST API
"""

import sys
import threading

from DHCP_config   import DHCPConfig
from DHCP_pool     import DHCPPool
from REST_API      import DHCPRestAPI
from DHCP_protocol import DHCPUDPServer


def parse_args(argv: list) -> dict:
    args = {
        "port":       8080,
        "host":       "0.0.0.0",
        "pool_start": "192.168.1.100",
        "pool_end":   "192.168.1.200",
        "gateway":    "192.168.1.1",
        "dns":        "8.8.8.8",
        "lease_time": 3600,
        "no_udp":     False,
    }
    i = 1
    while i < len(argv):
        key = argv[i].lstrip("-").replace("-", "_")
        if key == "no_udp":
            args["no_udp"] = True
            i += 1
        elif i + 1 < len(argv) and not argv[i + 1].startswith("-"):
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


def print_banner(config, pool, udp_enabled: bool):
    stats = pool.pool_stats()
    udp_status = "port 67/UDP  (aktívny)" if udp_enabled else "port 67/UDP  (vypnutý – chýbajú root práva)"
    print("=" * 58)
    print("   DHCP Server s REST API  –  Python (bez frameworkov)")
    print("=" * 58)
    print(f"  REST API:       http://127.0.0.1:{config.server_port}")
    print(f"  DHCP UDP:       {udp_status}")
    print(f"  Server IP:      {config.server_ip}")
    print(f"  Gateway:        {config.gateway}")
    print(f"  DNS servery:    {', '.join(config.dns_servers)}")
    print(f"  Subnet maska:   {config.subnet_mask}")
    print(f"  Pool:           {stats['start_ip']} – {stats['end_ip']}")
    print(f"  Celkový pool:   {stats['total']} adries")
    print(f"  Lease time:     {config.default_lease_time}s")
    print("=" * 58)
    print("  REST API endpointy:")
    print("  GET    /health          – stav servera")
    print("  GET    /config          – konfigurácia")
    print("  POST   /config          – zmena konfigurácie")
    print("  GET    /leases          – aktívne lease záznamy")
    print("  GET    /pool            – štatistiky poolu")
    print("  POST   /lease/assign    – pridelenie adresy")
    print("  POST   /lease/release   – uvoľnenie adresy")
    print("  GET    /options         – DHCP options")
    print("  POST   /options         – nastavenie option")
    print("  DELETE /options/<code>  – odstránenie option")
    print("=" * 58)
    if udp_enabled:
        print("  DHCP UDP: DISCOVER → OFFER → REQUEST → ACK/NAK")
    print("  Stlačte Ctrl+C pre ukončenie.")
    print("=" * 58)


def main():
    args = parse_args(sys.argv)

    config = DHCPConfig()
    config.server_port        = args["port"]
    config.gateway            = args["gateway"]
    config.dns_servers        = [args["dns"]]
    config.pool_start         = args["pool_start"]
    config.pool_end           = args["pool_end"]
    config.default_lease_time = args["lease_time"]

    # Pool je zdieľaný – UDP aj REST API pracujú s tými istými lease záznamami
    pool = DHCPPool(
        start_ip=config.pool_start,
        end_ip=config.pool_end,
        default_lease_time=config.default_lease_time,
    )

    udp_server  = DHCPUDPServer(config=config, pool=pool)
    udp_enabled = not args["no_udp"]
    if udp_enabled:
        udp_server.start_in_thread()

    api = DHCPRestAPI(config=config, pool=pool)
    print_banner(config, pool, udp_enabled)

    try:
        api.start(host=args["host"], port=args["port"])
    except KeyboardInterrupt:
        print("\n[Server] Zastavujem...")
        api.stop()
        udp_server.stop()


if __name__ == "__main__":
    main()