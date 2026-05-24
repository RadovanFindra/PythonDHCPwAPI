"""
pool.py – Správa adresného poolu a lease záznamov.
Žiadne externé knižnice – iba štandardné moduly.
"""

import time
import threading


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


class Lease:
    def __init__(self, ip: str, client_id: str, lease_time: int):
        self.ip = ip
        self.client_id = client_id
        self.assigned_at = time.time()
        self.lease_time = lease_time  # sekundy

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
    """
    Spravuje rozsah IP adries, prideľuje a uvoľňuje adresy.
    Thread-safe pomocou zámku.
    """

    def __init__(self, start_ip: str, end_ip: str, default_lease_time: int = 3600):
        self._lock = threading.Lock()
        self.start_ip = start_ip
        self.end_ip = end_ip
        self.default_lease_time = default_lease_time

        self._start_int = ip_to_int(start_ip)
        self._end_int = ip_to_int(end_ip)

        if self._start_int > self._end_int:
            raise ValueError("start_ip musí byť menšia alebo rovnaká ako end_ip")

        # ip -> Lease
        self._leases: dict[str, Lease] = {}
        # client_id -> ip  (pre rýchle vyhľadávanie)
        self._client_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Verejné metódy
    # ------------------------------------------------------------------

    def assign(self, client_id: str, requested_ip: str = None):
        """
        Pridelí IP adresu klientovi.
        Ak klient už má platnú lease, obnoví ju.
        Ak požaduje konkrétnu voľnú IP, pridelí ju.
        Inak pridelí prvú dostupnú.
        Vráti Lease alebo None ak nie je voľná adresa.
        """
        with self._lock:
            self._expire_leases()

            # Klient už má pridelenú adresu – obnov lease
            if client_id in self._client_map:
                existing_ip = self._client_map[client_id]
                lease = self._leases.get(existing_ip)
                if lease and not lease.is_expired:
                    lease.renew()
                    return lease

            # Požadovaná konkrétna IP
            if requested_ip and validate_ip(requested_ip):
                if self._ip_in_range(requested_ip) and requested_ip not in self._leases:
                    return self._create_lease(client_id, requested_ip)

            # Prvá voľná IP
            for n in range(self._start_int, self._end_int + 1):
                ip = int_to_ip(n)
                if ip not in self._leases:
                    return self._create_lease(client_id, ip)

            return None  # Pool je plný

    def release(self, client_id: str) -> bool:
        """Uvoľní IP adresu klienta. Vráti True ak existovala."""
        with self._lock:
            ip = self._client_map.pop(client_id, None)
            if ip:
                self._leases.pop(ip, None)
                return True
            return False

    def release_by_ip(self, ip: str) -> bool:
        """Uvoľní lease podľa IP adresy."""
        with self._lock:
            lease = self._leases.pop(ip, None)
            if lease:
                self._client_map.pop(lease.client_id, None)
                return True
            return False

    def get_lease(self, client_id: str):
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
            used = len(self._leases)
            return {
                "start_ip": self.start_ip,
                "end_ip": self.end_ip,
                "total": total,
                "used": used,
                "free": total - used,
            }

    def update_range(self, start_ip: str, end_ip: str):
        """Zmení rozsah poolu (vymaže existujúce lease)."""
        with self._lock:
            self._start_int = ip_to_int(start_ip)
            self._end_int = ip_to_int(end_ip)
            self.start_ip = start_ip
            self.end_ip = end_ip
            self._leases.clear()
            self._client_map.clear()

    # ------------------------------------------------------------------
    # Interné pomocné metódy
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
        """Odstráni expirované lease. Volať iba pod zámkom."""
        expired_ips = [ip for ip, l in self._leases.items() if l.is_expired]
        for ip in expired_ips:
            lease = self._leases.pop(ip)
            self._client_map.pop(lease.client_id, None)