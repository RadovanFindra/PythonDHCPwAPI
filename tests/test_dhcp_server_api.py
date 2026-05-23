import json
import threading
import unittest
from http.client import HTTPConnection

from dhcp_server_api import DHCPConfig, DHCPServerCore, create_api_server


class DHCPServerCoreTests(unittest.TestCase):
    def test_option_6_dns_servers_present(self):
        cfg = DHCPConfig(dns_servers=["9.9.9.9", "8.8.4.4"])
        options = cfg.dhcp_options()
        self.assertIn(6, options)
        self.assertEqual(options[6], ["9.9.9.9", "8.8.4.4"])

    def test_same_mac_gets_same_lease(self):
        core = DHCPServerCore()
        first = core.allocate_ip("AA:BB:CC:DD:EE:FF")
        second = core.allocate_ip("aa:bb:cc:dd:ee:ff")
        self.assertEqual(first, second)


class DHCPAPITests(unittest.TestCase):
    def setUp(self):
        self.server = create_api_server(port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _request(self, method, path, payload=None):
        host, port = self.server.server_address
        conn = HTTPConnection(host, port)
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read().decode("utf-8")
        conn.close()
        return response.status, json.loads(data)

    def test_get_config_includes_option_6(self):
        status, data = self._request("GET", "/config")
        self.assertEqual(status, 200)
        self.assertIn("options", data)
        self.assertIn("6", {str(k) for k in data["options"].keys()})

    def test_update_option_6_dns_servers(self):
        status, data = self._request("PUT", "/options/6", {"dns_servers": ["4.4.4.4", "8.8.8.8"]})
        self.assertEqual(status, 200)
        self.assertEqual(data["option"], 6)
        self.assertEqual(data["dns_servers"], ["4.4.4.4", "8.8.8.8"])

    def test_allocate_lease_via_api(self):
        status, data = self._request("POST", "/leases", {"mac": "11:22:33:44:55:66"})
        self.assertEqual(status, 201)
        self.assertEqual(data["mac"], "11:22:33:44:55:66")
        self.assertTrue(data["ip"].startswith("192.168.1."))


if __name__ == "__main__":
    unittest.main()
