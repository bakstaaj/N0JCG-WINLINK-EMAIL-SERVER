#!/usr/bin/env python3
"""N0JCG webmail authentication gateway for the installed Pat client.

Credentials are submitted only to this local service, passed to Pat through a
0600 temporary config file, and removed after the CMS/Telnet validation call.
Active sessions retain the credential in process memory only, with idle expiry.
This service is intentionally conservative: a successful local form post is
never treated as mailbox ownership without Pat CMS evidence.
"""

import base64
import http.cookies
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOST = os.environ.get("N0JCG_WEBMAIL_HOST", "127.0.0.1")
PORT = int(os.environ.get("N0JCG_WEBMAIL_PORT", "8097"))
PAT_BIN = os.environ.get("N0JCG_PAT_BIN", "pat-winlink")
PAT_TIMEOUT = int(os.environ.get("N0JCG_PAT_AUTH_TIMEOUT", "45"))
SESSION_IDLE = int(os.environ.get("N0JCG_SESSION_IDLE_SECONDS", "1800"))
STATE_DIR = Path(os.environ.get("N0JCG_WEBMAIL_STATE_DIR", "/var/lib/n0jcg-winlink-webmail"))
DB_PATH = STATE_DIR / "webmail.sqlite3"
EMAIL_RE = re.compile(r"^([A-Z0-9][A-Z0-9-]{2,15})@winlink\.org$", re.I)
FAILURE_RE = re.compile(r"secure login failed|authentication failed|login failed|invalid password|unknown callsign", re.I)
SUCCESS_RE = re.compile(r"CMS>|Connected to|Remote accepted|Connected", re.I)
SESSIONS = {}
LOCK = threading.RLock()


def json_bytes(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def normalize_account(value):
    account = str(value or "").strip().upper()
    match = EMAIL_RE.fullmatch(account)
    if not match:
        raise ValueError("use a Winlink address such as YOURCALL@winlink.org")
    return account, match.group(1)


def init_db():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_accounts (email TEXT PRIMARY KEY, callsign TEXT NOT NULL, first_validated_at INTEGER NOT NULL, last_validated_at INTEGER NOT NULL)")
        db.commit()
    os.chmod(DB_PATH, 0o600)


def pat_validate(callsign, password):
    """Perform a real CMS/Telnet login using Pat and return evidence."""
    config = None
    mailbox_dir = STATE_DIR / "mailbox" / callsign
    try:
        mailbox_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(mailbox_dir, 0o700)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="n0jcg-pat-", suffix=".json", delete=False) as handle:
            config = Path(handle.name)
            json.dump({"mycall": callsign, "secure_login_password": password}, handle)
            handle.write("\n")
        os.chmod(config, 0o600)
        # Pat v0.16 accepts --mycall as a global option. Pass it explicitly so
        # authentication cannot depend on whether a temporary config file was
        # discovered before the connect command is parsed.
        command = [PAT_BIN, "--config", str(config), "--mycall", callsign, "--mbox", str(mailbox_dir), "connect", "telnet"]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=PAT_TIMEOUT, env={**os.environ, "PAT_MYCALL": callsign, "PAT_SECURE_LOGIN_PASSWORD": password})
        except FileNotFoundError:
            alternate = shutil.which("pat")
            if not alternate:
                return False, "Pat client is not installed on the appliance."
            command[0] = alternate
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=PAT_TIMEOUT, env={**os.environ, "PAT_MYCALL": callsign, "PAT_SECURE_LOGIN_PASSWORD": password})
        output = result.stdout[-12000:]
        if FAILURE_RE.search(output):
            return False, "Winlink rejected the secure-login credentials."
        if result.returncode != 0:
            return False, "Pat could not complete Winlink authentication."
        if not SUCCESS_RE.search(output):
            return False, "Pat did not provide usable Winlink authentication evidence."
        return True, "Winlink CMS authentication succeeded; isolated Pat mailbox initialized."
    except subprocess.TimeoutExpired:
        return False, "Winlink authentication timed out."
    finally:
        if config:
            config.unlink(missing_ok=True)


def remember_account(email, callsign):
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT INTO mailbox_accounts(email,callsign,first_validated_at,last_validated_at) VALUES(?,?,?,?) ON CONFLICT(email) DO UPDATE SET last_validated_at=excluded.last_validated_at", (email, callsign, now, now))
        db.commit()


def create_session(email, callsign, password):
    token = secrets.token_urlsafe(32)
    with LOCK:
        SESSIONS[token] = {"email": email, "callsign": callsign, "password": password, "last_seen": time.time()}
    return token


def session_from(handler):
    raw = handler.headers.get("Cookie", "")
    cookies = http.cookies.SimpleCookie()
    cookies.load(raw)
    token = cookies.get("n0jcg_webmail_session")
    if not token:
        return None
    with LOCK:
        session = SESSIONS.get(token.value)
        if not session or time.time() - session["last_seen"] > SESSION_IDLE:
            SESSIONS.pop(token.value, None)
            return None
        session["last_seen"] = time.time()
        return {**session, "token": token.value}


class Handler(BaseHTTPRequestHandler):
    server_version = "N0JCG-Webmail/0.1"

    def log_message(self, *_args):
        return

    def send_json(self, status, value, cookie=None):
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 16384:
            raise ValueError("request is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self):
        try:
            data = self.read_json()
            if self.path == "/api/v1/auth/login":
                email, callsign = normalize_account(data.get("email"))
                password = str(data.get("password") or "")
                if len(password) < 1 or len(password) > 256:
                    raise ValueError("Winlink password is required")
                valid, evidence = pat_validate(callsign, password)
                if not valid:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": evidence, "source": "pat"})
                    return
                remember_account(email, callsign)
                token = create_session(email, callsign, password)
                self.send_json(HTTPStatus.OK, {"authenticated": True, "email": email, "callsign": callsign, "source": "pat", "evidence": evidence}, f"n0jcg_webmail_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_IDLE}")
                return
            if self.path == "/api/v1/auth/register":
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "separate webmail registration is not used; sign in with Winlink"})
                return
            if self.path == "/api/v1/auth/logout":
                session = session_from(self)
                if session:
                    with LOCK:
                        SESSIONS.pop(session["token"], None)
                self.send_json(HTTPStatus.OK, {"authenticated": False}, "n0jcg_webmail_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "authentication service error"})

    def do_GET(self):
        session = session_from(self)
        if self.path == "/api/v1/auth/session":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"authenticated": False})
            else:
                self.send_json(HTTPStatus.OK, {"authenticated": True, "email": session["email"], "callsign": session["callsign"], "source": "pat"})
            return
        if self.path == "/api/v1/mail/status":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            else:
                self.send_json(HTTPStatus.OK, {"authenticated": True, "mailbox": session["email"], "callsign": session["callsign"], "source": "pat", "state": "AUTHENTICATED", "message_access": "pending_pat_mailbox_api"})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def main():
    init_db()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
