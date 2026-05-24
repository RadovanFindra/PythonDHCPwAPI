"""
test_client.py – Testovací klient REST API (štandardná knižnica urllib).
Demonštruje všetky dostupné endpointy.
Spustenie: python test_client.py [--port 8080]
"""

import urllib.request
import urllib.error
import json
import sys


BASE_URL = "http://127.0.0.1:8080"


def request(method: str, path: str, body: dict = None):
    """Pošle HTTP požiadavku, vráti (status_code, parsed_body)."""
    url = BASE_URL + path
    data = json.dumps(body).encode("utf-8") if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def sep(title: str):
    print(f"\n{'─' * 50}\n  {title}\n{'─' * 50}")


def show(status, body):
    print(f"  HTTP {status}")
    print(json.dumps(body, indent=2, ensure_ascii=False))


def main():
    global BASE_URL
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--port" and i + 2 <= len(sys.argv) - 1:
            BASE_URL = f"http://127.0.0.1:{sys.argv[i + 2]}"

    sep("GET /health")
    show(*request("GET", "/health"))

    sep("GET /config")
    show(*request("GET", "/config"))

    sep("POST /config – zmena DNS a lease time")
    show(*request("POST", "/config", {
        "dns_servers": ["1.1.1.1", "9.9.9.9"],
        "default_lease_time": 7200,
    }))

    sep("GET /pool")
    show(*request("GET", "/pool"))

    sep("POST /lease/assign – klient AA:BB:CC:DD:EE:01")
    show(*request("POST", "/lease/assign", {"client_id": "AA:BB:CC:DD:EE:01"}))

    sep("POST /lease/assign – klient 02, požadovaná IP")
    show(*request("POST", "/lease/assign", {
        "client_id": "AA:BB:CC:DD:EE:02",
        "requested_ip": "192.168.1.110",
    }))

    sep("POST /lease/assign – obnova lease klienta 01")
    show(*request("POST", "/lease/assign", {"client_id": "AA:BB:CC:DD:EE:01"}))

    sep("GET /leases")
    show(*request("GET", "/leases"))

    sep("GET /options")
    show(*request("GET", "/options"))

    sep("POST /options – domain name (option 15)")
    show(*request("POST", "/options", {"code": 15, "value": "example.local"}))

    sep("POST /options – NTP server (option 42)")
    show(*request("POST", "/options", {"code": 42, "value": ["192.168.1.2"]}))

    sep("POST /options – TFTP server (option 66)")
    show(*request("POST", "/options", {"code": 66, "value": "192.168.1.5"}))

    sep("POST /options – boot file (option 67)")
    show(*request("POST", "/options", {"code": 67, "value": "pxelinux.0"}))

    sep("GET /options – po nastavení")
    show(*request("GET", "/options"))

    sep("DELETE /options/66 – zmazanie TFTP")
    show(*request("DELETE", "/options/66"))

    sep("POST /lease/assign – chýba client_id (chybový stav)")
    show(*request("POST", "/lease/assign", {}))

    sep("POST /config – neplatná IP (chybový stav)")
    show(*request("POST", "/config", {"gateway": "999.999.999.999"}))

    sep("POST /lease/release – podľa client_id")
    show(*request("POST", "/lease/release", {"client_id": "AA:BB:CC:DD:EE:01"}))

    sep("POST /lease/release – podľa IP")
    show(*request("POST", "/lease/release", {"ip": "192.168.1.110"}))

    sep("GET /leases – po uvoľnení")
    show(*request("GET", "/leases"))

    sep("GET /pool – finálne štatistiky")
    show(*request("GET", "/pool"))


if __name__ == "__main__":
    main()