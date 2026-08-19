#!/usr/bin/env python3
"""N0JCG webmail authentication gateway for the installed Pat client.

Credentials are submitted only to this local service, passed to Pat through a
0600 temporary config file, and removed after the CMS/Telnet validation call.
Active sessions retain the credential in process memory only. By default, the
session lasts for the browser session and ends when the browser discards the
session cookie or the user selects Log out. An optional idle expiry can be
enabled with N0JCG_SESSION_IDLE_SECONDS.
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
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from winlink_templates import catalog as template_catalog
    from winlink_templates import render as render_template
    from winlink_templates import update_library as update_template_library
    from rms_gateways import cache_payload, enrich_nearest, location_state, refresh_cache, set_simulated_location
except ModuleNotFoundError:  # direct import by the repository test loader
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from winlink_templates import catalog as template_catalog
    from winlink_templates import render as render_template
    from winlink_templates import update_library as update_template_library
    from rms_gateways import cache_payload, enrich_nearest, location_state, refresh_cache, set_simulated_location


HOST = os.environ.get("N0JCG_WEBMAIL_HOST", "127.0.0.1")
PORT = int(os.environ.get("N0JCG_WEBMAIL_PORT", "8097"))
PAT_BIN = os.environ.get("N0JCG_PAT_BIN", "/usr/local/bin/pat")
# Packet RMS exchanges can take several minutes while downloading messages;
# this is an exchange timeout, not an authentication timeout.
PAT_TIMEOUT = int(os.environ.get("N0JCG_PAT_AUTH_TIMEOUT", "300"))
# The browser must not receive a mailbox session until CMS secure login is
# accepted. This wait covers RF connection and challenge exchange only; the
# subsequent mailbox transfer remains asynchronous.
PAT_LOGIN_WAIT = int(os.environ.get("N0JCG_PAT_LOGIN_WAIT_SECONDS", "90"))
PAT_TELNET_URL = os.environ.get("N0JCG_PAT_TELNET_URL", "telnet://{mycall}:CMSTelnet@cms.winlink.org:8772/wl2k")
PAT_CONNECT_URL = os.environ.get("N0JCG_PAT_CONNECT_URL", "")
PAT_PACKET_CALLSIGN = os.environ.get("N0JCG_PACKET_CALLSIGN", "")
PAT_AGWPE_ADDR = os.environ.get("N0JCG_PAT_AGWPE_ADDR", "localhost:8002")
# A value greater than zero enables an operator-selected idle timeout. The
# default is browser-session lifetime so ordinary page refreshes and long-lived
# mailbox work do not unexpectedly sign the user out.
SESSION_IDLE = int(os.environ.get("N0JCG_SESSION_IDLE_SECONDS", "0"))
STATE_DIR = Path(os.environ.get("N0JCG_WEBMAIL_STATE_DIR", "/var/lib/n0jcg-winlink-webmail"))
RADIO_PROFILE_PATH = STATE_DIR / "radio-profile.conf"
PAT_BASE_CONFIG = os.environ.get("N0JCG_PAT_BASE_CONFIG", "")
DB_PATH = STATE_DIR / "webmail.sqlite3"
EMAIL_RE = re.compile(r"^([A-Z0-9][A-Z0-9-]{2,15})@winlink\.org$", re.I)
FAILURE_RE = re.compile(r"secure login failed|authentication failed|login failed|invalid password|unknown callsign", re.I)
SUCCESS_RE = re.compile(r"CMS>|WL2K-|Remote accepted|B2F", re.I)
SESSIONS = {}
SYNC_JOBS = {}
LOCK = threading.RLock()
LOGIN_ATTEMPTS = {}
LOGIN_WINDOW = 900
LOGIN_LIMIT = 5


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
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, callsign TEXT NOT NULL, recipient TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', attachment_name TEXT NOT NULL DEFAULT '', attachment_type TEXT NOT NULL DEFAULT '', attachment_data BLOB NOT NULL DEFAULT '', updated_at INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, callsign TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, attachment_name TEXT NOT NULL DEFAULT '', attachment_type TEXT NOT NULL DEFAULT '', attachment_data BLOB NOT NULL DEFAULT '', state TEXT NOT NULL, created_at INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_folders (id INTEGER PRIMARY KEY AUTOINCREMENT, callsign TEXT NOT NULL, name TEXT NOT NULL, created_at INTEGER NOT NULL, UNIQUE(callsign,name))")
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_folder_messages (callsign TEXT NOT NULL, folder_id INTEGER NOT NULL, box TEXT NOT NULL, mid TEXT NOT NULL, assigned_at INTEGER NOT NULL, PRIMARY KEY(callsign,folder_id,mid))")
        for table in ("mailbox_drafts", "mailbox_queue"):
            for column in ("attachment_name TEXT NOT NULL DEFAULT ''", "attachment_type TEXT NOT NULL DEFAULT ''", "attachment_data BLOB NOT NULL DEFAULT ''"):
                try:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
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
    # The packet path uses AGWPE; do not require an unrelated telnet profile.
    # Pat's official client can connect through the configured AGWPE transport
    # with the per-login callsign supplied below.
    config.setdefault("agwpe", {})["addr"] = PAT_AGWPE_ADDR
    # Keep the RF AX.25 source identity separate from the logged-in Winlink
    # mailbox. Pat supports auxiliary callsigns as CALLSIGN:PASSWORD entries.
    # This lets the packet station use its configured SSID while secure login
    # is still performed for the mailbox call entered by the user.
    # The local AGWPE bridge rewrites Pat's mailbox call to the packet station
    # call before Dire Wolf sees it. Pat therefore keeps the logged-in call as
    # its own primary Winlink/FBB identity.
    config["mycall"] = callsign
    config["auxiliary_addresses"] = []
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
        # The official Pat client accepts --mycall as a global option. Pass it explicitly so
        # authentication cannot depend on whether a temporary config file was
        # discovered before the connect command is parsed.
        # Use the canonical CMS URL directly. A locally customized `telnet`
        # alias may point to an executable or stale label; that caused Pat's
        # Exit 126 here before the Winlink server was contacted.
        profile_target = radio_profile().get("N0JCG_RMS_TARGET", "").strip()
        profile_url = f"ax25+agwpe:///{profile_target}" if profile_target else ""
        connect_url = (profile_url or PAT_CONNECT_URL or PAT_TELNET_URL).replace("{mycall}", callsign)
        # The mailbox identity is dynamic per login. The AGWPE identity bridge
        # rewrites only the RF AX.25 source to the configured packet callsign.
        station_call = callsign
        command = [PAT_BIN, "--config", str(config), "--mycall", station_call, "--mbox", str(mailbox_dir), "connect", connect_url]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=PAT_TIMEOUT, env={**os.environ, "PAT_MYCALL": station_call, "PAT_SECURE_LOGIN_PASSWORD": password})
        except FileNotFoundError:
            alternate = shutil.which("pat")
            if not alternate:
                return False, "Pat client is not installed on the appliance."
            command[0] = alternate
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=PAT_TIMEOUT, env={**os.environ, "PAT_MYCALL": station_call, "PAT_SECURE_LOGIN_PASSWORD": password})
        output = result.stdout[-12000:]
        if FAILURE_RE.search(output):
            return False, "Winlink rejected the secure-login credentials."
        if result.returncode != 0:
            return False, pat_failure_detail(output, result.returncode, password)
        if not SUCCESS_RE.search(output):
            return False, "AX.25 connected, but Pat did not begin the Winlink mailbox exchange."
        transport = "Packet RMS" if connect_url.startswith("ax25") else "Winlink CMS"
        return True, f"{transport} authentication succeeded; isolated Pat mailbox initialized."
    except subprocess.TimeoutExpired:
        return False, "Winlink authentication timed out."
    except RuntimeError as exc:
        return False, str(exc)
    finally:
        if config:
            config.unlink(missing_ok=True)


def sync_status(callsign):
    with LOCK:
        job = SYNC_JOBS.get(callsign)
        if not job:
            return {"state": "IDLE", "stage": "idle", "message": "No mailbox synchronization is running."}
        return {key: job.get(key) for key in ("state", "stage", "message", "started_at", "updated_at", "received", "sent", "pending_count", "outgoing_count", "rms_target", "last_line")}


def _pat_sync_worker(callsign, password, config, process, job):
    """Keep the RF exchange alive after CMS authentication and report progress."""
    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            with LOCK:
                job["updated_at"] = time.time()
                job["last_line"] = line[-500:]
                if "Connected to CMS" in line:
                    job["stage"] = "cms_connected"
                    job["message"] = "Connected to Winlink CMS; completing secure login."
                elif line.startswith(";PQ"):
                    job["stage"] = "authenticating"
                    job["state"] = "AUTHENTICATED"
                    job["message"] = "Winlink secure login accepted; requesting mailbox index."
                    job["auth_event"].set()
                elif re.search(r"\d+ proposal\(s\) received", line, re.I):
                    job["pending_count"] = int(re.search(r"(\d+) proposal", line, re.I).group(1))
                    job["state"] = "AUTHENTICATED"
                    job["stage"] = "downloading"
                    job["message"] = "Mailbox opened; downloading messages."
                    job["auth_event"].set()
                elif line.startswith(">FC EM"):
                    job["outgoing_count"] = int(job.get("outgoing_count") or 0) + 1
                    job["state"] = "AUTHENTICATED"
                    job["stage"] = "uploading"
                    job["message"] = f"Mailbox authenticated; sending {job['outgoing_count']} queued message(s)."
                    job["auth_event"].set()
                elif "No messages" in line or "0 proposal(s)" in line:
                    job["state"] = "AUTHENTICATED"
                    job["stage"] = "no_messages"
                    job["message"] = "The RMS returned no downloadable proposals in this exchange."
                    job["pending_count"] = 0
                    job["auth_event"].set()
                elif line.lstrip(">").strip() == "FQ":
                    # FQ is Pat's final exchange command. If this session
                    # uploaded queued messages, the RMS has accepted the
                    # outgoing transfer; do not keep the local send queue in
                    # STAGED while waiting for the remote socket to close.
                    if job.get("outgoing_count"):
                        with sqlite3.connect(DB_PATH) as db:
                            db.execute("UPDATE mailbox_queue SET state='SENT' WHERE callsign=? AND state='STAGED'", (callsign,))
                            db.commit()
                        job["sent"] = job.get("outgoing_count") or 0
                    job["state"] = "AUTHENTICATED"
                    job["stage"] = "no_messages"
                    job["message"] = "The RMS returned no downloadable proposals in this exchange."
                    job["pending_count"] = 0
                    job["auth_event"].set()
                elif re.search(r"Receiving \[|Sending \[|received", line, re.I):
                    job["stage"] = "downloading"
                    job["message"] = "Mailbox opened; message transfer in progress."
        returncode = process.wait()
        with LOCK:
            if returncode == 0 and job["state"] == "AUTHENTICATED":
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE mailbox_queue SET state='SENT' WHERE callsign=? AND state='STAGED'", (callsign,))
                    db.commit()
                job["stage"] = "complete"
                job["message"] = "Mailbox synchronization complete."
            if job["state"] not in ("AUTHENTICATED", "ERROR"):
                if returncode == 0:
                    job["state"] = "ERROR"
                    job["stage"] = "failed"
                    job["message"] = "Winlink exchange ended before the mailbox result was confirmed."
                    job["error_event"].set()
                else:
                    job["state"] = "ERROR"
                    job["stage"] = "failed"
                    detail = job.get("last_line") or "The client stopped before reporting a connection attempt."
                    job["message"] = f"Winlink mailbox synchronization failed. {detail}"
                    job["error_event"].set()
    except Exception as exc:
        with LOCK:
            job["state"] = "ERROR"
            job["stage"] = "failed"
            job["message"] = str(exc)
            job["error_event"].set()
    finally:
        config.unlink(missing_ok=True)


def start_pat_sync(callsign, password):
    mailbox_dir = STATE_DIR / "mailbox" / callsign
    mailbox_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(mailbox_dir, 0o700)
    with LOCK:
        existing = SYNC_JOBS.get(callsign)
        if existing and existing.get("process") and existing["process"].poll() is None:
            return existing
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="n0jcg-pat-sync-", suffix=".json", delete=False) as handle:
        config = Path(handle.name)
    write_pat_config(callsign, password, config)
    os.chmod(config, 0o600)
    # Read the operator profile for every new exchange. The profile can be
    # changed from the console while this long-running webmail service stays
    # up; using only the EnvironmentFile value would keep the previous RMS
    # target in memory until the next service restart.
    profile_target = radio_profile().get("N0JCG_RMS_TARGET", "").strip()
    profile_url = f"ax25+agwpe:///{profile_target}" if profile_target else ""
    connect_url = (profile_url or PAT_CONNECT_URL or PAT_TELNET_URL).replace("{mycall}", callsign)
    command = [PAT_BIN, "--config", str(config), "--mycall", callsign, "--mbox", str(mailbox_dir), "connect", connect_url]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env={**os.environ, "PAT_MYCALL": callsign, "PAT_SECURE_LOGIN_PASSWORD": password})
    except FileNotFoundError:
        alternate = shutil.which("pat")
        if not alternate:
            config.unlink(missing_ok=True)
            raise RuntimeError("Pat client is not installed on the appliance.")
        command[0] = alternate
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env={**os.environ, "PAT_MYCALL": callsign, "PAT_SECURE_LOGIN_PASSWORD": password})
    job = {"process": process, "state": "CONNECTING", "stage": "connecting", "message": f"Connecting to Packet RMS gateway {profile_target or 'configured target'}.", "rms_target": profile_target, "started_at": time.time(), "updated_at": time.time(), "received": 0, "sent": 0, "pending_count": None, "outgoing_count": 0, "last_line": "", "auth_event": threading.Event(), "error_event": threading.Event()}
    with LOCK:
        SYNC_JOBS[callsign] = job
    threading.Thread(target=_pat_sync_worker, args=(callsign, password, config, process, job), daemon=True, name=f"pat-sync-{callsign}").start()
    return job


def wait_for_pat_auth(job, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job["auth_event"].is_set():
            return True, "Winlink secure login accepted; mailbox synchronization is continuing."
        if job["error_event"].is_set():
            return False, job.get("message", "Winlink authentication failed.")
        process = job.get("process")
        if process and process.poll() is not None:
            return False, job.get("message", "Winlink authentication failed before secure login was accepted.")
        time.sleep(0.25)
    return False, "Winlink authentication timed out before secure login was accepted."


def pat_mailbox_request(session, box, mid=None, method="GET", payload=None, suffix=""):
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
        station_call = session["callsign"]
        command = [PAT_BIN, "--config", str(config), "--mycall", station_call, "--mbox", str(mailbox_dir), "http", "--addr", f"127.0.0.1:{port}"]
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
            endpoint += f"/{urllib.parse.quote(str(mid), safe='')}"
        endpoint += suffix
        request = urllib.request.Request(endpoint, method=method)
        if payload is not None:
            attachment = payload.get("_attachment") if isinstance(payload, dict) else None
            if suffix == "/read":
                # Pat's read-state handler decodes JSON; mailbox composition
                # posts below intentionally use form or multipart encoding.
                request.data = json.dumps(payload).encode("utf-8")
                request.add_header("Content-Type", "application/json")
            elif attachment and attachment[2]:
                boundary = f"----N0JCG{secrets.token_hex(12)}"
                chunks = []
                for key, value in payload.items():
                    if key == "_attachment":
                        continue
                    chunks.extend([f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n".encode(), str(value).encode(), b"\r\n"])
                name, content_type, data = attachment
                chunks.extend([f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\nContent-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode(), data, b"\r\n", f"--{boundary}--\r\n".encode()])
                request.data = b"".join(chunks)
                request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            else:
                request.data = urllib.parse.urlencode({key: value for key, value in payload.items() if key != "_attachment"}).encode("utf-8")
                request.add_header("Content-Type", "application/x-www-form-urlencoded")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                error = (process.stderr.read() if process.stderr else "").strip().splitlines()
                detail = error[-1] if error else ""
                # Pat can emit its CLI usage text when the mailbox HTTP
                # process cannot initialize. That text is useful in logs but
                # is not actionable for a webmail user.
                if "--send-only" in detail or "Download inbound messages later" in detail:
                    raise RuntimeError("Your Winlink account is authenticated, but the local mailbox is not available yet. Connect the radio and run a mailbox sync, then select Refresh.")
                raise RuntimeError(detail or "The local Winlink mailbox service stopped unexpectedly. Select Refresh or ask the operator to check the client service.")
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    body = response.read().decode("utf-8")
                    if not body:
                        return {}
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError:
                        # Pat returns plain text for successful outbox posts.
                        return {"text": body}
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


def stage_queued_messages(session):
    """Copy WES queue entries into Pat's official local outbox before connect."""
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT id,recipient,subject,body,attachment_name,attachment_type,attachment_data FROM mailbox_queue WHERE callsign=? AND state='QUEUED' ORDER BY created_at",
            (session["callsign"],),
        ).fetchall()
    staged = 0
    for queue_id, recipient, subject, body, attachment_name, attachment_type, attachment_data in rows:
        pat_mailbox_request(
            session,
            "out",
            method="POST",
            payload={
                "to": recipient,
                "subject": subject,
                "body": body,
                "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "_attachment": (attachment_name, attachment_type, attachment_data),
            },
        )
        with sqlite3.connect(DB_PATH) as db:
            db.execute("UPDATE mailbox_queue SET state='STAGED' WHERE id=? AND callsign=? AND state='QUEUED'", (queue_id, session["callsign"]))
            db.commit()
        staged += 1
    return staged


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
    templates = template_catalog()
    profile = radio_profile()
    return {
        "source": "local_probe",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "transmit_policy": "PACKET_SESSION_CONTROLLED",
        "services": {"webmail": service_state("n0jcg-webmail.service"), "pat": service_state("pat@pi.service"), "direwolf": service_state("direwolf.service")},
        "binaries": {"pat": bool(shutil.which("pat-winlink") or shutil.which("pat")), "direwolf": bool(shutil.which("direwolf"))},
        "devices": devices,
        "standard_forms": {"available": bool(templates.get("available")), "version": templates.get("version", ""), "count": len(templates.get("templates", []))},
        "packet": {"local_id": profile["N0JCG_PACKET_CALLSIGN"], "frequency_mhz": profile["N0JCG_PACKET_FREQUENCY"], "mode": "1200-AFSK", "rms_target": profile["N0JCG_RMS_TARGET"], "auto_sync_minutes": int(profile["N0JCG_AUTO_SYNC_MINUTES"])},
    }


def radio_profile():
    values = {"N0JCG_PACKET_CALLSIGN": PAT_PACKET_CALLSIGN or "N0JCG-3", "N0JCG_PACKET_FREQUENCY": "145.070", "N0JCG_AUDIO_DEVICE": "plughw:Device,0", "N0JCG_PTT_DEVICE": "/dev/digirig-ptt", "N0JCG_AUTO_SYNC_MINUTES": "30", "N0JCG_RMS_TARGET": "N0JCG-10"}
    try:
        for line in RADIO_PROFILE_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if key in values:
                    values[key] = value.strip()
    except OSError:
        pass
    return values


def apply_radio_profile(data):
    current = radio_profile()
    callsign = str(data.get("packet_callsign") or current["N0JCG_PACKET_CALLSIGN"]).strip().upper()
    frequency = str(data.get("frequency") or current["N0JCG_PACKET_FREQUENCY"]).strip()
    auto_sync_minutes = str(data.get("auto_sync_minutes") or current["N0JCG_AUTO_SYNC_MINUTES"]).strip()
    rms_target = str(data.get("rms_target") or current["N0JCG_RMS_TARGET"]).strip().upper()
    if not re.fullmatch(r"[A-Z0-9-]{3,15}", callsign):
        raise ValueError("packet station ID is invalid")
    if not re.fullmatch(r"[0-9]{2,3}(?:\.[0-9]{1,6})?", frequency):
        raise ValueError("frequency must be entered in MHz, for example 145.070")
    if not re.fullmatch(r"[0-9]+", auto_sync_minutes) or not 5 <= int(auto_sync_minutes) <= 1440:
        raise ValueError("automatic mailbox sync must be between 5 and 1440 minutes")
    if not re.fullmatch(r"[A-Z0-9-]{3,15}", rms_target):
        raise ValueError("RMS gateway target is invalid")
    RADIO_PROFILE_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    profile_lines = [
        f"N0JCG_PACKET_CALLSIGN={callsign}",
        f"N0JCG_PACKET_FREQUENCY={frequency}",
        f"N0JCG_AUDIO_DEVICE={current['N0JCG_AUDIO_DEVICE']}",
        f"N0JCG_PTT_DEVICE={current['N0JCG_PTT_DEVICE']}",
        f"N0JCG_AUTO_SYNC_MINUTES={auto_sync_minutes}",
        f"N0JCG_RMS_TARGET={rms_target}",
        "N0JCG_PACKET_MODE=1200-AFSK",
        f"N0JCG_PAT_CONNECT_URL=ax25+agwpe:///{rms_target}",
        "",
    ]
    RADIO_PROFILE_PATH.write_text("\n".join(profile_lines), encoding="utf-8")
    result = subprocess.run(["sudo", "/opt/n0jcg-winlink/tools/apply_radio_profile.sh"], capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "radio profile could not be applied").strip()[-500:])
    return radio_profile()


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


def session_cookie(token):
    """Return a browser-session cookie; it is intentionally not persistent."""
    return f"n0jcg_webmail_session={token}; Path=/; HttpOnly; SameSite=Strict"


def clear_session_cookie():
    return "n0jcg_webmail_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"


def session_from(handler):
    raw = handler.headers.get("Cookie", "")
    cookies = http.cookies.SimpleCookie()
    cookies.load(raw)
    token = cookies.get("n0jcg_webmail_session")
    if not token:
        return None
    with LOCK:
        session = SESSIONS.get(token.value)
        expired = SESSION_IDLE > 0 and time.time() - session["last_seen"] > SESSION_IDLE if session else True
        if not session or expired:
            SESSIONS.pop(token.value, None)
            return None
        session["last_seen"] = time.time()
        return {**session, "token": token.value}


def session_view(session):
    expires_at = int(session["last_seen"] + SESSION_IDLE) if SESSION_IDLE > 0 else None
    return {"authenticated": True, "email": session["email"], "callsign": session["callsign"], "source": "pat", "auth_state": sync_status(session["callsign"])["state"], "expires_at": expires_at}


def login_allowed(client_id):
    now = time.time()
    with LOCK:
        attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(client_id, []) if now - stamp < LOGIN_WINDOW]
        LOGIN_ATTEMPTS[client_id] = attempts
        return len(attempts) < LOGIN_LIMIT, max(0, int(LOGIN_WINDOW - (now - attempts[0]))) if attempts else 0


def record_login_failure(client_id):
    with LOCK:
        LOGIN_ATTEMPTS.setdefault(client_id, []).append(time.time())


def clear_login_failures(client_id):
    with LOCK:
        LOGIN_ATTEMPTS.pop(client_id, None)


def parse_attachment(data):
    attachment = data.get("attachment") or {}
    if not isinstance(attachment, dict):
        raise ValueError("invalid attachment")
    encoded = str(attachment.get("data") or "")
    if not encoded:
        return "", "", b""
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("invalid attachment data") from exc
    if len(content) > 100 * 1024:
        raise ValueError("attachment exceeds the 100 KB maximum")
    return str(attachment.get("name") or "attachment")[:255], str(attachment.get("type") or "application/octet-stream")[:120], content


def folder_name(value):
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}", name):
        raise ValueError("folder names must be 1-40 characters and use letters, numbers, spaces, _ or -")
    if name.lower() in {"inbox", "sent", "drafts", "send queue", "archive"}:
        raise ValueError("that name is reserved")
    return name


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

    def send_rate_limited(self, retry_after):
        body = json_bytes({"error": "too many login attempts; try again later", "retry_after": retry_after})
        self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Retry-After", str(retry_after))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 180000:
            raise ValueError("request is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self):
        try:
            data = self.read_json()
            if self.path == "/api/v1/auth/login":
                client_id = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",", 1)[0].strip()
                allowed, retry_after = login_allowed(client_id)
                if not allowed:
                    self.send_rate_limited(retry_after)
                    return
                email, callsign = normalize_account(data.get("email"))
                password = str(data.get("password") or "")
                if len(password) < 1 or len(password) > 256:
                    raise ValueError("Winlink password is required")
                staged = stage_queued_messages({"callsign": callsign, "password": password})
                job = start_pat_sync(callsign, password)
                authenticated, evidence = wait_for_pat_auth(job, PAT_LOGIN_WAIT)
                if not authenticated:
                    record_login_failure(client_id)
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": evidence, "source": "pat"})
                    return
                # A valid Winlink login proves the operator has recovered from
                # prior transient RF/authentication failures. Do not carry
                # those failures into the next login window.
                clear_login_failures(client_id)
                token = create_session(email, callsign, password)
                self.send_json(HTTPStatus.OK, {**session_view({"email": email, "callsign": callsign, "last_seen": time.time()}), "evidence": evidence, "staged_for_send": staged, "sync": sync_status(callsign)}, session_cookie(token))
                return
            if self.path == "/api/v1/auth/register":
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "separate webmail registration is not used; sign in with Winlink"})
                return
            if self.path == "/api/v1/mail/sync":
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                job = start_pat_sync(session["callsign"], session["password"])
                self.send_json(HTTPStatus.ACCEPTED, {"state": "SYNCING", "sync": sync_status(session["callsign"])})
                return
            if self.path == "/api/v1/auth/logout":
                session = session_from(self)
                if session:
                    with LOCK:
                        SESSIONS.pop(session["token"], None)
                self.send_json(HTTPStatus.OK, {"authenticated": False}, clear_session_cookie())
                return
            if self.path == "/api/v1/operator/rms-gateways/refresh":
                try:
                    payload = refresh_cache()
                except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
                    self.send_json(HTTPStatus.BAD_GATEWAY, {"error": f"RMS gateway list update failed: {exc}", "source": "winlink_rms_status"})
                    return
                self.send_json(HTTPStatus.OK, {"updated": True, **payload})
                return
            if self.path == "/api/v1/operator/location":
                try:
                    enabled = bool(data.get("enabled", True))
                    value = set_simulated_location(data.get("latitude"), data.get("longitude"), enabled)
                except ValueError as exc:
                    self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self.send_json(HTTPStatus.OK, {"saved": True, "location": value})
                return
            if self.path == "/api/v1/operator/templates/update":
                try:
                    payload = update_template_library()
                except (RuntimeError, OSError) as exc:
                    self.send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "source": "winlink_standard_forms"})
                    return
                forms = template_catalog()
                self.send_json(HTTPStatus.OK, {"source": "winlink_standard_forms", "updated": True, "count": len(forms.get("templates", [])), **payload})
                return
            if self.path == "/api/v1/operator/radio-profile":
                payload = apply_radio_profile(data)
                self.send_json(HTTPStatus.OK, {"saved": True, "profile": payload})
                return
            if self.path == "/api/v1/templates/render":
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                rendered = render_template(str(data.get("template_id") or ""), data.get("values") or {}, session["callsign"])
                self.send_json(HTTPStatus.OK, {"source": "winlink_standard_forms", **rendered})
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
                if folder.startswith("custom:") and folder[7:].isdigit():
                    with sqlite3.connect(DB_PATH) as db:
                        assignment = db.execute("SELECT box FROM mailbox_folder_messages WHERE callsign=? AND folder_id=? AND mid=?", (session["callsign"], int(folder[7:]), parts[5])).fetchone()
                    box = assignment[0] if assignment else None
                if not box:
                    self.send_json(HTTPStatus.BAD_REQUEST, {"error": "unknown mailbox folder"})
                    return
                try:
                    # Pat's mailbox API exposes read-state changes at
                    # /api/mailbox/{box}/{mid}/read.  Posting to the message
                    # URL itself does not change the mailbox flag.
                    pat_mailbox_request(session, box, parts[5], "POST", {"Read": True}, "/read")
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
                attachment_name, attachment_type, attachment_data = parse_attachment(data)
                if len(recipient) > 320 or len(subject) > 160 or len(body) > 10000:
                    raise ValueError("draft exceeds an allowed field length")
                draft_id = data.get("id")
                now = int(time.time())
                with sqlite3.connect(DB_PATH) as db:
                    if draft_id:
                        db.execute("UPDATE mailbox_drafts SET recipient=?,subject=?,body=?,attachment_name=?,attachment_type=?,attachment_data=?,updated_at=? WHERE id=? AND callsign=?", (recipient, subject, body, attachment_name, attachment_type, attachment_data, now, int(draft_id), session["callsign"]))
                    else:
                        cursor = db.execute("INSERT INTO mailbox_drafts(callsign,recipient,subject,body,attachment_name,attachment_type,attachment_data,updated_at) VALUES(?,?,?,?,?,?,?,?)", (session["callsign"], recipient, subject, body, attachment_name, attachment_type, attachment_data, now))
                        draft_id = cursor.lastrowid
                    db.commit()
                self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "READY", "saved": True, "draft": {"id": int(draft_id), "recipient": recipient, "subject": subject, "body": body, "attachment_name": attachment_name, "attachment_type": attachment_type, "attachment_size": len(attachment_data), "updated_at": now}})
                return
            if self.path == "/api/v1/mail/queue":
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                recipient = str(data.get("recipient") or "").strip()
                subject = str(data.get("subject") or "").strip()
                body = str(data.get("body") or "")
                attachment_name, attachment_type, attachment_data = parse_attachment(data)
                if not recipient or len(recipient) > 320 or len(subject) > 160 or not body or len(body) > 10000:
                    raise ValueError("recipient, subject, and message body are required")
                draft_id = data.get("draft_id")
                now = int(time.time())
                with sqlite3.connect(DB_PATH) as db:
                    cursor = db.execute("INSERT INTO mailbox_queue(callsign,recipient,subject,body,attachment_name,attachment_type,attachment_data,state,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (session["callsign"], recipient, subject, body, attachment_name, attachment_type, attachment_data, "QUEUED", now))
                    if draft_id and str(draft_id).isdigit():
                        db.execute("DELETE FROM mailbox_drafts WHERE id=? AND callsign=?", (int(draft_id), session["callsign"]))
                    db.commit()
                sync_error = ""
                staged = 0
                try:
                    staged = stage_queued_messages(session)
                    start_pat_sync(session["callsign"], session["password"])
                except RuntimeError as exc:
                    # Keep the local queue record intact if staging or the
                    # radio exchange cannot start; the next login/Refresh
                    # will retry it.
                    sync_error = str(exc)
                self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "QUEUED", "queued": True, "draft_deleted": bool(draft_id and str(draft_id).isdigit()), "id": int(cursor.lastrowid), "created_at": now, "staged_for_send": staged, "sync": sync_status(session["callsign"]), "sync_error": sync_error})
                return
            if self.path == "/api/v1/mail/folders":
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                name = folder_name(data.get("name"))
                now = int(time.time())
                try:
                    with sqlite3.connect(DB_PATH) as db:
                        cursor = db.execute("INSERT INTO mailbox_folders(callsign,name,created_at) VALUES(?,?,?)", (session["callsign"], name, now))
                        db.commit()
                except sqlite3.IntegrityError:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "that folder already exists"})
                    return
                self.send_json(HTTPStatus.OK, {"source": "local_queue", "created": True, "folder": {"id": int(cursor.lastrowid), "name": name, "created_at": now}})
                return
            if self.path.startswith("/api/v1/mail/messages/") and self.path.endswith("/move"):
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                parts = self.path.split("/")
                if len(parts) != 7 or not re.fullmatch(r"[A-Za-z0-9._-]+", parts[5]):
                    self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid message id"})
                    return
                folder_id = data.get("folder_id")
                box = str(data.get("box") or "in")
                if not str(folder_id).isdigit() or box not in {"in", "sent", "out", "archive"}:
                    self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid destination folder"})
                    return
                with sqlite3.connect(DB_PATH) as db:
                    owner = db.execute("SELECT 1 FROM mailbox_folders WHERE id=? AND callsign=?", (int(folder_id), session["callsign"])).fetchone()
                    if not owner:
                        self.send_json(HTTPStatus.NOT_FOUND, {"error": "folder not found"})
                        return
                    # A message belongs to one user folder at a time. Remove
                    # any prior assignment so a move removes it from the
                    # source folder and repeated moves do not create stale
                    # copies in custom folders.
                    db.execute("DELETE FROM mailbox_folder_messages WHERE callsign=? AND mid=?", (session["callsign"], parts[5]))
                    db.execute("INSERT INTO mailbox_folder_messages(callsign,folder_id,box,mid,assigned_at) VALUES(?,?,?,?,?)", (session["callsign"], int(folder_id), box, parts[5], int(time.time())))
                    db.commit()
                self.send_json(HTTPStatus.OK, {"source": "local_queue", "moved": True, "folder_id": int(folder_id)})
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "authentication service error"})

    def do_GET(self):
        session = session_from(self)
        if self.path == "/api/v1/operator/diagnostics":
            diagnostics = operator_diagnostics()
            cache = cache_payload()
            diagnostics["rms_gateways"] = {"installed": bool(cache.get("installed")), "count": int(cache.get("count", len(cache.get("records", [])))), "updated_at": cache.get("updated_at"), "location": location_state()}
            self.send_json(HTTPStatus.OK, diagnostics)
            return
        if self.path == "/api/v1/operator/location":
            self.send_json(HTTPStatus.OK, {"location": location_state()})
            return
        if self.path == "/api/v1/operator/rms-gateways":
            cache = cache_payload()
            location = location_state()
            latitude, longitude = location.get("latitude"), location.get("longitude")
            nearby = enrich_nearest(cache.get("records", []), latitude, longitude, limit=5) if latitude is not None and longitude is not None else []
            self.send_json(HTTPStatus.OK, {"source": cache.get("source"), "updated_at": cache.get("updated_at"), "count": len(cache.get("records", [])), "location": location, "gateways": nearby})
            return
        if self.path == "/api/v1/operator/radio-profile":
            self.send_json(HTTPStatus.OK, {"profile": radio_profile()})
            return
        if self.path == "/api/v1/auth/session":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"authenticated": False})
            else:
                self.send_json(HTTPStatus.OK, session_view(session))
            return
        if self.path == "/api/v1/mail/sync":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            else:
                self.send_json(HTTPStatus.OK, sync_status(session["callsign"]))
            return
        if self.path == "/api/v1/mail/settings":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            else:
                self.send_json(HTTPStatus.OK, {"auto_sync_minutes": int(radio_profile()["N0JCG_AUTO_SYNC_MINUTES"])})
            return
        if self.path == "/api/v1/mail/status":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            else:
                self.send_json(HTTPStatus.OK, {"authenticated": True, "mailbox": session["email"], "callsign": session["callsign"], "source": "pat", "state": "AUTHENTICATED", "message_access": "pending_pat_mailbox_api"})
            return
        if self.path == "/api/v1/templates":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            payload = template_catalog()
            if not payload.get("available"):
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Standard Forms have not been installed by the operator yet.", "source": "winlink_standard_forms", "available": False})
                return
            self.send_json(HTTPStatus.OK, {"source": "winlink_standard_forms", **payload})
            return
        if self.path.startswith("/api/v1/mail/messages"):
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            current_sync = sync_status(session["callsign"])
            # Keep previously downloaded mail visible while a new RF exchange
            # is running. The old behavior returned an empty list for the
            # entire duration of a download, which made a populated mailbox
            # appear to contain zero messages.
            # Pat's --mbox root contains a callsign-named mailbox directory.
            inbox_dir = STATE_DIR / "mailbox" / session["callsign"] / session["callsign"] / "in"
            has_local_inbox = inbox_dir.is_dir() and any(inbox_dir.iterdir())
            if (current_sync["state"] in ("CONNECTING", "AUTHENTICATING") or current_sync["stage"] in ("downloading", "uploading")) and not has_local_inbox:
                self.send_json(HTTPStatus.OK, {"source": "pat", "state": "SYNCING", "sync": current_sync, "messages": []})
                return
            if current_sync.get("stage") == "no_messages":
                # A sync can legitimately return no *new* proposals while
                # Pat still has previously downloaded mail. Do not hide that
                # local mailbox just because the latest exchange was empty.
                if not has_local_inbox:
                    self.send_json(HTTPStatus.OK, {"source": "pat", "state": "NO_MESSAGES", "sync": current_sync, "messages": []})
                    return
            try:
                path, _, query = self.path.partition("?")
                params = {key: values[-1] for key, values in urllib.parse.parse_qs(query, keep_blank_values=True).items()}
                folder = params.get("folder", "inbox")
                prefix = "/api/v1/mail/messages/"
                mid = urllib.parse.unquote(path[len(prefix):]) if path.startswith(prefix) else ""
                boxes = {"inbox": "in", "sent": "sent", "drafts": "out", "archive": "archive"}
                box = boxes.get(folder)
                if folder.startswith("custom:"):
                    custom_id = folder[7:]
                    if not custom_id.isdigit():
                        raise ValueError("invalid custom folder")
                    with sqlite3.connect(DB_PATH) as db:
                        row = db.execute("SELECT box FROM mailbox_folder_messages WHERE callsign=? AND folder_id=? AND mid=?", (session["callsign"], int(custom_id), mid)).fetchone()
                    box = row[0] if row else None
                if not box:
                    raise ValueError("unknown mailbox folder")
                if mid and ("/" in mid or not re.fullmatch(r"[A-Za-z0-9._-]+", mid)):
                    raise ValueError("invalid message id")
                payload = pat_mailbox_request(session, box, mid or None)
                if not mid:
                    # Custom-folder assignment is a move, not a label.
                    with sqlite3.connect(DB_PATH) as db:
                        assigned = {row[0] for row in db.execute("SELECT mid FROM mailbox_folder_messages WHERE callsign=?", (session["callsign"],)).fetchall()}
                    payload = [message for message in payload if str(message.get("MID", "")) not in assigned]
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
                rows = db.execute("SELECT id,recipient,subject,body,attachment_name,attachment_type,length(attachment_data),updated_at FROM mailbox_drafts WHERE callsign=? ORDER BY updated_at DESC", (session["callsign"],)).fetchall()
            self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "READY", "drafts": [{"id": row[0], "recipient": row[1], "subject": row[2], "body": row[3], "attachment_name": row[4], "attachment_type": row[5], "attachment_size": row[6] or 0, "updated_at": row[7]} for row in rows]})
            return
        if self.path == "/api/v1/mail/queue":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT id,recipient,subject,state,created_at FROM mailbox_queue WHERE callsign=? AND state IN ('QUEUED','STAGED') ORDER BY created_at DESC", (session["callsign"],)).fetchall()
            self.send_json(HTTPStatus.OK, {"source": "local_queue", "state": "READY", "queue": [{"id": row[0], "recipient": row[1], "subject": row[2], "state": row[3], "created_at": row[4]} for row in rows]})
            return
        if self.path == "/api/v1/mail/folders":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT id,name,created_at FROM mailbox_folders WHERE callsign=? ORDER BY name COLLATE NOCASE", (session["callsign"],)).fetchall()
            folders = []
            for row in rows:
                with sqlite3.connect(DB_PATH) as folder_db:
                    count = folder_db.execute("SELECT COUNT(*) FROM mailbox_folder_messages WHERE callsign=? AND folder_id=?", (session["callsign"], row[0])).fetchone()[0]
                folders.append({"id": row[0], "name": row[1], "created_at": row[2], "count": count})
            self.send_json(HTTPStatus.OK, {"source": "local_queue", "folders": folders})
            return
        folder_message_prefix = "/api/v1/mail/folders/"
        if self.path.startswith(folder_message_prefix) and self.path.endswith("/messages"):
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            folder_id = self.path[len(folder_message_prefix):-len("/messages")]
            if not folder_id.isdigit():
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid folder id"})
                return
            with sqlite3.connect(DB_PATH) as db:
                owned = db.execute("SELECT 1 FROM mailbox_folders WHERE id=? AND callsign=?", (int(folder_id), session["callsign"])).fetchone()
                assignments = db.execute("SELECT box,mid FROM mailbox_folder_messages WHERE callsign=? AND folder_id=? ORDER BY assigned_at DESC", (session["callsign"], int(folder_id))).fetchall()
            if not owned:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "folder not found"})
                return
            messages = []
            for box, mid in assignments:
                try:
                    messages.append(pat_mailbox_request(session, box, mid))
                except RuntimeError:
                    continue
            self.send_json(HTTPStatus.OK, {"source": "pat", "state": "READY", "messages": messages})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_DELETE(self):
        session = session_from(self)
        folder_prefix = "/api/v1/mail/folders/"
        if self.path.startswith(folder_prefix):
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            folder_id = self.path[len(folder_prefix):]
            if not folder_id.isdigit():
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid folder id"})
                return
            with sqlite3.connect(DB_PATH) as db:
                db.execute("DELETE FROM mailbox_folder_messages WHERE folder_id=? AND callsign=?", (int(folder_id), session["callsign"]))
                cursor = db.execute("DELETE FROM mailbox_folders WHERE id=? AND callsign=?", (int(folder_id), session["callsign"]))
                db.commit()
            if cursor.rowcount == 0:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "folder not found"})
            else:
                self.send_json(HTTPStatus.OK, {"source": "local_queue", "deleted": True})
            return
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
        mid = urllib.parse.unquote(path[len(prefix):])
        params = {key: values[-1] for key, values in urllib.parse.parse_qs(query, keep_blank_values=True).items()}
        folder = params.get("folder", "inbox")
        box = {"inbox": "in", "sent": "sent", "drafts": "out", "archive": "archive"}.get(folder)
        mid = urllib.parse.unquote(mid)
        if folder.startswith("custom:") and folder[7:].isdigit():
            with sqlite3.connect(DB_PATH) as db:
                assignment = db.execute("SELECT box FROM mailbox_folder_messages WHERE callsign=? AND folder_id=? AND mid=?", (session["callsign"], int(folder[7:]), mid)).fetchone()
            box = assignment[0] if assignment else None
        if not box or not re.fullmatch(r"[A-Za-z0-9._-]+", mid):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid mailbox message"})
            return
        try:
            pat_mailbox_request(session, box, mid, "DELETE")
            if folder.startswith("custom:"):
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("DELETE FROM mailbox_folder_messages WHERE callsign=? AND folder_id=? AND mid=?", (session["callsign"], int(folder[7:]), mid))
                    db.commit()
            self.send_json(HTTPStatus.OK, {"source": "pat", "state": "READY", "deleted": True})
        except RuntimeError as exc:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "source": "pat"})


def main():
    init_db()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
