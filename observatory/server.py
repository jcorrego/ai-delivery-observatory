"""Loopback admin server. Organization deployments require an authenticating proxy."""

from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import secrets
from threading import Lock
import time
from urllib.parse import parse_qs, urlsplit

from .metrics import report
from .storage import disposition, read, write

LOGIN = """<!doctype html><meta charset=utf-8><title>Admin sign in</title>
<style>body{font:16px system-ui;max-width:380px;margin:12vh auto;padding:24px;background:#f6f7f9}
input,button{font:inherit;padding:12px;margin:8px 0;width:100%;box-sizing:border-box}button{cursor:pointer}</style>
<h1>AI Delivery Observatory</h1><p>Sign in with your administrator token.</p>
<form><label>Admin token<input type=password autocomplete=current-password required></label><button>Sign in</button></form>
<p role=alert></p><script>document.querySelector('form').onsubmit=async e=>{e.preventDefault();
const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json','X-Observatory-Action':'login'},
body:JSON.stringify({token:document.querySelector('input').value})});
if(r.ok)location.reload();else document.querySelector('[role=alert]').textContent='Sign in failed.';};</script>"""


def make_server(data_path, config_path, port=8787):
    config = read(config_path)
    credentials = [(a["id"], os.environ.get(a["token_env"], "")) for a in config.get("admins", [])]
    if not credentials or any(len(token) < 32 for _, token in credentials):
        raise ValueError("Set a unique admin token of at least 32 characters for every configured admin")
    if len({token for _, token in credentials}) != len(credentials):
        raise ValueError("Admin tokens must be unique")
    sessions, failures, lock = {}, {}, Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # No request paths, cookies, or provider metadata in console logs.

        def send(self, status, body, content_type="application/json", cookie=None):
            encoded = body.encode() if isinstance(body, str) else json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def admin(self):
            bearer = self.headers.get("Authorization", "")
            if bearer.startswith("Bearer "):
                for name, token in credentials:
                    if hmac.compare_digest(bearer[7:], token):
                        return name
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookie.get("observatory_session")
            except Exception:
                return None
            session = sessions.get(value.value) if value else None
            return session[0] if session and session[1] > time.time() else None

        def do_GET(self):
            path = urlsplit(self.path)
            if not self.admin():
                return self.send(200 if path.path == "/" else 401, LOGIN if path.path == "/" else {"error": "Admin sign in required"}, "text/html" if path.path == "/" else "application/json")
            if path.path == "/":
                return self.send(200, Path(__file__).with_name("dashboard.html").read_text().replace("/*BOOTSTRAP*/null", "null"), "text/html")
            if path.path != "/api/report":
                return self.send(404, {"error": "Unknown route"})
            query = parse_qs(path.query)
            try:
                with lock:
                    data = read(data_path)
                prs = data.get("prs", [])
                observed = [p.get("merged") or p["created"] for p in prs]
                observed += [u["date"] for u in data.get("usage", [])]
                observed += [c["to"] for c in data.get("coverage", []) if c.get("status") == "complete"]
                latest = max(observed, default=config["baseline"]["to"])
                start = query.get("from", [latest[:7] + "-01"])[0]
                end = query.get("to", [latest])[0]
                output = report(data, config, start, end, query.get("person", [None])[0], query.get("repository"))
                output["options"] = {"people": [{"id": p["id"], "name": p["name"]} for p in config["people"]],
                                     "repositories": sorted({p["repository"] for p in prs})}
                output["admin"] = self.admin()
                self.send(200, output)
            except (ValueError, KeyError) as error:
                self.send(400, {"error": str(error)})

        def do_POST(self):
            # A required custom header blocks cross-site forms and cross-origin fetches.
            if self.headers.get("X-Observatory-Action") not in ("login", "review", "logout"):
                return self.send(403, {"error": "Action header required"})
            if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{self.server.server_port}"):
                return self.send(403, {"error": "Origin rejected"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length < 1 or length > 8192:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Request body must be a JSON object")
                if self.path == "/login":
                    address = self.client_address[0]
                    attempts = [t for t in failures.get(address, []) if t > time.time() - 60]
                    if len(attempts) >= 10:
                        return self.send(429, {"error": "Try again in one minute"})
                    failures[address] = attempts + [time.time()]
                    candidate = body.get("token", "")
                    if not isinstance(candidate, str):
                        raise ValueError("Invalid token")
                    name = next((name for name, token in credentials if hmac.compare_digest(candidate, token)), None)
                    if not name:
                        return self.send(401, {"error": "Invalid credentials"})
                    session = secrets.token_urlsafe(32)
                    sessions[session] = (name, time.time() + 3600)
                    return self.send(200, {"admin": name}, cookie=f"observatory_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600")
                name = self.admin()
                if not name:
                    return self.send(401, {"error": "Admin sign in required"})
                if self.path == "/logout":
                    cookie = SimpleCookie(self.headers.get("Cookie", ""))
                    if cookie.get("observatory_session"):
                        sessions.pop(cookie["observatory_session"].value, None)
                    return self.send(200, {}, cookie="observatory_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
                if self.path != "/api/effort":
                    return self.send(404, {"error": "Unknown route"})
                if not body.get("revision"):
                    raise ValueError("A reviewed revision is required")
                with lock:
                    data = read(data_path)
                    row = disposition(data, config, body["pr"], name, body["status"], body["low_hours"], body["high_hours"], body["revision"])
                    write(data_path, data)
                self.send(200, row)
            except (ValueError, KeyError, TypeError):
                self.send(400, {"error": "Invalid request or changed PR revision; reload and review"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
