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
    """Normalizuje MAC na veľké písmená s dvojbodkami."""
    mac = mac.strip().upper()
    mac = mac.replace("-", ":").replace(".", ":")
    # Prípad keď MAC nemá oddeľovače: AABBCCDDEEFF → AA:BB:CC:DD:EE:FF
    clean = mac.replace(":", "")
    if len(clean) == 12 and ":" not in mac:
        mac = ":".join(clean[i:i+2] for i in range(0, 12, 2))
    return mac


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
        self._lease_counter: int = 0

        if self._start_int > self._end_int:
            raise ValueError("start_ip musí byť menšia alebo rovnaká ako end_ip")

        self._leases: dict = {}
        self._client_map: dict = {}

        # Statické lease – MAC -> IP
        self._static_file = static_leases_file
        self._static_leases: list = []
        self._load_static_leases()

    # ------------------------------------------------------------------
    # Persistencia statických lease
    # ------------------------------------------------------------------

    def _load_static_leases(self):
        if not os.path.exists(self._static_file):
            print(f"[Pool] Súbor {self._static_file} neexistuje – začínam prázdny.")
            return
        try:
            with open(self._static_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):  # ← musí byť list
                self._static_leases = [
                    {"mac": normalize_mac(e["mac"]), "ip": e["ip"]}
                    for e in data if "mac" in e and "ip" in e
                ]
            elif isinstance(data, dict):  # ← starý formát – automatická konverzia
                self._static_leases = [
                    {"mac": normalize_mac(mac), "ip": ip}
                    for mac, ip in data.items()
                ]
                self._save_static_leases()  # ← uložíme v novom formáte
                print(f"[Pool] Konvertovaný starý formát → nový zoznam")
            print(f"[Pool] Načítaných {len(self._static_leases)} statických lease")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[Pool] Chyba pri načítaní {self._static_file}: {e}")

    def _save_static_leases(self):
        try:
            tmp_file = self._static_file + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self._static_leases, f, indent=2, ensure_ascii=False)
            import os
            os.replace(tmp_file, self._static_file) # Atomická operácia
        except OSError as e:
            print(f"[Pool] Chyba pri ukladaní: {e}")

    # ------------------------------------------------------------------
    # Správa statických lease
    # ------------------------------------------------------------------

    def add_static(self, mac: str, ip: str) -> str | None:
        mac = normalize_mac(mac)
        if not validate_ip(ip):
            return f"Neplatná IP adresa: {ip}"
        if not self._ip_in_range(ip):
            return f"IP {ip} nie je v rozsahu poolu ({self.start_ip} – {self.end_ip})"
        
        # --- ZAMKNUTIE PRE THREAD SAFETY ---
        with self._lock:
            # Kontrola duplicity
            for entry in self._static_leases:
                if entry["ip"] == ip and entry["mac"] != mac:
                    return f"IP {ip} je už priradená MAC {entry['mac']}"
                if entry["mac"] == mac:
                    return f"MAC {mac} už má statický lease → {entry['ip']}"
            
            self._static_leases.append({"mac": mac, "ip": ip})
            self._save_static_leases()
        # -----------------------------------

        print(f"[Pool] Statický lease pridaný #{len(self._static_leases)}: {mac} → {ip}")
        return None

    def remove_static(self, id: int) -> bool:
        """Odstráni statický lease podľa poradového čísla (1-based). Zoznam sa posunie."""
        idx = id - 1
        
        # --- ZAMKNUTIE PRE THREAD SAFETY ---
        with self._lock:
            if idx < 0 or idx >= len(self._static_leases):
                return False
            entry = self._static_leases.pop(idx)   # ← posunie zoznam automaticky
            self._save_static_leases()
        # -----------------------------------

        print(f"[Pool] Statický lease #{id} odstránený: {entry['mac']} → {entry['ip']}")
        return True

    def all_static_leases(self) -> list:
        """Vráti zoznam so ID začínajúcim od 1."""
        # --- ZAMKNUTIE PRE THREAD SAFETY ---
        with self._lock:
            return [
                {"id": i + 1, "mac": e["mac"], "ip": e["ip"]}
                for i, e in enumerate(self._static_leases)
            ]
        # -----------------------------------
        
    def _get_static_ip(self, client_id: str) -> str | None:
        """Vyhľadá statickú IP podľa MAC adresy."""
        for entry in self._static_leases:
            if entry["mac"] == client_id:
                return entry["ip"]
        return None    

    # ------------------------------------------------------------------
    # Prideľovanie adries
    # ------------------------------------------------------------------

    def assign(self, client_id: str, requested_ip: str = None):
        client_id = normalize_mac(client_id)
        with self._lock:
            self._expire_leases()

            # 1. KONTROLA STATICKÉHO LEASE
            static_ip = self._get_static_ip(client_id)
            if static_ip:
                # --- OCHRANA PROTI ZOMBIFIKÁCII / IP LEAKU ---
                # Ak mal klient doteraz pridelenú inú IP (napr. starú dynamickú), 
                # musíme ju kompletne vymazať, aby nezostala visieť v systéme.
                old_ip = self._client_map.get(client_id)
                if old_ip and old_ip != static_ip:
                    self._leases.pop(old_ip, None)
                    self._client_map.pop(client_id, None)
                # ----------------------------------------------

                lease = self._leases.get(static_ip)
                if lease and lease.client_id == client_id:
                    lease.renew()
                    return lease
                
                # Ak túto statickú IP držal niekto iný (napr. starý expirovaný lease), uvoľníme ju
                if static_ip in self._leases:
                    old_lease = self._leases.pop(static_ip)
                    self._client_map.pop(old_lease.client_id, None)
                    
                return self._create_lease(client_id, static_ip)

            # 2. EXISTUJÚCI AKTÍVNY DYNAMICKÝ LEASE
            if client_id in self._client_map:
                existing_ip = self._client_map[client_id]
                lease = self._leases.get(existing_ip)
                if lease and not lease.is_expired:
                    lease.renew()
                    return lease

            # Množina všetkých IP adries, ktoré sú rezervované staticky
            reserved = {e["ip"] for e in self._static_leases}

            # 3. VYHOVENIE POŽIADAVKE KLIENTA (Requested IP Option 50)
            if requested_ip and validate_ip(requested_ip):
                if (self._ip_in_range(requested_ip)
                        and requested_ip not in self._leases
                        and requested_ip not in reserved):
                    return self._create_lease(client_id, requested_ip)

            # 4. PRIDELENIE PRVEJ VOĽNEJ DYNAMICKEJ IP Z POOLU
            for n in range(self._start_int, self._end_int + 1):
                ip = int_to_ip(n)
                if ip in reserved or ip in self._leases:
                    continue
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
    
    def release_by_id(self, lease_id: int) -> bool:
        with self._lock:
            leases_list = list(self._leases.items())
            idx = lease_id - 1
            if idx < 0 or idx >= len(leases_list):
                return False
            ip, lease = leases_list[idx]
            self._leases.pop(ip)
            self._client_map.pop(lease.client_id, None)
            return True 

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
            return [
                {"id": i + 1, **lease.to_dict()}
                for i, lease in enumerate(self._leases.values())
            ]
        
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