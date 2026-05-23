import ipaddress
import json
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List


@dataclass
class DHCPConfig:
    network: str = "192.168.1.0/24"
    pool_start: str = "192.168.1.100"
    pool_end: str = "192.168.1.200"
    router: str = "192.168.1.1"
    lease_time_seconds: int = 3600
    dns_servers: List[str] = field(default_factory=lambda: ["8.8.8.8", "1.1.1.1"])

    def set_dns_servers(self, dns_servers: List[str]) -> None:
        if not dns_servers:
            raise ValueError("dns_servers must contain at least one IP")
        for dns in dns_servers:
            ipaddress.ip_address(dns)
        self.dns_servers = dns_servers

    def dhcp_options(self) -> Dict[int, object]:
        subnet_mask = str(ipaddress.ip_network(self.network, strict=False).netmask)
        return {
            1: subnet_mask,
            3: self.router,
            6: self.dns_servers,
            51: self.lease_time_seconds,
        }


class DHCPServerCore:
    def __init__(self, config: DHCPConfig | None = None) -> None:
        self.config = config or DHCPConfig()
        self._leases: Dict[str, str] = {}

    @staticmethod
    def normalize_mac(mac: str) -> str:
        return mac.strip().lower()

    def _pool_ips(self) -> List[str]:
        start = int(ipaddress.ip_address(self.config.pool_start))
        end = int(ipaddress.ip_address(self.config.pool_end))
        if start > end:
            raise ValueError("pool_start must be <= pool_end")
        return [str(ipaddress.ip_address(ip)) for ip in range(start, end + 1)]

    def allocate_ip(self, mac: str) -> str:
        normalized = self.normalize_mac(mac)
        if normalized in self._leases:
            return self._leases[normalized]

        leased = set(self._leases.values())
        for ip in self._pool_ips():
            if ip not in leased:
                self._leases[normalized] = ip
                return ip

        raise RuntimeError("No available addresses in DHCP pool")

    def leases(self) -> Dict[str, str]:
        return dict(self._leases)


def create_api_server(host: str = "127.0.0.1", port: int = 8000, core: DHCPServerCore | None = None) -> ThreadingHTTPServer:
    dhcp_core = core or DHCPServerCore()

    class DHCPAPIHandler(BaseHTTPRequestHandler):
        def _read_json(self) -> Dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise ValueError
                return parsed
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                raise ValueError("Invalid JSON payload")

        def _respond(self, status: HTTPStatus, payload: Dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._respond(HTTPStatus.OK, {"status": "ok"})
                return

            if self.path == "/config":
                cfg = dhcp_core.config
                self._respond(
                    HTTPStatus.OK,
                    {
                        "network": cfg.network,
                        "pool_start": cfg.pool_start,
                        "pool_end": cfg.pool_end,
                        "router": cfg.router,
                        "lease_time_seconds": cfg.lease_time_seconds,
                        "options": dhcp_core.config.dhcp_options(),
                    },
                )
                return

            if self.path == "/leases":
                self._respond(HTTPStatus.OK, {"leases": dhcp_core.leases()})
                return

            self._respond(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/leases":
                self._respond(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return

            try:
                payload = self._read_json()
                mac = str(payload.get("mac", "")).strip()
                if not mac:
                    raise ValueError("Missing mac")
                ip = dhcp_core.allocate_ip(mac)
                self._respond(HTTPStatus.CREATED, {"mac": dhcp_core.normalize_mac(mac), "ip": ip})
            except (ValueError, RuntimeError) as exc:
                self._respond(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_PUT(self) -> None:  # noqa: N802
            if self.path != "/options/6":
                self._respond(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return

            try:
                payload = self._read_json()
                dns_servers = payload.get("dns_servers")
                if not isinstance(dns_servers, list) or not all(isinstance(item, str) for item in dns_servers):
                    raise ValueError("dns_servers must be a list of IP strings")
                dhcp_core.config.set_dns_servers(dns_servers)
                self._respond(HTTPStatus.OK, {"option": 6, "dns_servers": dhcp_core.config.dns_servers})
            except ValueError as exc:
                self._respond(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def log_message(self, message_format: str, *args: object) -> None:
            """Silence default request logs in all environments."""
            return

    return ThreadingHTTPServer((host, port), DHCPAPIHandler)


if __name__ == "__main__":
    server = create_api_server()
    print("DHCP API server running at http://127.0.0.1:8000")
    server.serve_forever()
