"""
DHCP_pool.py – Správa adresného poolu a lease záznamov.
Statické lease sú persistentné – ukladajú sa do JSON súboru.
Žiadne externé knižnice – iba štandardné moduly.
"""

import time
import threading
import json
import os


def ip_to_int(ip: str) -> int:
    parts = ip.strip().split(".")
    if len(parts) != 4:
        raise ValueError(f"Neplatná IP adresa: {ip}")
    result = 0
    for part in parts:
        val = int(part)
        if val < 0 or val > 255:
            raise ValueError(f"Neplatná IP adresa: {ip}")
        result = (result << 8) | val
    return result


def int_to_ip(n: int) -> str:
    return ".".join([str((n >> (8 * i)) & 0xFF) for i in reversed(range(4))])


def validate_ip(ip: str) -> bool:
    try:
        ip_to_int(ip)
        return True
    except (ValueError, AttributeError):
        return False


def normalize_mac(mac: str) -> str:
    """Normalizuje MAC adresu na veľké písmená s dvojbodkami."""
    return mac.upper().replace("-", ":")


class Lease:
    def __init__(self, ip: str, client_id: str, lease_time: int):
        self.ip = ip
        self.client_id = client_id
        self.assigned_at = time.time()
        self.lease_time = lease_time

    @property
    def expires_at(self) -> float:
        return self.assigned_at + self.lease_time

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def renew(self, lease_time: int = None):
        self.assigned_at = time.time()
        if lease_time is not None:
            self.lease_time = lease_time

    def to_dict(self) -> dict:
        return {
            "ip": self.ip,
            "client_id": self.client_id,
            "assigned_at": self.assigned_at,
            "lease_time": self.lease_time,
            "expires_at": self.expires_at,
            "expired": self.is_expired,
        }


class DHCPPool:
    def __init__(self, start_ip: str, end_ip: str,
                 default_lease_time: int = 3600,
                 static_leases_file: str = "static_leases.json"):
        self._lock = threading.Lock()
        self.start_ip = start_ip
        self.end_ip = end_ip
        self.default_lease_time = default_lease_time
        self._start_int = ip_to_int(start_ip)
        self._end_int = ip_to_int(end_ip)

        if self._start_int > self._end_int:
            raise ValueError("start_ip musí byť menšia alebo rovnaká ako end_ip")

        self._leases: dict = {}
        self._client_map: dict = {}

        # Statické lease – MAC -> IP
        self._static_file = static_leases_file
        self._static_leases: dict = {}
        self._load_static_leases()

    # ------------------------------------------------------------------
    # Persistencia statických lease
    # ------------------------------------------------------------------

    def _load_static_leases(self):
        """Načíta statické lease zo súboru pri štarte."""
        if not os.path.exists(self._static_file):
            print(f"[Pool] Súbor {self._static_file} neexistuje – začínam s prázdnymi statickými lease.")
            return
        try:
            with open(self._static_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Normalizujeme MAC adresy pri načítaní
                self._static_leases = {
                    normalize_mac(mac): ip
                    for mac, ip in data.items()
                }
                print(f"[Pool] Načítaných {len(self._static_leases)} statických lease z {self._static_file}")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[Pool] Chyba pri načítaní {self._static_file}: {e}")

    def _save_static_leases(self):
        """Uloží statické lease do súboru po každej zmene."""
        try:
            with open(self._static_file, "w", encoding="utf-8") as f:
                json.dump(self._static_leases, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"[Pool] Chyba pri ukladaní {self._static_file}: {e}")

    # ------------------------------------------------------------------
    # Správa statických lease
    # ------------------------------------------------------------------

    def add_static(self, mac: str, ip: str) -> str | None:
        """
        Pridá statický lease (MAC → IP) a uloží do súboru.
        Vráti chybový reťazec alebo None ak OK.
        """
        mac = normalize_mac(mac)

        if not validate_ip(ip):
            return f"Neplatná IP adresa: {ip}"
        if not self._ip_in_range(ip):
            return f"IP {ip} nie je v rozsahu poolu ({self.start_ip} – {self.end_ip})"

        # Kontrola duplicity IP
        for existing_mac, existing_ip in self._static_leases.items():
            if existing_ip == ip and existing_mac != mac:
                return f"IP {ip} je už priradená MAC adrese {existing_mac}"

        self._static_leases[mac] = ip
        self._save_static_leases()
        print(f"[Pool] Statický lease pridaný: {mac} → {ip}")
        return None

    def remove_static(self, mac: str) -> bool:
        """Odstráni statický lease podľa MAC. Vráti True ak existoval."""
        mac = normalize_mac(mac)
        if mac in self._static_leases:
            ip = self._static_leases.pop(mac)
            self._save_static_leases()
            print(f"[Pool] Statický lease odstránený: {mac} → {ip}")
            return True
        return False

    def all_static_leases(self) -> list:
        return [
            {"mac": mac, "ip": ip}
            for mac, ip in self._static_leases.items()
        ]

    # ------------------------------------------------------------------
    # Prideľovanie adries
    # ------------------------------------------------------------------

    def assign(self, client_id: str, requested_ip: str = None):
        client_id = normalize_mac(client_id)
        with self._lock:
            self._expire_leases()

            # Statický lease má vždy prednosť
            static_ip = self._static_leases.get(client_id)
            if static_ip:
                lease = self._leases.get(static_ip)
                if lease and lease.client_id == client_id:
                    lease.renew()
                    return lease
                # Ak statickú IP drží iný klient, uvoľníme ju
                if static_ip in self._leases:
                    old_lease = self._leases.pop(static_ip)
                    self._client_map.pop(old_lease.client_id, None)
                return self._create_lease(client_id, static_ip)

            # Dynamické prideľovanie
            if client_id in self._client_map:
                existing_ip = self._client_map[client_id]
                lease = self._leases.get(existing_ip)
                if lease and not lease.is_expired:
                    lease.renew()
                    return lease

            if requested_ip and validate_ip(requested_ip):
                if (self._ip_in_range(requested_ip)
                        and requested_ip not in self._leases
                        and requested_ip not in self._static_leases.values()):
                    return self._create_lease(client_id, requested_ip)

            for n in range(self._start_int, self._end_int + 1):
                ip = int_to_ip(n)
                if ip in self._static_leases.values():
                    continue   # rezervované pre statický lease
                if ip not in self._leases:
                    return self._create_lease(client_id, ip)

            return None

    def release(self, client_id: str) -> bool:
        client_id = normalize_mac(client_id)
        with self._lock:
            ip = self._client_map.pop(client_id, None)
            if ip:
                self._leases.pop(ip, None)
                return True
            return False

    def release_by_ip(self, ip: str) -> bool:
        with self._lock:
            lease = self._leases.pop(ip, None)
            if lease:
                self._client_map.pop(lease.client_id, None)
                return True
            return False

    def get_lease(self, client_id: str):
        client_id = normalize_mac(client_id)
        with self._lock:
            ip = self._client_map.get(client_id)
            return self._leases.get(ip) if ip else None

    def all_leases(self) -> list:
        with self._lock:
            self._expire_leases()
            return [l.to_dict() for l in self._leases.values()]

    def pool_stats(self) -> dict:
        with self._lock:
            self._expire_leases()
            total = self._end_int - self._start_int + 1
            used  = len(self._leases)
            return {
                "start_ip": self.start_ip,
                "end_ip":   self.end_ip,
                "total":    total,
                "used":     used,
                "free":     total - used,
                "static":   len(self._static_leases),
            }

    def update_range(self, start_ip: str, end_ip: str):
        with self._lock:
            self._start_int = ip_to_int(start_ip)
            self._end_int   = ip_to_int(end_ip)
            self.start_ip   = start_ip
            self.end_ip     = end_ip
            self._leases.clear()
            self._client_map.clear()

    # ------------------------------------------------------------------
    # Interné metódy
    # ------------------------------------------------------------------

    def _ip_in_range(self, ip: str) -> bool:
        n = ip_to_int(ip)
        return self._start_int <= n <= self._end_int

    def _create_lease(self, client_id: str, ip: str):
        lease = Lease(ip, client_id, self.default_lease_time)
        self._leases[ip] = lease
        self._client_map[client_id] = ip
        return lease

    def _expire_leases(self):
        expired_ips = [ip for ip, l in self._leases.items() if l.is_expired]
        for ip in expired_ips:
            lease = self._leases.pop(ip)
            self._client_map.pop(lease.client_id, None)