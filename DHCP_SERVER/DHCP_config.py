"""
config.py – Konfigurácia DHCP servera a DHCP options.
Bez externých knižníc.
"""

from DHCP_pool import validate_ip


# Najčastejšie DHCP options (RFC 2132) – kód: popis
KNOWN_OPTIONS = {
    1:   "subnet_mask",
    3:   "router",           # predvolená brána
    6:   "dns_servers",
    12:  "hostname",
    15:  "domain_name",
    28:  "broadcast_address",
    42:  "ntp_servers",
    51:  "lease_time",
    66:  "tftp_server",
    67:  "boot_file",
    43:  "vendor_specific",
    119: "domain_search",
    121: "classless_static_routes",
}


class DHCPConfig:
    """
    Uchováva celú konfiguráciu servera.
    Povinné: gateway, dns, subnet_mask.
    Voliteľné: ďalšie DHCP options.
    """

    def __init__(self):
        # --- Povinné parametre ---
        self.subnet_mask: str = "255.255.255.0"
        self.gateway: str = "192.168.1.1"
        self.dns_servers: list = ["8.8.8.8", "8.8.4.4"]

        # --- Voliteľné DHCP options (kód -> hodnota) ---
        self._options: dict = {}

        # --- Pool parametre ---
        self.pool_start: str = "192.168.1.100"
        self.pool_end: str = "192.168.1.200"
        self.default_lease_time: int = 3600   # sekundy
        self.max_lease_time: int = 86400

        # --- Server parametre ---
        self.server_ip: str = "192.168.1.1"
        self.server_port: int = 8080          # REST API port

    # ------------------------------------------------------------------
    # Validácia a aktualizácia konfigurácie
    # ------------------------------------------------------------------

    def update(self, data: dict) -> list:
        """
        Aktualizuje konfiguráciu zo slovníka.
        Vráti zoznam chybových hlásení (prázdny = OK).
        """
        errors = []

        if "gateway" in data:
            if validate_ip(data["gateway"]):
                self.gateway = data["gateway"]
            else:
                errors.append(f"Neplatná gateway: {data['gateway']}")

        if "subnet_mask" in data:
            if validate_ip(data["subnet_mask"]):
                self.subnet_mask = data["subnet_mask"]
            else:
                errors.append(f"Neplatná subnet_mask: {data['subnet_mask']}")

        if "dns_servers" in data:
            dns_list = data["dns_servers"]
            if not isinstance(dns_list, list):
                dns_list = [dns_list]
            valid_dns = [ip for ip in dns_list if validate_ip(ip)]
            if valid_dns:
                self.dns_servers = valid_dns
            else:
                errors.append("Žiaden platný DNS server nebol zadaný")

        if "pool_start" in data:
            if validate_ip(data["pool_start"]):
                self.pool_start = data["pool_start"]
            else:
                errors.append(f"Neplatný pool_start: {data['pool_start']}")

        if "pool_end" in data:
            if validate_ip(data["pool_end"]):
                self.pool_end = data["pool_end"]
            else:
                errors.append(f"Neplatný pool_end: {data['pool_end']}")

        if "default_lease_time" in data:
            try:
                val = int(data["default_lease_time"])
                if val > 0:
                    self.default_lease_time = val
                else:
                    errors.append("default_lease_time musí byť kladné číslo")
            except (ValueError, TypeError):
                errors.append("default_lease_time musí byť celé číslo")

        if "max_lease_time" in data:
            try:
                val = int(data["max_lease_time"])
                if val > 0:
                    self.max_lease_time = val
                else:
                    errors.append("max_lease_time musí byť kladné číslo")
            except (ValueError, TypeError):
                errors.append("max_lease_time musí byť celé číslo")

        if "server_ip" in data:
            if validate_ip(data["server_ip"]):
                self.server_ip = data["server_ip"]
            else:
                errors.append(f"Neplatná server_ip: {data['server_ip']}")

        return errors

    # ------------------------------------------------------------------
    # DHCP Options
    # ------------------------------------------------------------------

    def set_option(self, code: int, value):
        """Nastaví voliteľnú DHCP option. Vráti chybový reťazec alebo None."""
        if not isinstance(code, int) or code < 1 or code > 254:
            return "Kód option musí byť celé číslo v rozsahu 1–254"
        self._options[code] = value
        return None

    def get_option(self, code: int):
        return self._options.get(code)

    def remove_option(self, code: int) -> bool:
        return self._options.pop(code, None) is not None

    def all_options(self) -> dict:
        result = {}
        for code, value in self._options.items():
            name = KNOWN_OPTIONS.get(code, f"option_{code}")
            result[str(code)] = {"name": name, "value": value}
        return result

    # ------------------------------------------------------------------
    # Serializácia
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "server_ip": self.server_ip,
            "server_port": self.server_port,
            "subnet_mask": self.subnet_mask,
            "gateway": self.gateway,
            "dns_servers": self.dns_servers,
            "pool_start": self.pool_start,
            "pool_end": self.pool_end,
            "default_lease_time": self.default_lease_time,
            "max_lease_time": self.max_lease_time,
            "options": self.all_options(),
        }

    def known_options_list(self) -> list:
        return [
            {"code": code, "name": name}
            for code, name in KNOWN_OPTIONS.items()
        ]