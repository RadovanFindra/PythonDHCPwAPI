# PythonDHCPwAPI

Minimal Python DHCP core with a built-in HTTP API.

## What is implemented

- In-memory DHCP lease allocation by MAC address
- DHCP options export, including advanced **option 6 (DNS servers)**
- HTTP API endpoints:
  - `GET /health`
  - `GET /config`
  - `GET /leases`
  - `POST /leases` with `{"mac": "..."}`
  - `PUT /options/6` with `{"dns_servers": ["8.8.8.8", "1.1.1.1"]}`

## Run

```bash
python dhcp_server_api.py
```

Server starts on `http://127.0.0.1:8000`.
