"""
DHCP_protocol.py – UDP DHCP server (port 67) bez externých knižníc.
Implementuje stavový automat: DISCOVER → OFFER → REQUEST → ACK/NAK
Parsuje a skladá binárne DHCP pakety podľa RFC 2131 a RFC 2132.
"""

import socket
import struct
import threading


# ---------------------------------------------------------------------------
# Konštanty
# ---------------------------------------------------------------------------

MAGIC_COOKIE = b"\x63\x82\x53\x63"   # 99.130.83.99

DHCPDISCOVER = 1
DHCPOFFER    = 2
DHCPREQUEST  = 3
DHCPDECLINE  = 4
DHCPACK      = 5
DHCPNAK      = 6
DHCPRELEASE  = 7
DHCPINFORM   = 8

MSG_NAMES = {
    1: "DISCOVER", 2: "OFFER",   3: "REQUEST", 4: "DECLINE",
    5: "ACK",      6: "NAK",     7: "RELEASE", 8: "INFORM",
}

OPT_SUBNET_MASK  = 1
OPT_ROUTER       = 3
OPT_DNS_SERVERS  = 6
OPT_HOSTNAME     = 12
OPT_DOMAIN_NAME  = 15
OPT_BROADCAST    = 28
OPT_NTP_SERVERS  = 42
OPT_REQUESTED_IP = 50
OPT_LEASE_TIME   = 51
OPT_MSG_TYPE     = 53
OPT_SERVER_ID    = 54
OPT_PARAM_LIST   = 55
OPT_CLIENT_ID    = 61
OPT_END          = 255
OPT_PAD          = 0


# ---------------------------------------------------------------------------
# Pomocné funkcie – IP / MAC konverzia
# ---------------------------------------------------------------------------

def ip_to_bytes(ip: str) -> bytes:
    return bytes(int(p) for p in ip.strip().split("."))


def bytes_to_ip(b: bytes) -> str:
    return ".".join(str(x) for x in b[:4])


def mac_to_str(b: bytes, hlen: int = 6) -> str:
    return ":".join(f"{x:02X}" for x in b[:hlen])


# ---------------------------------------------------------------------------
# Parsovanie DHCP paketu (RFC 2131)
# ---------------------------------------------------------------------------

def parse_dhcp_packet(data: bytes) -> dict | None:
    """
    BOOTP fixed header (236 bajtov):
      1B  op       – 1=BOOTREQUEST, 2=BOOTREPLY
      1B  htype    – typ HW adresy (1 = Ethernet)
      1B  hlen     – dĺžka HW adresy (6 pre Ethernet)
      1B  hops
      4B  xid      – transaction ID (náhodné číslo od klienta)
      2B  secs
      2B  flags
      4B  ciaddr   – client IP (ak ju klient má)
      4B  yiaddr   – your IP (adresa ktorú server prideľuje)
      4B  siaddr   – next server IP
      4B  giaddr   – relay agent IP
     16B  chaddr   – client HW adresa (MAC + padding)
     64B  sname    – server hostname
    128B  file     – boot file name
      4B  magic cookie
         options (TLV formát)
    """
    if len(data) < 240:
        return None
    try:
        op, htype, hlen, hops = struct.unpack("!BBBB", data[0:4])
        xid   = struct.unpack("!I", data[4:8])[0]
        secs  = struct.unpack("!H", data[8:10])[0]
        flags = struct.unpack("!H", data[10:12])[0]

        ciaddr = bytes_to_ip(data[12:16])
        yiaddr = bytes_to_ip(data[16:20])
        siaddr = bytes_to_ip(data[20:24])
        giaddr = bytes_to_ip(data[24:28])

        chaddr = data[28:44]
        mac    = mac_to_str(chaddr, min(hlen, 16))

        if data[236:240] != MAGIC_COOKIE:
            return None

        options = _parse_options(data[240:])

        return {
            "op": op, "htype": htype, "hlen": hlen, "hops": hops,
            "xid": xid, "secs": secs, "flags": flags,
            "ciaddr": ciaddr, "yiaddr": yiaddr,
            "siaddr": siaddr, "giaddr": giaddr,
            "mac": mac, "chaddr_raw": chaddr,
            "options": options,
            "msg_type":  options.get(OPT_MSG_TYPE, [None])[0],
            "client_id": _extract_client_id(options, mac),
        }
    except Exception:
        return None


def _parse_options(data: bytes) -> dict:
    """
    Parsuje DHCP options v TLV formáte (Type, Length, Value).
    Vráti slovník {kód: zoznam hodnôt}.
    """
    options = {}
    i = 0
    while i < len(data):
        code = data[i]
        i += 1
        if code == OPT_END:
            break
        if code == OPT_PAD:
            continue
        if i >= len(data):
            break
        length = data[i]
        i += 1
        value = data[i:i + length]
        i += length

        if code == OPT_MSG_TYPE and length == 1:
            options[code] = [value[0]]
        elif code == OPT_LEASE_TIME and length == 4:
            options[code] = [struct.unpack("!I", value)[0]]
        elif code in (OPT_SUBNET_MASK, OPT_ROUTER, OPT_DNS_SERVERS,
                      OPT_BROADCAST,   OPT_NTP_SERVERS,
                      OPT_SERVER_ID,   OPT_REQUESTED_IP):
            options[code] = [
                bytes_to_ip(value[j:j+4])
                for j in range(0, len(value), 4) if j + 4 <= len(value)
            ]
        else:
            options[code] = [value]
    return options


def _extract_client_id(options: dict, mac: str) -> str:
    """Preferujeme option 61 (client ID), inak použijeme MAC adresu."""
    raw = options.get(OPT_CLIENT_ID)
    if raw and isinstance(raw[0], bytes) and len(raw[0]) > 1:
        return mac_to_str(raw[0][1:], len(raw[0]) - 1)
    return mac


# ---------------------------------------------------------------------------
# Skladanie DHCP odpovede
# ---------------------------------------------------------------------------

def build_dhcp_packet(msg_type: int, xid: int, chaddr_raw: bytes,
                      yiaddr: str, server_ip: str, config,
                      lease_time: int) -> bytes:
    """
    Zostaví binárny DHCP paket pre OFFER, ACK alebo NAK.
    """
    yiaddr_b = ip_to_bytes(yiaddr) if yiaddr != "0.0.0.0" else b"\x00" * 4

    header  = struct.pack("!BBBBIHH", 2, 1, 6, 0, xid, 0, 0x8000)
    header += b"\x00" * 4                          # ciaddr
    header += yiaddr_b                              # yiaddr – pridelená IP
    header += ip_to_bytes(server_ip)               # siaddr
    header += b"\x00" * 4                          # giaddr
    header += chaddr_raw[:16].ljust(16, b"\x00")   # chaddr
    header += b"\x00" * 64                         # sname
    header += b"\x00" * 128                        # file
    header += MAGIC_COOKIE

    # --- Povinné options ---
    opts  = _opt(OPT_MSG_TYPE,  bytes([msg_type]))
    opts += _opt(OPT_SERVER_ID, ip_to_bytes(server_ip))

    if msg_type in (DHCPOFFER, DHCPACK):
        opts += _opt(OPT_LEASE_TIME,  struct.pack("!I", lease_time))
        opts += _opt(OPT_SUBNET_MASK, ip_to_bytes(config.subnet_mask))
        opts += _opt(OPT_ROUTER,      ip_to_bytes(config.gateway))
        dns_bytes = b"".join(ip_to_bytes(d) for d in config.dns_servers)
        opts += _opt(OPT_DNS_SERVERS, dns_bytes)

        # --- Voliteľné options z konfigurácie ---
        skip = {OPT_SUBNET_MASK, OPT_ROUTER, OPT_DNS_SERVERS,
                OPT_LEASE_TIME,  OPT_MSG_TYPE, OPT_SERVER_ID}
        for code_str, entry in config.all_options().items():
            code = int(code_str)
            if code in skip:
                continue
            raw = _encode_option_value(entry["value"])
            if raw:
                opts += _opt(code, raw)

    opts += bytes([OPT_END])
    return header + opts


def _opt(code: int, value: bytes) -> bytes:
    """Zostaví jeden TLV option."""
    return bytes([code, len(value)]) + value


def _encode_option_value(value) -> bytes | None:
    """Prevedie Python hodnotu na bajty pre DHCP option."""
    try:
        if isinstance(value, str):
            return value.encode("ascii")
        elif isinstance(value, list):
            result = b""
            for item in value:
                if isinstance(item, str) and item.count(".") == 3:
                    result += ip_to_bytes(item)
                elif isinstance(item, str):
                    result += item.encode("ascii")
            return result or None
        elif isinstance(value, int):
            return struct.pack("!I", value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# UDP DHCP Server
# ---------------------------------------------------------------------------

class DHCPUDPServer:
    """
    UDP server počúvajúci na zadanom porte (default 67).
    Spracúva DISCOVER, REQUEST, RELEASE a INFORM správy.
    Port 67 vyžaduje root/sudo práva.
    """

    def __init__(self, config, pool):
        self.config   = config
        self.pool     = pool
        self._sock    = None
        self._running = False

    def start(self, host: str = "0.0.0.0", port: int = 67):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.bind((host, port))
            self._running = True
            print(f"[DHCP UDP] Počúva na {host}:{port}/UDP")
        except PermissionError:
            print(f"[DHCP UDP] CHYBA: Port {port} vyžaduje root/sudo práva.")
            return
        except OSError as e:
            print(f"[DHCP UDP] CHYBA pri bindovaní {host}:{port} – {e}")
            return

        while self._running:
            try:
                self._sock.settimeout(1.0)
                data, addr = self._sock.recvfrom(4096)
                threading.Thread(
                    target=self._handle_packet,
                    args=(data, addr),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()

    def start_in_thread(self, host: str = "0.0.0.0", port: int = 67):
        t = threading.Thread(target=self.start, args=(host, port), daemon=True)
        t.start()
        return t

    # ------------------------------------------------------------------
    # Spracovanie paketov
    # ------------------------------------------------------------------

    def _handle_packet(self, data: bytes, addr):
        pkt = parse_dhcp_packet(data)
        if pkt is None:
            return
        msg_type = pkt["msg_type"]
        name = MSG_NAMES.get(msg_type, f"UNKNOWN({msg_type})")
        print(f"[DHCP UDP] {name} od {pkt['mac']} (xid={pkt['xid']:#010x})")

        if   msg_type == DHCPDISCOVER: self._handle_discover(pkt)
        elif msg_type == DHCPREQUEST:  self._handle_request(pkt)
        elif msg_type == DHCPRELEASE:  self._handle_release(pkt)
        elif msg_type == DHCPINFORM:   self._handle_inform(pkt)

    def _handle_discover(self, pkt: dict):
        """DISCOVER → pridelíme adresu z poolu → OFFER."""
        requested_ip = None
        req_opt = pkt["options"].get(OPT_REQUESTED_IP)
        if req_opt:
            requested_ip = req_opt[0]

        lease = self.pool.assign(pkt["client_id"], requested_ip)
        if lease is None:
            print(f"[DHCP UDP] OFFER: pool plný, nemôžem obsluhovať {pkt['client_id']}")
            return

        reply = build_dhcp_packet(
            msg_type   = DHCPOFFER,
            xid        = pkt["xid"],
            chaddr_raw = pkt["chaddr_raw"],
            yiaddr     = lease.ip,
            server_ip  = self.config.server_ip,
            config     = self.config,
            lease_time = lease.lease_time,
        )
        self._send_reply(reply, pkt)
        print(f"[DHCP UDP] OFFER → {lease.ip} pre {pkt['client_id']}")

    def _handle_request(self, pkt: dict):
        """REQUEST → potvrdíme alebo zamietname → ACK / NAK."""
        client_id = pkt["client_id"]

        req_opt = pkt["options"].get(OPT_REQUESTED_IP)
        if req_opt:
            requested_ip = req_opt[0]
        elif pkt["ciaddr"] != "0.0.0.0":
            requested_ip = pkt["ciaddr"]
        else:
            requested_ip = None

        # Klient si vybral iný server – uvoľníme rezerváciu
        server_id_opt = pkt["options"].get(OPT_SERVER_ID)
        if server_id_opt and server_id_opt[0] != self.config.server_ip:
            self.pool.release(client_id)
            return

        lease = self.pool.assign(client_id, requested_ip)

        if lease and (requested_ip is None or lease.ip == requested_ip):
            reply = build_dhcp_packet(
                msg_type   = DHCPACK,
                xid        = pkt["xid"],
                chaddr_raw = pkt["chaddr_raw"],
                yiaddr     = lease.ip,
                server_ip  = self.config.server_ip,
                config     = self.config,
                lease_time = lease.lease_time,
            )
            print(f"[DHCP UDP] ACK → {lease.ip} pre {client_id}")
        else:
            reply = build_dhcp_packet(
                msg_type   = DHCPNAK,
                xid        = pkt["xid"],
                chaddr_raw = pkt["chaddr_raw"],
                yiaddr     = "0.0.0.0",
                server_ip  = self.config.server_ip,
                config     = self.config,
                lease_time = 0,
            )
            print(f"[DHCP UDP] NAK pre {client_id} (IP {requested_ip} nedostupná)")

        self._send_reply(reply, pkt)

    def _handle_release(self, pkt: dict):
        """RELEASE – klient vracia adresu späť do poolu."""
        self.pool.release(pkt["client_id"])
        print(f"[DHCP UDP] RELEASE od {pkt['client_id']} ({pkt['ciaddr']})")

    def _handle_inform(self, pkt: dict):
        """INFORM – klient má IP, žiada len sieťové parametre (bez lease)."""
        reply = build_dhcp_packet(
            msg_type   = DHCPACK,
            xid        = pkt["xid"],
            chaddr_raw = pkt["chaddr_raw"],
            yiaddr     = "0.0.0.0",
            server_ip  = self.config.server_ip,
            config     = self.config,
            lease_time = 0,
        )
        self._send_reply(reply, pkt)
        print(f"[DHCP UDP] ACK(INFORM) pre {pkt['mac']}")

    # ------------------------------------------------------------------
    # Odoslanie odpovede
    # ------------------------------------------------------------------

    def _send_reply(self, packet: bytes, req: dict):
        """
        Routing odpovede podľa RFC 2131:
          giaddr != 0  → relay agent   (unicast na giaddr:67)
          ciaddr != 0  → unicast       (priamo klientovi na port 68)
          inak         → broadcast     (255.255.255.255:68)
        """
        if req["giaddr"] != "0.0.0.0":
            dest = (req["giaddr"], 67)
        elif req["ciaddr"] != "0.0.0.0":
            dest = (req["ciaddr"], 68)
        else:
            dest = ("255.255.255.255", 68)

        try:
            self._sock.sendto(packet, dest)
        except Exception as e:
            print(f"[DHCP UDP] Chyba pri odosielaní na {dest}: {e}")