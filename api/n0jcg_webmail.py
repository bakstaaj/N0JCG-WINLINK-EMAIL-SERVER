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
import socket
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOST = os.environ.get("N0JCG_WEBMAIL_HOST", "127.0.0.1")
PORT = int(os.environ.get("N0JCG_WEBMAIL_PORT", "8097"))
PAT_BIN = os.environ.get("N0JCG_PAT_BIN", "pat-winlink")
PAT_TIMEOUT = int(os.environ.get("N0JCG_PAT_AUTH_TIMEOUT", "45"))
PAT_TELNET_URL = os.environ.get("N0JCG_PAT_TELNET_URL", "telnet://{mycall}:CMSTelnet@cms.winlink.org:8772/wl2k")
SESSION_IDLE = int(os.environ.get("N0JCG_SESSION_IDLE_SECONDS", "1800"))
STATE_DIR = Path(os.environ.get("N0JCG_WEBMAIL_STATE_DIR", "/var/lib/n0jcg-winlink-webmail"))
PAT_BASE_CONFIG = os.environ.get("N0JCG_PAT_BASE_CONFIG", "")
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
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_signatures (callsign TEXT PRIMARY KEY, signature TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, callsign TEXT NOT NULL, recipient TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, callsign TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL)")
        db.commit()
    os.chmod(DB_PATH, 0o600)


def pat_config_path():
    """Find Pat's installed config without requiring a second copy of it."""
    candidates = []
    if PAT_BASE_CONFIG:
        candidates.append(Path(PAT_BASE_CONFIG).expanduser())
    home = Path.home()
    candidates.extend((home / ".config" / "pat" / "config.json", home / ".wl2k" / "config.json"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def write_pat_config(callsign, password, destination):
    """Copy Pat's transport settings and override only account credentials."""
    source = pat_config_path()
    config = {}
    if source:
        try:
            with source.open("r", encoding="utf-8") as handle:
                config = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Pat configuration could not be read: {exc}") from exc
    if not isinstance(config, dict):
        raise RuntimeError("Pat configuration is not a JSON object")
    if "telnet" not in config:
        raise RuntimeError("Pat telnet profile is missing; run Pat configuration once before using webmail")
    config["mycall"] = callsign
    config["secure_login_password"] = password
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(config, handle)
        handle.write("\n")


def pat_failure_detail(output, returncode, password):
    """Expose useful Pat diagnostics without returning submitted secrets."""
    safe = output.replace(password, "[REDACTED]") if password else output
    lines = [line.strip() for line in safe.splitlines() if line.strip()]
    detail = " | ".join(lines[-3:])
    if len(detail) > 600:
        detail = detail[-600:]
    return f"Pat authentication failed (exit {returncode})." + (f" {detail}" if detail else "")


def pat_validate(callsign, password):
    """Perform a real CMS/Telnet login using Pat and return evidence."""
    config = None
    mailbox_dir = STATE_DIR / "mailbox" / callsign
    try:
        mailbox_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(mailbox_dir, 0o700)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="n0jcg-pat-", suffix=".json", delete=False) as handle:
            config = Path(handle.name)
        write_pat_config(callsign, password, config)
        os.chmod(config, 0o600)
        # Pat v0.16 accepts --mycall as a global option. Pass it explicitly so
        # authentication cannot depend on whether a temporary config file was
        # discovered before the connect command is parsed.
        # Use the canonical CMS URL directly. A locally customized `telnet`
        # alias may point to an executable or stale label; that caused Pat's
        # Exit 126 here before the Winlink server was contacted.
        telnet_url = PAT_TELNET_URL.replace("{mycall}", callsign)
        command = [PAT_BIN, "--config", str(config), "--mycall", callsign, "--mbox", str(mailbox_dir), "connect", telnet_url]
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
            return False, pat_failure_detail(output, result.returncode, password)
        if not SUCCESS_RE.search(output):
            return False, "Pat returned no recognizable Winlink authentication evidence."
        return True, "Winlink CMS authentication succeeded; isolated Pat mailbox initialized."
    except subprocess.TimeoutExpired:
        return False, "Winlink authentication timed out."
    except RuntimeError as exc:
        return False, str(exc)
    finally:
        if config:
            config.unlink(missing_ok=True)


def pat_mailbox_request(session, box, mid=None, method="GET", payload=None):
    """Ask a short-lived Pat HTTP process to decode mailbox messages."""
    config = None
    process = None
    mailbox_dir = STATE_DIR / "mailbox" / session["callsign"]
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="n0jcg-pat-mail-", suffix=".json", delete=False) as handle:
            config = Path(handle.name)
        write_pat_config(session["callsign"], session["password"], config)
        os.chmod(config, 0o600)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        command = [PAT_BIN, "--config", str(config), "--mycall", session["callsign"], "--mbox", str(mailbox_dir), "--listen", "telnet", "--addr", f"127.0.0.1:{port}", "http"]
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            alternate = shutil.which("pat")
            if not alternate:
                raise RuntimeError("Pat client is not installed on the appliance.")
            command[0] = alternate
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        endpoint = f"http://127.0.0.1:{port}/api/mailbox/{box}"
        if mid:
            endpoint += f"/{mid}"
        request = urllib.request.Request(endpoint, method=method)
        if payload is not None:
            request.data = json.dumps(payload).encode("utf-8")
            request.add_header("Content-Type", "application/json")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                error = (process.stderr.read() if process.stderr else "").strip().splitlines()
                raise RuntimeError(error[-1] if error else "Pat mailbox service stopped unexpectedly.")
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    body = response.read().decode("utf-8")
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"Pat mailbox request failed ({exc.code}).") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
                time.sleep(0.1)
        raise RuntimeError("Pat mailbox API timed out.")
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        if config:
            config.unlink(missing_ok=True)


def service_state(name):
    try:
        result = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=3)
        return result.stdout.strip() or "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


def operator_diagnostics():
    devices = {}
    for label, pattern in (("audio", "/dev/snd"), ("serial", "/dev/digirig-serial"), ("ptt", "/dev/digirig-ptt")):
        path = Path(pattern)
        devices[label] = {"path": pattern, "present": path.exists()}
    return {
        "source": "local_probe",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "transmit_policy": "DISABLED",
        "services": {"webmail": service_state("n0jcg-webmail.service"), "pat": service_state("pat@pi.service"), "direwolf": service_state("direwolf.service")},
        "binaries": {"pat": bool(shutil.which("pat-winlink") or shutil.which("pat")), "direwolf": bool(shutil.which("direwolf"))},
        "devices": devices,
    }


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
            if self.path.startswith("/api/v1/mail/messages/") and self.path.endswith("/read"):
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                parts = self.path.split("/")
                if len(parts) != 7 or not re.fullmatch(r"[A-Za-z0-9._-]+", parts[5]):
                    self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid message id"})
                    return
                folder = str(data.get("folder") or "inbox")
                box = {"inbox": "in", "sent": "sent", "drafts": "out", "archive": "archive"}.get(folder)
                if not box:
                    self.send_json(HTTPStatus.BAD_REQUEST, {"error": "unknown mailbox folder"})
                    return
                try:
                    pat_mailbox_request(session, box, parts[5], "POST", {"Read": True})
                    self.send_json(HTTPStatus.OK, {"source": "pat", "state": "READY", "read": True})
                except RuntimeError as exc:
                    self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "source": "pat"})
                return
            if self.path == "/api/v1/account/signature":
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                signature = str(data.get("signature") or "").strip()
                if len(signature) > 2000:
                    raise ValueError("signature must be 2000 characters or fewer")
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("INSERT INTO mailbox_signatures(callsign,signature,updated_at) VALUES(?,?,?) ON CONFLICT(callsign) DO UPDATE SET signature=excluded.signature, updated_at=excluded.updated_at", (session["callsign"], signature, int(time.time())))
                    db.commit()
                self.send_json(HTTPStatus.OK, {"saved": True, "signature": signature})
                return
            if self.path == "/api/v1/mail/drafts":
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                recipient = str(data.get("recipient") or "").strip()
                subject = str(data.get("subject") or "").strip()
                body = str(data.get("body") or "")
                if len(recipient) > 320 or len(subject) > 160 or len(body) > 10000:
                    raise ValueError("draft exceeds an allowed field length")
                draft_id = data.get("id")
                now = int(time.time())
                with sqlite3.connect(DB_PATH) as db:
                    if draft_id:
                        db.execute("UPDATE mailbox_drafts SET recipient=?,subject=?,body=?,updated_at=? WHERE id=? AND callsign=?", (recipient, subject, body, now, int(draft_id), session["callsign"]))
                    else:
                        cursor = db.execute("INSERT INTO mailbox_drafts(callsign,recipient,subject,body,updated_at) VALUES(?,?,?,?,?)", (session["callsign"], recipient, subject, body, now))
                        draft_id = cursor.lastrowid
                    db.commit()
                self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "READY", "saved": True, "draft": {"id": int(draft_id), "recipient": recipient, "subject": subject, "body": body, "updated_at": now}})
                return
            if self.path == "/api/v1/mail/queue":
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                recipient = str(data.get("recipient") or "").strip()
                subject = str(data.get("subject") or "").strip()
                body = str(data.get("body") or "")
                if not recipient or len(recipient) > 320 or len(subject) > 160 or not body or len(body) > 10000:
                    raise ValueError("recipient, subject, and message body are required")
                now = int(time.time())
                with sqlite3.connect(DB_PATH) as db:
                    cursor = db.execute("INSERT INTO mailbox_queue(callsign,recipient,subject,body,state,created_at) VALUES(?,?,?,?,?,?)", (session["callsign"], recipient, subject, body, "QUEUED", now))
                    db.commit()
                self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "QUEUED", "queued": True, "id": int(cursor.lastrowid), "created_at": now})
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "authentication service error"})

    def do_GET(self):
        session = session_from(self)
        if self.path == "/api/v1/operator/diagnostics":
            self.send_json(HTTPStatus.OK, operator_diagnostics())
            return
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
        if self.path.startswith("/api/v1/mail/messages"):
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            try:
                path, _, query = self.path.partition("?")
                params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
                folder = params.get("folder", "inbox")
                boxes = {"inbox": "in", "sent": "sent", "drafts": "out", "archive": "archive"}
                box = boxes.get(folder)
                if not box:
                    raise ValueError("unknown mailbox folder")
                prefix = "/api/v1/mail/messages/"
                mid = path[len(prefix):] if path.startswith(prefix) else ""
                if mid and ("/" in mid or not re.fullmatch(r"[A-Za-z0-9._-]+", mid)):
                    raise ValueError("invalid message id")
                payload = pat_mailbox_request(session, box, mid or None)
                self.send_json(HTTPStatus.OK, {"source": "pat", "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "state": "READY", "folder": folder, "messages": payload if not mid else [], "message": payload if mid else None})
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except RuntimeError as exc:
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "source": "pat"})
            return
        if self.path == "/api/v1/account/signature":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT signature FROM mailbox_signatures WHERE callsign=?", (session["callsign"],)).fetchone()
            self.send_json(HTTPStatus.OK, {"callsign": session["callsign"], "signature": row[0] if row else ""})
            return
        if self.path == "/api/v1/mail/drafts":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT id,recipient,subject,body,updated_at FROM mailbox_drafts WHERE callsign=? ORDER BY updated_at DESC", (session["callsign"],)).fetchall()
            self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "READY", "drafts": [{"id": row[0], "recipient": row[1], "subject": row[2], "body": row[3], "updated_at": row[4]} for row in rows]})
            return
        if self.path == "/api/v1/mail/queue":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT id,recipient,subject,state,created_at FROM mailbox_queue WHERE callsign=? ORDER BY created_at DESC", (session["callsign"],)).fetchall()
            self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "READY", "queue": [{"id": row[0], "recipient": row[1], "subject": row[2], "state": row[3], "created_at": row[4]} for row in rows]})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_DELETE(self):
        session = session_from(self)
        queue_prefix = "/api/v1/mail/queue/"
        if self.path.startswith(queue_prefix):
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            queue_id = self.path[len(queue_prefix):]
            if not queue_id.isdigit():
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid queue id"})
                return
            with sqlite3.connect(DB_PATH) as db:
                cursor = db.execute("DELETE FROM mailbox_queue WHERE id=? AND callsign=? AND state='QUEUED'", (int(queue_id), session["callsign"]))
                db.commit()
            if cursor.rowcount == 0:
                self.send_json(HTTPStatus.CONFLICT, {"error": "queued message not found or no longer cancellable"})
            else:
                self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "READY", "cancelled": True})
            return
        draft_prefix = "/api/v1/mail/drafts/"
        if self.path.startswith(draft_prefix):
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            draft_id = self.path[len(draft_prefix):]
            if not draft_id.isdigit():
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid draft id"})
                return
            with sqlite3.connect(DB_PATH) as db:
                cursor = db.execute("DELETE FROM mailbox_drafts WHERE id=? AND callsign=?", (int(draft_id), session["callsign"]))
                db.commit()
            if cursor.rowcount == 0:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "draft not found"})
            else:
                self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "READY", "deleted": True})
            return
        prefix = "/api/v1/mail/messages/"
        if not self.path.startswith(prefix):
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not session:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            return
        path, _, query = self.path.partition("?")
        mid = path[len(prefix):]
        params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
        folder = params.get("folder", "inbox")
        box = {"inbox": "in", "sent": "sent", "drafts": "out", "archive": "archive"}.get(folder)
        if not box or not re.fullmatch(r"[A-Za-z0-9._-]+", mid):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid mailbox message"})
            return
        try:
            pat_mailbox_request(session, box, mid, "DELETE")
            self.send_json(HTTPStatus.OK, {"source": "pat", "state": "READY", "deleted": True})
        except RuntimeError as exc:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "source": "pat"})


def main():
    init_db()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
