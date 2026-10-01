import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from observatory.server import make_server
from observatory.storage import read, write

ROOT = Path(__file__).parents[1]


class AdminBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.data_path = Path(cls.folder.name) / "data.json"
        cls.config_path = ROOT / "examples/config.json"
        write(cls.data_path, read(ROOT / "examples/synthetic.json"))
        cls.token = "synthetic-test-credential-not-for-real-use-123456"
        with patch.dict(os.environ, {"OBSERVATORY_ADMIN_TOKEN": cls.token}):
            cls.server = make_server(cls.data_path, cls.config_path, 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.folder.cleanup()

    def request(self, path, body=None, headers=None):
        req = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers or {}, method="POST" if body is not None else "GET")
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read().decode()
            return response.status, payload, response.headers

    def auth(self, **extra):
        return {"Authorization": "Bearer " + self.token, **extra}

    def test_anonymous_home_has_login_but_no_report_data(self):
        status, body, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Admin token", body)
        self.assertNotIn("Avery", body)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_anonymous_reports_are_denied(self):
        status, body, _ = self.request("/api/report")
        self.assertEqual(status, 401)
        self.assertNotIn("Avery", body)

    def test_authenticated_report_and_filters(self):
        status, body, _ = self.request("/api/report?from=2026-09-01&to=2026-09-30&person=avery", headers=self.auth())
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["summary"]["merged"], 24)
        self.assertEqual(result["admin"], "demo-admin")
        self.assertEqual(json.loads(self.request("/api/report", headers=self.auth())[1])["period"]["to"], "2026-09-30")

    def test_path_traversal_is_not_a_file_route(self):
        status, _, _ = self.request("/../../examples/synthetic.json", headers=self.auth())
        self.assertEqual(status, 404)

    def test_login_cookie_and_logout(self):
        status, body, headers = self.request("/login", {"token": self.token}, {"X-Observatory-Action": "login"})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertEqual(self.request("/api/report", headers={"Cookie": cookie})[0], 200)
        self.assertEqual(self.request("/logout", {}, {"Cookie": cookie, "X-Observatory-Action": "logout"})[0], 200)
        self.assertEqual(self.request("/api/report", headers={"Cookie": cookie})[0], 401)

    def test_foreign_origin_and_missing_action_are_denied(self):
        self.assertEqual(self.request("/login", {"token": self.token})[0], 403)
        self.assertEqual(self.request("/login", {"token": self.token}, {"X-Observatory-Action": "login", "Origin": "https://foreign.example.invalid"})[0], 403)

    def test_forged_reviewer_is_ignored_and_revision_required(self):
        pr = read(self.data_path)["prs"][-1]
        payload = {"pr": pr["key"], "revision": "old", "status": "approved", "low_hours": 2, "high_hours": 4, "approved_by": "outsider"}
        headers = self.auth(**{"X-Observatory-Action": "review"})
        self.assertEqual(self.request("/api/effort", payload, headers)[0], 400)
        payload["revision"] = pr["revision"]
        status, body, _ = self.request("/api/effort", payload, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["approved_by"], "demo-admin")
        self.assertEqual(read(self.data_path)["effort"][-1]["approved_by"], "demo-admin")
        self.assertEqual(self.data_path.stat().st_mode & 0o777, 0o600)

    def test_invalid_json_and_oversized_body_do_not_write(self):
        before = self.data_path.read_bytes()
        status, _, _ = self.request("/api/effort", {"payload": "x" * 9000}, self.auth(**{"X-Observatory-Action": "review"}))
        self.assertEqual(status, 400)
        self.assertEqual(before, self.data_path.read_bytes())

    def test_server_rejects_missing_credentials(self):
        with patch.dict(os.environ, {"OBSERVATORY_ADMIN_TOKEN": ""}):
            with self.assertRaises(ValueError):
                make_server(self.data_path, self.config_path, 0)
