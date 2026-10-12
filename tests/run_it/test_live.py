"""run-it live state: only allowed origins on a localhost Host can read it, and snapshots are served whole."""
import http.client
import json
import os
import unittest
from unittest import mock

from run_it import live

SITE = live.DEFAULT_ORIGINS[0]


class LiveServerTest(unittest.TestCase):
    def setUp(self):
        self.state = live.LiveState()
        self.server = live.serve(self.state, 0)
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method="GET", path="/state", host=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, headers={"Host": host or f"127.0.0.1:{self.port}", **headers})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body

    def test_allowed_origin_gets_the_latest_snapshot_with_cors(self):
        self.state.update({"status": "running", "measured": True})
        response, body = self.request(Origin=SITE)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("access-control-allow-origin"), SITE)
        self.assertEqual(response.getheader("cache-control"), "no-store")
        self.assertEqual(json.loads(body), {"schema": live.SCHEMA, "status": "running", "measured": True})

    def test_other_origins_are_refused(self):
        response, body = self.request(Origin="https://evil.example")
        self.assertEqual(response.status, 403)
        self.assertIsNone(response.getheader("access-control-allow-origin"))
        self.assertNotIn(b"running", body)

    def test_non_localhost_host_is_refused_against_dns_rebinding(self):
        response, _ = self.request(host=f"attacker.example:{self.port}", Origin=SITE)
        self.assertEqual(response.status, 403)

    def test_same_machine_tools_without_origin_can_read(self):
        response, body = self.request(host=f"localhost:{self.port}")
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.getheader("access-control-allow-origin"))
        self.assertEqual(json.loads(body)["schema"], live.SCHEMA)

    def test_private_network_preflight_is_answered(self):
        response, _ = self.request("OPTIONS", Origin=SITE, **{"Access-Control-Request-Method": "GET",
                                                             "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(response.status, 204)
        self.assertEqual(response.getheader("access-control-allow-private-network"), "true")
        self.assertEqual(response.getheader("access-control-allow-origin"), SITE)

    def test_unknown_paths_are_not_found(self):
        response, _ = self.request(path="/", Origin=SITE)
        self.assertEqual(response.status, 404)


class OriginsTest(unittest.TestCase):
    def test_extra_origins_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"RUN_IT_ALLOW_ORIGINS": "https://a.example/, https://b.example"}):
            origins = live.allowed_origins()
        self.assertTrue({"https://a.example", "https://b.example", *live.DEFAULT_ORIGINS} <= origins)


if __name__ == "__main__":
    unittest.main()
