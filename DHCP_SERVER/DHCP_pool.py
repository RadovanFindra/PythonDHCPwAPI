"""
DHCP_pool.py
============
Správa adresného poolu a lease záznamov DHCP servera.

Zodpovedá za:
  - dynamické prideľovanie IP adries z nakonfigurovaného rozsahu,
  - správu aktívnych lease záznamov vrátane expirácie,
  - správu statických lease (MAC → IP) s perzistenciou do JSON súboru,
  - thread-safe operácie pomocou zámku.

Statické lease majú vždy prednosť pred dynamickým prideľovaním.
Pri odstraňovaní záznamu sa zoznam posúva – ID sa prepočítajú.
"""

import time
import threading
import json
import os


def ip_to_int(ip: str) -> int:
    """Prevedie IPv4 adresu na 32-bitové celé číslo."""
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
    """Prevedie 32-bitové celé číslo na IPv4 adresu."""
    return ".".join([str((n >> (8 * i)) & 0xFF) for i in reversed(range(4))])


def validate_ip(ip: str) -> bool:
    """Overí či je reťazec platnou IPv4 adresou."""
    try:
        ip_to_int(ip)
        return True
    except (ValueError, AttributeError):
        return False


def normalize_mac(mac: str) -> str:
    """
    Normalizuje MAC adresu na formát s veľkými písmenami a dvojbodkami.

    Podporuje formáty: AA:BB:CC:DD:EE:FF, aa-bb-cc-dd-ee-ff, AABBCCDDEEFF.
    """
    mac = mac.strip().upper().replace("-", ":").replace(".", ":")
    clean = mac.replace(":", "")
    if len(clean) == 12 and ":" not in mac:
        mac = ":".join(clean[i:i+2] for i in range(0, 12, 2))
    return mac


class Lease:
    """
    Reprezentuje jeden DHCP lease záznam.

    Uchováva pridelenú IP adresu, identifikátor klienta (MAC),
    čas pridelenia a dobu platnosti. Poskytuje vlastnosti na
    kontrolu expirácie a metódu na obnovenie lease.
    """

    def __init__(self, ip: str, client_id: str, lease_time: int):
        self.ip          = ip
        self.client_id   = client_id
        self.assigned_at = time.time()
        self.lease_time  = lease_time

    @property
    def expires_at(self) -> float:
        """Unix timestamp kedy lease expiruje."""
        return self.assigned_at + self.lease_time

    @property
    def is_expired(self) -> bool:
        """Vráti True ak lease už vypršal."""
        return time.time() > self.expires_at

    def renew(self, lease_time: int = None):
        """Obnoví lease – resetuje čas pridelenia, voliteľne aj dobu platnosti."""
        self.assigned_at = time.time()
        if lease_time is not None:
            self.lease_time = lease_time

    def to_dict(self) -> dict:
        """Vráti lease ako slovník vhodný na serializáciu do JSON."""
        return {
            "ip":          self.ip,
            "client_id":   self.client_id,
            "assigned_at": self.assigned_at,
            "lease_time":  self.lease_time,
            "expires_at":  self.expires_at,
            "expired":     self.is_expired,
        }


class DHCPPool:
    """
    Správca adresného poolu a lease záznamov.

    Spravuje dynamické aj statické lease. Statické lease sa načítavajú
    zo JSON súboru pri štarte a ukladajú sa po každej zmene.
    Všetky operácie sú thread-safe.

    Args:
        start_ip:           Začiatok rozsahu dynamických adries.
        end_ip:             Koniec rozsahu dynamických adries.
        default_lease_time: Predvolená doba platnosti lease v sekundách.
        static_leases_file: Cesta k JSON súboru so statickými lease.
    """

    def __init__(self, start_ip: str, end_ip: str,
                 default_lease_time: int = 3600,
                 static_leases_file: str = "static_leases.json"):
        self._lock              = threading.Lock()
        self.start_ip           = start_ip
        self.end_ip             = end_ip
        self.default_lease_time = default_lease_time
        self._start_int         = ip_to_int(start_ip)
        self._end_int           = ip_to_int(end_ip)

        if self._start_int > self._end_int:
            raise ValueError("start_ip musí byť menšia alebo rovnaká ako end_ip")

        self._leases:        dict = {}
        self._client_map:    dict = {}
        self._static_file         = static_leases_file
        self._static_leases: list = []
        self._load_static_leases()

    def _load_static_leases(self):
        """
        Načíta statické lease zo súboru JSON pri štarte.

        Podporuje nový formát (zoznam objektov) aj starý formát (slovník MAC→IP).
        Starý formát sa automaticky konvertuje a uloží v novom formáte.
        """
        if not os.path.exists(self._static_file):
            print(f"[Pool] Súbor {self._static_file} neexistuje – začínam prázdny.")
            return
        try:
            with open(self._static_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._static_leases = [
                    {"mac": normalize_mac(e["mac"]), "ip": e["ip"]}
                    for e in data if "mac" in e and "ip" in e
                ]
            elif isinstance(data, dict):
                self._static_leases = [
                    {"mac": normalize_mac(mac), "ip": ip}
                    for mac, ip in data.items()
                ]
                self._save_static_leases()
                print(f"[Pool] Starý formát konvertovaný na zoznam.")
            print(f"[Pool] Načítaných {len(self._static_leases)} statických lease z {self._static_file}")
            for i, e in enumerate(self._static_leases, 1):
                print(f"[Pool]   {i}. {e['mac']} → {e['ip']}")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[Pool] Chyba pri načítaní {self._static_file}: {e}")

    def _save_static_leases(self):
        """
        Uloží statické lease do JSON súboru.

        Používa atomickú operáciu (dočasný súbor + premenúvanie)
        aby sa predišlo poškodeniu súboru pri výpadku.
        """
        try:
            tmp = self._static_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._static_leases, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._static_file)
        except OSError as e:
            print(f"[Pool] Chyba pri ukladaní {self._static_file}: {e}")

    def add_static(self, mac: str, ip: str) -> str | None:
        """
        Pridá statický lease (MAC → IP) a uloží do súboru.

        Args:
            mac: MAC adresa klienta (ľubovoľný formát).
            ip:  IP adresa ktorá sa má klientovi vždy prideliť.

        Returns:
            Chybový reťazec ak validácia zlyhá, inak None.
        """
        mac = normalize_mac(mac)
        if not validate_ip(ip):
            return f"Neplatná IP adresa: {ip}"
        if not self._ip_in_range(ip):
            return f"IP {ip} nie je v rozsahu poolu ({self.start_ip} – {self.end_ip})"
        with self._lock:
            for entry in self._static_leases:
                if entry["ip"] == ip and entry["mac"] != mac:
                    return f"IP {ip} je už priradená MAC {entry['mac']}"
                if entry["mac"] == mac:
                    return f"MAC {mac} už má statický lease → {entry['ip']}"
            self._static_leases.append({"mac": mac, "ip": ip})
            self._save_static_leases()
        print(f"[Pool] Statický lease pridaný #{len(self._static_leases)}: {mac} → {ip}")
        return None

    def remove_static(self, lease_id: int) -> bool:
        """
        Odstráni statický lease podľa poradového čísla (1-based).

        Po odstránení sa zoznam posunie – ID ostatných záznamov sa zmenia.

        Returns:
            True ak záznam existoval a bol odstránený, inak False.
        """
        idx = lease_id - 1
        with self._lock:
            if idx < 0 or idx >= len(self._static_leases):
                return False
            entry = self._static_leases.pop(idx)
            self._save_static_leases()
        print(f"[Pool] Statický lease #{lease_id} odstránený: {entry['mac']} → {entry['ip']}")
        return True

    def all_static_leases(self) -> list:
        """Vráti zoznam statických lease s poradovými číslami (1-based)."""
        with self._lock:
            return [
                {"id": i + 1, "mac": e["mac"], "ip": e["ip"]}
                for i, e in enumerate(self._static_leases)
            ]

    def _get_static_ip(self, client_id: str) -> str | None:
        """Vyhľadá staticky pridelenú IP podľa MAC adresy klienta."""
        for entry in self._static_leases:
            if entry["mac"] == client_id:
                return entry["ip"]
        return None

    def assign(self, client_id: str, requested_ip: str = None):
        """
        Pridelí IP adresu klientovi.

        Poradie prednosti:
          1. Statický lease (MAC → pevná IP)
          2. Existujúci aktívny dynamický lease (obnova)
          3. Požadovaná IP klientom (option 50)
          4. Prvá voľná IP z dynamického rozsahu

        IP adresy rezervované pre statické lease sú preskočené
        pri dynamickom prideľovaní.

        Returns:
            Objekt Lease pri úspechu, None ak pool je plný.
        """
        client_id = normalize_mac(client_id)
        with self._lock:
            self._expire_leases()

            static_ip = self._get_static_ip(client_id)
            if static_ip:
                old_ip = self._client_map.get(client_id)
                if old_ip and old_ip != static_ip:
                    self._leases.pop(old_ip, None)
                    self._client_map.pop(client_id, None)
                lease = self._leases.get(static_ip)
                if lease and lease.client_id == client_id:
                    lease.renew()
                    return lease
                if static_ip in self._leases:
                    old_lease = self._leases.pop(static_ip)
                    self._client_map.pop(old_lease.client_id, None)
                return self._create_lease(client_id, static_ip)

            if client_id in self._client_map:
                existing_ip = self._client_map[client_id]
                lease = self._leases.get(existing_ip)
                if lease and not lease.is_expired:
                    lease.renew()
                    return lease

            reserved = {e["ip"] for e in self._static_leases}

            if requested_ip and validate_ip(requested_ip):
                if (self._ip_in_range(requested_ip)
                        and requested_ip not in self._leases
                        and requested_ip not in reserved):
                    return self._create_lease(client_id, requested_ip)

            for n in range(self._start_int, self._end_int + 1):
                ip = int_to_ip(n)
                if ip in reserved or ip in self._leases:
                    continue
                return self._create_lease(client_id, ip)

            return None

    def release(self, client_id: str) -> bool:
        """
        Uvoľní lease podľa MAC adresy klienta.

        Returns:
            True ak lease existoval a bol uvoľnený, inak False.
        """
        client_id = normalize_mac(client_id)
        with self._lock:
            ip = self._client_map.pop(client_id, None)
            if ip:
                self._leases.pop(ip, None)
                return True
            return False

    def release_by_id(self, lease_id: int) -> bool:
        """
        Uvoľní lease podľa poradového čísla v aktuálnom zozname (1-based).

        Returns:
            True ak lease existoval a bol uvoľnený, inak False.
        """
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
        """
        Uvoľní lease podľa IP adresy.

        Returns:
            True ak lease existoval a bol uvoľnený, inak False.
        """
        with self._lock:
            lease = self._leases.pop(ip, None)
            if lease:
                self._client_map.pop(lease.client_id, None)
                return True
            return False

    def get_lease(self, client_id: str):
        """Vráti aktívny lease pre daného klienta alebo None."""
        client_id = normalize_mac(client_id)
        with self._lock:
            ip = self._client_map.get(client_id)
            return self._leases.get(ip) if ip else None

    def all_leases(self) -> list:
        """
        Vráti zoznam všetkých aktívnych lease s poradovými číslami (1-based).

        Pred vrátením zoznamu automaticky vyčistí expirované záznamy.
        """
        with self._lock:
            self._expire_leases()
            return [
                {"id": i + 1, **lease.to_dict()}
                for i, lease in enumerate(self._leases.values())
            ]

    def pool_stats(self) -> dict:
        """Vráti štatistiky poolu – celkový počet, obsadené, voľné adresy."""
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
        """
        Zmení rozsah dynamického poolu a vyčistí všetky aktívne lease.

        Volá sa pri zmene konfigurácie cez REST API.
        """
        with self._lock:
            self._start_int = ip_to_int(start_ip)
            self._end_int   = ip_to_int(end_ip)
            self.start_ip   = start_ip
            self.end_ip     = end_ip
            self._leases.clear()
            self._client_map.clear()

    def _ip_in_range(self, ip: str) -> bool:
        """Overí či IP adresa patrí do nakonfigurovaného rozsahu poolu."""
        return self._start_int <= ip_to_int(ip) <= self._end_int

    def _create_lease(self, client_id: str, ip: str) -> Lease:
        """Vytvorí nový lease záznam a zaregistruje ho v interných štruktúrach."""
        lease = Lease(ip, client_id, self.default_lease_time)
        self._leases[ip]            = lease
        self._client_map[client_id] = ip
        return lease

    def _expire_leases(self):
        """Odstráni všetky expirované lease záznamy. Volať pod zámkom."""
        expired = [ip for ip, l in self._leases.items() if l.is_expired]
        for ip in expired:
            lease = self._leases.pop(ip)
            self._client_map.pop(lease.client_id, None)