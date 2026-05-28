"""
DHCP_config.py
==============
Správa konfigurácie DHCP servera.

Uchováva sieťové parametre (IP, gateway, DNS, subnet maska),
rozsah adresného poolu a voliteľné DHCP options (RFC 2132).
Poskytuje metódy na čítanie, aktualizáciu a validáciu konfigurácie.
"""

import socket


KNOWN_OPTIONS = {
    1:  {"name": "Subnet Mask",             "type": "ip"},
    3:  {"name": "Router",                  "type": "ip"},
    6:  {"name": "DNS Servers",             "type": "ip_list"},
    12: {"name": "Hostname",                "type": "string"},
    15: {"name": "Domain Name",             "type": "string"},
    28: {"name": "Broadcast Address",       "type": "ip"},
    42: {"name": "NTP Servers",             "type": "ip_list"},
    51: {"name": "Lease Time",              "type": "int"},
    66: {"name": "TFTP Server",             "type": "string"},
    67: {"name": "Boot File",               "type": "string"},
    43: {"name": "Vendor Specific",         "type": "string"},
    119:{"name": "Domain Search",           "type": "string"},
    121:{"name": "Classless Static Routes", "type": "string"},
}

PROTECTED_OPTIONS = {1, 3, 6, 51, 53, 54}


def _is_valid_ip(ip: str) -> bool:
    """Overí či je reťazec platnou IPv4 adresou."""
    try:
        socket.inet_aton(ip)
        return ip.count(".") == 3
    except socket.error:
        return False


class DHCPConfig:
    """
    Konfiguračný objekt DHCP servera.

    Atribúty:
        server_ip           -- IP adresa servera (OPT_SERVER_ID)
        server_port         -- Port REST API
        subnet_mask         -- Sieťová maska (option 1)
        gateway             -- Predvolená brána (option 3)
        dns_servers         -- Zoznam DNS serverov (option 6)
        pool_start          -- Začiatok dynamického rozsahu
        pool_end            -- Koniec dynamického rozsahu
        default_lease_time  -- Predvolený čas platnosti lease v sekundách
        max_lease_time      -- Maximálny čas platnosti lease v sekundách
    """

    def __init__(self):
        self.server_ip          = "192.168.1.1"
        self.server_port        = 8080
        self.subnet_mask        = "255.255.255.0"
        self.gateway            = "192.168.1.1"
        self.dns_servers        = ["8.8.8.8", "8.8.4.4"]
        self.pool_start         = "192.168.1.100"
        self.pool_end           = "192.168.1.200"
        self.default_lease_time = 3600
        self.max_lease_time     = 86400
        self._options: dict     = {}

    def to_dict(self) -> dict:
        """Vráti konfiguráciu ako slovník."""
        return {
            "server_ip":          self.server_ip,
            "server_port":        self.server_port,
            "subnet_mask":        self.subnet_mask,
            "gateway":            self.gateway,
            "dns_servers":        self.dns_servers,
            "pool_start":         self.pool_start,
            "pool_end":           self.pool_end,
            "default_lease_time": self.default_lease_time,
            "max_lease_time":     self.max_lease_time,
            "options":            self.all_options(),
        }

    def update(self, data: dict) -> list:
        """
        Aktualizuje konfiguráciu podľa dodaného slovníka.

        Validuje IP adresy a číselné hodnoty. Vráti zoznam chybových
        hlásení – prázdny zoznam znamená úspech.
        """
        errors = []

        for field in ["server_ip", "subnet_mask", "gateway"]:
            if field in data:
                if not _is_valid_ip(data[field]):
                    errors.append(f"Neplatná IP adresa pre '{field}': {data[field]}")
                else:
                    setattr(self, field, data[field])

        if "dns_servers" in data:
            dns = data["dns_servers"]
            if not isinstance(dns, list):
                dns = [dns]
            valid = [ip for ip in dns if _is_valid_ip(ip)]
            if valid:
                self.dns_servers = valid
            else:
                errors.append("Žiaden platný DNS server nebol zadaný")

        for field in ["pool_start", "pool_end"]:
            if field in data:
                if not _is_valid_ip(data[field]):
                    errors.append(f"Neplatná IP adresa pre '{field}': {data[field]}")
                else:
                    setattr(self, field, data[field])

        for field in ["default_lease_time", "max_lease_time"]:
            if field in data:
                try:
                    val = int(data[field])
                    if val > 0:
                        setattr(self, field, val)
                    else:
                        errors.append(f"'{field}' musí byť kladné číslo")
                except (ValueError, TypeError):
                    errors.append(f"'{field}' musí byť celé číslo")

        return errors

    def set_option(self, code: int, value) -> str | None:
        """
        Nastaví voliteľnú DHCP option podľa kódu.

        Chránené options (subnet maska, gateway, DNS, lease time, typ správy,
        server ID) nie je možné nastaviť touto metódou.
        Vráti chybový reťazec alebo None pri úspechu.
        """
        if code in PROTECTED_OPTIONS:
            name = KNOWN_OPTIONS.get(code, {}).get("name", str(code))
            return f"Option {code} ({name}) je spravovaná automaticky"
        if not (1 <= code <= 254):
            return "Kód option musí byť v rozsahu 1–254"
        info = KNOWN_OPTIONS.get(code, {})
        self._options[str(code)] = {
            "code":  code,
            "name":  info.get("name", f"Option {code}"),
            "value": value,
        }
        return None

    def get_option(self, code: int):
        """Vráti hodnotu option podľa kódu alebo None."""
        entry = self._options.get(str(code))
        return entry["value"] if entry else None

    def remove_option(self, code: int) -> bool:
        """Odstráni voliteľnú option. Vráti True ak existovala."""
        return self._options.pop(str(code), None) is not None

    def all_options(self) -> dict:
        """Vráti slovník všetkých nastavených voliteľných options."""
        return dict(self._options)

    def known_options_list(self) -> list:
        """Vráti zoznam všetkých známych DHCP options s ich popisom."""
        return [
            {"code": code, "name": info["name"], "type": info["type"]}
            for code, info in KNOWN_OPTIONS.items()
            if code not in PROTECTED_OPTIONS
        ]