"""
REST_API.py – Vlastný HTTP/REST server bez externých frameworkov.
Používa iba socket a threading zo štandardnej knižnice Pythonu.
"""

import socket
import threading
import json
import time

STATUS_TEXTS = {
    200: "OK", 201: "Created", 204: "No Content",
    400: "Bad Request", 404: "Not Found",
    405: "Method Not Allowed", 409: "Conflict",
    500: "Internal Server Error",
}


def parse_request(raw: bytes):
    try:
        if b"\r\n\r\n" in raw:
            header_part, body_bytes = raw.split(b"\r\n\r\n", 1)
        else:
            header_part = raw
            body_bytes = b""
        lines = header_part.decode("utf-8", errors="replace").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return None
        method = parts[0].upper()
        full_path = parts[1]
        path, query_string = (full_path.split("?", 1) if "?" in full_path else (full_path, ""))
        headers = {}
        for line in lines[1:]:
            if ": " in line:
                k, v = line.split(": ", 1)
                headers[k.lower()] = v.strip()
        body = None
        if body_bytes:
            cl = int(headers.get("content-length", len(body_bytes)))
            body_bytes = body_bytes[:cl]
            if body_bytes:
                try:
                    body = json.loads(body_bytes.decode("utf-8"))
                except json.JSONDecodeError:
                    body = None
        return {
            "method": method,
            "path": path.rstrip("/") or "/",
            "query": query_string,
            "headers": headers,
            "body": body,
        }
    except Exception:
        return None


def build_response(status: int, body=None, extra_headers: dict = None) -> bytes:
    status_text = STATUS_TEXTS.get(status, "Unknown")
    if body is None:
        body_bytes = b""
        content_type = "text/plain"
    elif isinstance(body, (dict, list)):
        body_bytes = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        content_type = "application/json"
    else:
        body_bytes = str(body).encode("utf-8")
        content_type = "text/plain"
    headers = {
        "Content-Type": content_type + "; charset=utf-8",
        "Content-Length": str(len(body_bytes)),
        "Connection": "close",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    if extra_headers:
        headers.update(extra_headers)
    header_lines = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
    return (f"HTTP/1.1 {status} {status_text}\r\n" + header_lines + "\r\n\r\n").encode("utf-8") + body_bytes


class Router:
    def __init__(self):
        self._routes = []

    def add(self, method: str, path: str, handler):
        parts = [p for p in path.split("/") if p]
        self._routes.append((method.upper(), parts, handler))

    def resolve(self, method: str, path: str):
        path_parts = [p for p in path.split("/") if p]
        for route_method, pattern_parts, handler in self._routes:
            if route_method != method.upper():
                continue
            if len(pattern_parts) != len(path_parts):
                continue
            params = {}
            match = True
            for pp, rp in zip(path_parts, pattern_parts):
                if rp.startswith("<") and rp.endswith(">"):
                    params[rp[1:-1]] = pp
                elif pp != rp:
                    match = False
                    break
            if match:
                return handler, params
        return None, None


class DHCPRestAPI:
    def __init__(self, config, pool):
        self.config = config
        self.pool = pool
        self._router = Router()
        self._server_socket = None
        self._running = False
        self._start_time = time.time()
        self._register_routes()

    def _register_routes(self):
        r = self._router
        r.add("GET",    "/health",                  self._health)
        r.add("GET",    "/config",                  self._get_config)
        r.add("POST",   "/config",                  self._post_config)
        r.add("GET",    "/leases",                  self._get_leases)
        r.add("GET",    "/pool",                    self._get_pool)
        r.add("POST",   "/lease/assign",            self._assign_lease)
        r.add("POST",   "/lease/release",           self._release_lease)
        r.add("GET",    "/options",                 self._get_options)
        r.add("POST",   "/options",                 self._post_options)
        r.add("DELETE", "/options/<code>",          self._delete_option)
        r.add("GET",    "/leases/static",           self._get_static)
        r.add("POST",   "/leases/static",           self._post_static)
        r.add("DELETE", "/leases/static/<id>", self._delete_static)

    def start(self, host: str = "0.0.0.0", port: int = None):
        port = port or self.config.server_port
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((host, port))
        self._server_socket.listen(10)
        self._running = True
        print(f"[REST API] Počúva na {host}:{port}")
        try:
            while self._running:
                try:
                    client_sock, addr = self._server_socket.accept()
                    threading.Thread(target=self._handle_client,
                                     args=(client_sock, addr), daemon=True).start()
                except OSError:
                    break
        finally:
            self._server_socket.close()

    def stop(self):
        self._running = False
        if self._server_socket:
            self._server_socket.close()

    def start_in_thread(self, host="0.0.0.0", port=None):
        t = threading.Thread(target=self.start, args=(host, port), daemon=True)
        t.start()
        return t

    def _handle_client(self, sock, addr):
        try:
            raw = b""
            sock.settimeout(5.0)
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
                if b"\r\n\r\n" in raw:
                    hp = raw.split(b"\r\n\r\n")[0]
                    cl = 0
                    for line in hp.decode("utf-8", errors="replace").split("\r\n")[1:]:
                        if line.lower().startswith("content-length:"):
                            cl = int(line.split(":", 1)[1].strip())
                    if len(raw) - len(hp) - 4 >= cl:
                        break
            if not raw:
                return
            req = parse_request(raw)
            if req is None:
                sock.sendall(build_response(400, {"error": "Neplatná HTTP požiadavka"}))
                return
            if req["method"] == "OPTIONS":
                sock.sendall(build_response(204))
                return
            sock.sendall(self._dispatch(req))
        except Exception as e:
            try:
                sock.sendall(build_response(500, {"error": str(e)}))
            except Exception:
                pass
        finally:
            sock.close()

    def _dispatch(self, req):
        handler, params = self._router.resolve(req["method"], req["path"])
        if handler is None:
            _, check = self._router.resolve("GET", req["path"])
            if check is not None:
                return build_response(405, {"error": "Metóda nie je povolená"})
            return build_response(404, {"error": f"Endpoint nenájdený: {req['path']}"})
        try:
            return handler(req, params or {})
        except Exception as e:
            return build_response(500, {"error": f"Interná chyba: {str(e)}"})

    # ------------------------------------------------------------------
    # Handlery – konfigurácia, pool, lease
    # ------------------------------------------------------------------

    def _health(self, req, params):
        return build_response(200, {
            "status": "ok",
            "uptime_seconds": int(time.time() - self._start_time),
            "server_ip": self.config.server_ip,
            "pool": self.pool.pool_stats(),
        })

    def _get_config(self, req, params):
        return build_response(200, self.config.to_dict())

    def _post_config(self, req, params):
        body = req.get("body")
        if not isinstance(body, dict):
            return build_response(400, {"error": "Telo požiadavky musí byť JSON objekt"})
        errors = self.config.update(body)
        if errors:
            return build_response(400, {"errors": errors})
        if "pool_start" in body or "pool_end" in body:
            try:
                self.pool.update_range(self.config.pool_start, self.config.pool_end)
            except ValueError as e:
                return build_response(400, {"error": str(e)})
        return build_response(200, {"message": "Konfigurácia aktualizovaná",
                                    "config": self.config.to_dict()})

    def _get_leases(self, req, params):
        leases = self.pool.all_leases()
        return build_response(200, {"count": len(leases), "leases": leases})

    def _get_pool(self, req, params):
        return build_response(200, self.pool.pool_stats())

    def _assign_lease(self, req, params):
        body = req.get("body")
        if not isinstance(body, dict):
            return build_response(400, {"error": "Telo požiadavky musí byť JSON objekt"})
        client_id = body.get("client_id")
        if not client_id:
            return build_response(400, {"error": "Chýba povinné pole 'client_id'"})
        lease = self.pool.assign(str(client_id), body.get("requested_ip"))
        if lease is None:
            return build_response(409, {"error": "Žiadna voľná IP adresa v poole"})
        return build_response(201, {
            "message": "Adresa pridelená",
            "lease": lease.to_dict(),
            "network_params": {
                "ip": lease.ip,
                "subnet_mask": self.config.subnet_mask,
                "gateway": self.config.gateway,
                "dns_servers": self.config.dns_servers,
                "lease_time": lease.lease_time,
            },
            "options": self.config.all_options(),
        })

    def _release_lease(self, req, params):
        body = req.get("body")
        if not isinstance(body, dict):
            return build_response(400, {"error": "Telo požiadavky musí byť JSON objekt"})
        
        client_id = body.get("client_id")
        ip        = body.get("ip")
        lease_id  = body.get("id")

        if lease_id is not None:
            # Uvoľnenie podľa ID
            released = self.pool.release_by_id(int(lease_id))
        elif client_id:
            released = self.pool.release(str(client_id))
        elif ip:
            released = self.pool.release_by_ip(ip)
        else:
            return build_response(400, {"error": "Chýba 'id', 'client_id' alebo 'ip'"})

        if released:
            return build_response(200, {"message": "Adresa uvoľnená"})
        return build_response(404, {"error": "Lease nenájdená"})

    def _get_options(self, req, params):
        return build_response(200, {
            "active_options": self.config.all_options(),
            "known_options": self.config.known_options_list(),
        })

    def _post_options(self, req, params):
        body = req.get("body")
        if not isinstance(body, dict):
            return build_response(400, {"error": "Telo požiadavky musí byť JSON objekt"})
        code  = body.get("code")
        value = body.get("value")
        if code is None or value is None:
            return build_response(400, {"error": "Chýbajú polia 'code' a/alebo 'value'"})
        try:
            code = int(code)
        except (ValueError, TypeError):
            return build_response(400, {"error": "'code' musí byť celé číslo"})
        error = self.config.set_option(code, value)
        if error:
            return build_response(400, {"error": error})
        return build_response(201, {"message": f"Option {code} nastavená",
                                    "options": self.config.all_options()})

    def _delete_option(self, req, params):
        try:
            code = int(params.get("code", ""))
        except (ValueError, TypeError):
            return build_response(400, {"error": "Kód option musí byť celé číslo"})
        if self.config.remove_option(code):
            return build_response(200, {"message": f"Option {code} odstránená"})
        return build_response(404, {"error": f"Option {code} nenájdená"})

    # ------------------------------------------------------------------
    # Handlery – statické lease
    # ------------------------------------------------------------------

    def _get_static(self, req, params):
        static = self.pool.all_static_leases()
        return build_response(200, {
            "count": len(static),
            "static_leases": static,
        })

    def _post_static(self, req, params):
        body = req.get("body")
        if not isinstance(body, dict):
            return build_response(400, {"error": "Telo požiadavky musí byť JSON objekt"})
        mac = body.get("mac")
        ip  = body.get("ip")
        if not mac or not ip:
            return build_response(400, {"error": "Chýbajú polia 'mac' a/alebo 'ip'"})
        error = self.pool.add_static(str(mac), ip)
        if error:
            return build_response(400, {"error": error})
        return build_response(201, {
            "message": f"Statický lease pridaný: {mac.upper()} → {ip}",
            "static_leases": self.pool.all_static_leases(),
        })

    def _delete_static(self, req, params):
        try:
            lease_id = int(params.get("id", ""))
        except (ValueError, TypeError):
            return build_response(400, {"error": "ID musí byť celé číslo"})
        if self.pool.remove_static(lease_id):
            return build_response(200, {
                "message": f"Statický lease #{lease_id} odstránený",
                "static_leases": self.pool.all_static_leases(),
            })
        return build_response(404, {"error": f"Statický lease #{lease_id} nenájdený"})