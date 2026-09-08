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
import atexit
import shlex
import http.cookies
import json
import math
import os
import re
import secrets
import socket
import struct
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
    from registration import registration_status, activate as activate_license
except ModuleNotFoundError:  # direct import by the repository test loader
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    sys.path.insert(0, "/opt/n0jcg-winlink/tools")
    from winlink_templates import catalog as template_catalog
    from winlink_templates import render as render_template
    from winlink_templates import update_library as update_template_library
    from rms_gateways import cache_payload, enrich_nearest, location_state, refresh_cache, set_simulated_location
    from registration import registration_status, activate as activate_license


HOST = os.environ.get("N0JCG_WEBMAIL_HOST", "127.0.0.1")
PORT = int(os.environ.get("N0JCG_WEBMAIL_PORT", "8097"))
PAT_BIN = os.environ.get("N0JCG_PAT_BIN", "/usr/local/bin/pat")
# Packet RMS exchanges can take several minutes while downloading messages;
# this is an exchange timeout, not an authentication timeout.
PAT_TIMEOUT = int(os.environ.get("N0JCG_PAT_AUTH_TIMEOUT", "300"))
# The browser must not receive a mailbox session until CMS secure login is
# accepted. This wait covers RF connection and challenge exchange only; the
# subsequent mailbox transfer remains asynchronous.
# Packet RMS secure-login can require several RF retries before CMS hands
# control to Pat. Keep the on-demand process alive through that exchange;
# this is not the overall mailbox-transfer timeout.
PAT_LOGIN_WAIT = int(os.environ.get("N0JCG_PAT_LOGIN_WAIT_SECONDS", "180"))
PAT_POST_AUTH_TIMEOUT = int(os.environ.get("N0JCG_PAT_POST_AUTH_TIMEOUT_SECONDS", "180"))
PAT_PROGRESS_GRACE = int(os.environ.get("N0JCG_PAT_PROGRESS_GRACE_SECONDS", str(PAT_POST_AUTH_TIMEOUT)))
# Allow the radio, AGWPE bridge, and PTT path to settle after a forced stop.
# This is deliberately independent of the radio profile and RMS settings.
PAT_RF_COOLDOWN_SECONDS = int(os.environ.get("N0JCG_PAT_RF_COOLDOWN_SECONDS", "10"))
GPS_RF_GUARD = "/usr/local/sbin/n0jcg-gps-rf-guard"
OPERATOR_SETTINGS_SCRIPT = os.environ.get("N0JCG_OPERATOR_SETTINGS_SCRIPT", "/opt/n0jcg-winlink/tools/apply_operator_settings.py")
PAT_TELNET_URL = os.environ.get("N0JCG_PAT_TELNET_URL", "telnet://{mycall}:CMSTelnet@cms.winlink.org:8772/wl2k")
PAT_CONNECT_URL = os.environ.get("N0JCG_PAT_CONNECT_URL", "")
PAT_PACKET_CALLSIGN = os.environ.get("N0JCG_PACKET_CALLSIGN", "")
PAT_AGWPE_ADDR = os.environ.get("N0JCG_PAT_AGWPE_ADDR", "localhost:8002")
PAT_TRACE = os.environ.get("N0JCG_PAT_TRACE", "0") == "1"
# A value greater than zero enables an operator-selected idle timeout. The
# default is browser-session lifetime so ordinary page refreshes and long-lived
# mailbox work do not unexpectedly sign the user out.
SESSION_IDLE = int(os.environ.get("N0JCG_SESSION_IDLE_SECONDS", "0"))
STATE_DIR = Path(os.environ.get("N0JCG_WEBMAIL_STATE_DIR", "/var/lib/n0jcg-winlink-webmail"))
RADIO_PROFILE_PATH = STATE_DIR / "radio-profile.conf"
PAT_BASE_CONFIG = os.environ.get("N0JCG_PAT_BASE_CONFIG", "")
DB_PATH = STATE_DIR / "webmail.sqlite3"
EMAIL_RE = re.compile(r"^([A-Z0-9][A-Z0-9-]{2,15})@winlink\.org$", re.I)
CONTACT_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.I)
FAILURE_RE = re.compile(r"secure login failed|authentication failed|login failed|invalid password|unknown callsign|does not match login callsign", re.I)
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
    # The UI accepts the operator's callsign alone; Winlink's mailbox domain
    # is implicit. Continue accepting a complete address for API compatibility.
    if "@" not in account:
        account = f"{account}@WINLINK.ORG"
    match = EMAIL_RE.fullmatch(account)
    if not match:
        raise ValueError("enter a valid Winlink callsign")
    return account, match.group(1)


def init_db():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_accounts (email TEXT PRIMARY KEY, callsign TEXT NOT NULL, first_validated_at INTEGER NOT NULL, last_validated_at INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_signatures (callsign TEXT PRIMARY KEY, signature TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS mailbox_contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, callsign TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, UNIQUE(callsign,email))")
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
    # The standard client-side Pat build applies this at the FBB proposal
    # boundary, so trial mode accepts exactly one inbound message without
    # terminating the process before the message is committed to disk.
    config["max_inbound_messages"] = 0 if registration_status()["registered"] else 1
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


def meaningful_pat_error(detail, stage=""):
    """Turn common Pat/RMS failures into operator-actionable diagnostics."""
    text = str(detail or "").strip()
    lowered = text.lower()
    if "disconnected from station" in lowered or "dm res" in lowered or "station is busy" in lowered:
        return "The RMS is still clearing the previous packet session. Wait 1 minute, then try the Winlink login again."
    if "port closed" in lowered or "unable to establish connection" in lowered:
        return "The RMS gateway did not accept the packet connection. Verify the RMS target, frequency, 1200-AFSK mode, radio audio, and PTT path."
    if "retryout" in lowered or "connection timed out" in lowered or "timed out" in lowered:
        return "The radio reached the connection attempt but the RMS did not complete the packet session before timeout. Check frequency, squelch, audio level, and whether the selected RMS is reachable."
    if "connection lost" in lowered or "closed network connection" in lowered:
        if stage == "mailbox_records":
            return "The RMS sent mailbox records, but the mailbox summary (F>) did not reach the client. Check the RMS/KISS/Dire Wolf downlink path."
        if stage in {"mailbox_index", "password_sent", "username_sent"}:
            return "The RMS connection closed before the mailbox index was returned. The secure login stage completed, but the gateway did not finish opening the mailbox."
        return "The packet connection closed unexpectedly. Check the DigiRig audio/PTT path and review the operator RF log for retries."
    if "unexpected protocol line" in lowered:
        return "The RMS returned a protocol response that Pat could not interpret. Confirm the selected target is a Winlink RMS Packet gateway, not a node or Telnet prompt."
    if "read-only file system" in lowered or "permission denied" in lowered:
        return "The local Winlink mailbox storage is not writable. Check Pi disk space and permissions for the Pat mailbox directory."
    if "exit 126" in lowered or "no such file or directory" in lowered:
        return "The Pat transport command could not be started. Ask the operator to verify the installed Pat client and configured executable path."
    if "login failed" in lowered or "invalid password" in lowered or "authentication failed" in lowered:
        return "Winlink rejected the secure-login credentials. Verify the callsign and Winlink password, then try again."
    if "does not match login callsign" in lowered:
        return "Winlink rejected the callsign identity. Verify the callsign and Winlink password, then try again."
    return text or "The Winlink client stopped without reporting a specific reason. Review the operator diagnostics and Pat log."


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
            return False, meaningful_pat_error(output, "connecting")
        if not SUCCESS_RE.search(output):
            return False, "AX.25 connected, but Pat did not begin the Winlink mailbox exchange."
        transport = "Packet RMS" if connect_url.startswith("ax25") else "Winlink CMS"
        return True, f"{transport} authentication succeeded; isolated Pat mailbox initialized."
    except subprocess.TimeoutExpired:
        return False, "The RMS server did not complete the authentication exchange before timeout. Verify the selected gateway and RF path, then try again."
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
        payload = {key: job.get(key) for key in ("state", "stage", "stage_label", "message", "started_at", "updated_at", "received", "sent", "pending_count", "outgoing_count", "window_count", "rms_target", "last_line")}
        payload["registration"] = registration_status()
        payload["trial_limit_reached"] = bool(job.get("trial_limit_reached"))
        payload["proposal_count"] = int(job.get("proposal_count") or 0)
        # RMS/PAT may transfer messages in several FBB windows (for example
        # 5 + 5 + 1).  pending_count is the immutable session total; the
        # current window is reported separately so it cannot change 11/11
        # progress into 5/5 and then 1/1.
        total_offered = payload["proposal_count"] or (int(payload["pending_count"]) if payload["pending_count"] is not None else 0)
        payload["offered_count"] = total_offered
        payload["pending_count"] = total_offered if total_offered else payload["pending_count"]
        pending = payload["pending_count"]
        received = int(payload.get("received") or 0)
        payload["remaining_count"] = max(int(pending) - received, 0) if pending is not None else None
        payload["progress_percent"] = min(100, round((received / int(pending)) * 100)) if pending else 0
        return payload


def record_sync_event(job, source, message):
    """Keep a bounded, redacted timeline for the operator debug view."""
    if not job or not message:
        return
    text = re.sub(r"(?i)(password|passcode|secure-login-password)\s*[:=]\s*\S+", r"\1=[redacted]", str(message).strip())
    event = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": str(source), "message": text[-600:]}
    with LOCK:
        events = job.setdefault("events", [])
        events.append(event)
        del events[:-200]


def _journal_events(unit, source, limit=80):
    try:
        result = subprocess.run(["journalctl", "-u", unit, "-n", str(limit), "--no-pager", "-o", "short-iso"], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    events = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            events.append({"at": line[:25], "source": source, "message": line[26:].strip() or line})
    return events


def _digirig_serial_device():
    """Return the stable DigiRig serial alias or a non-GPS USB serial path."""
    aliases = (Path("/dev/digirig-serial"), Path("/dev/digirig-ptt"))
    for path in aliases:
        if path.exists():
            return str(path)
    for path in sorted(Path("/dev/serial/by-id").glob("*") if Path("/dev/serial/by-id").exists() else []):
        name = path.name.lower()
        if any(token in name for token in ("gps", "gnss", "u-blox")):
            continue
        if path.exists():
            return str(path)
    for path in sorted(Path("/dev").glob("ttyUSB*")):
        if path.exists():
            return str(path)
    return None


def sync_debug(callsign):
    with LOCK:
        job = SYNC_JOBS.get(callsign)
        pat_events = list(job.get("events", [])) if job else []
        snapshot = sync_status(callsign)
    serial_device = _digirig_serial_device()
    devices = {"audio": Path("/dev/snd").exists(), "serial": bool(serial_device), "ptt": bool(serial_device)}
    devices["serial_path"] = serial_device
    devices["ptt_path"] = serial_device
    events = pat_events + _journal_events("n0jcg-direwolf.service", "Dire Wolf") + _journal_events("n0jcg-agwpe-identity-bridge.service", "AGW bridge")
    events.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": "DigiRig", "message": f"audio={'present' if devices['audio'] else 'missing'}, serial={'present' if devices['serial'] else 'missing'}, PTT={'present' if devices['ptt'] else 'missing'}"})
    events.sort(key=lambda item: item.get("at", ""))
    return {"sync": snapshot, "events": events[-300:], "services": {"Dire Wolf": service_state("n0jcg-direwolf.service"), "AGW bridge": service_state("n0jcg-agwpe-identity-bridge.service"), "webmail": service_state("n0jcg-webmail.service")}, "devices": devices, "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def terminate_pat_process(process):
    """Stop Pat promptly and release its AGW/PTT connection."""
    if not process or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def cancel_pat_job(job, message):
    """Mark a sync cancelled and terminate its transport process."""
    if not job:
        return
    with LOCK:
        job["state"] = "ERROR"
        job["stage"] = "cancelled"
        job["message"] = message
        job["error_event"].set()
        process = job.get("process")
    terminate_pat_process(process)


def mark_post_auth_sync_failure(job, message):
    """Record a transport failure without invalidating the Winlink session."""
    if not job:
        return
    with LOCK:
        job["state"] = "AUTHENTICATED"
        job["stage"] = "failed"
        job["stage_label"] = "Synchronization failed"
        job["message"] = message
        job["error_event"].set()


def stop_all_pat_sessions(message, cooldown=True):
    """Stop all appliance RF sessions and optionally wait for RF/PTT settle."""
    with LOCK:
        jobs = list(SYNC_JOBS.values())
        SESSIONS.clear()
    had_live_process = False
    for job in jobs:
        process = job.get("process")
        had_live_process = had_live_process or bool(process and process.poll() is None)
        cancel_pat_job(job, message)
    if cooldown and had_live_process and PAT_RF_COOLDOWN_SECONDS > 0:
        time.sleep(PAT_RF_COOLDOWN_SECONDS)
    return had_live_process


def reset_single_user_appliance(message):
    """Enforce the appliance's one-user-at-a-time mailbox boundary."""
    return stop_all_pat_sessions(message, cooldown=True)


def _pause_gps_for_rf() -> bool:
    """Release the GPS serial device while the DigiRig owns the USB bus."""
    try:
        result = subprocess.run(["sudo", "-n", GPS_RF_GUARD, "pause"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _resume_gps_after_rf(paused: bool) -> None:
    if not paused:
        return
    try:
        subprocess.run(["sudo", "-n", GPS_RF_GUARD, "resume"], capture_output=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        pass


def _pat_sync_worker(callsign, password, config, process, job):
    """Keep the RF exchange alive after CMS authentication and report progress."""
    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            if PAT_TRACE:
                elapsed_ms = int((time.monotonic() - job["monotonic_started"]) * 1000)
                print(f"PAT +{elapsed_ms}ms line: {line[-500:]}", flush=True)
            with LOCK:
                job["updated_at"] = time.time()
                job["last_line"] = line[-500:]
                record_sync_event(job, "Pat/RMS", line)
                # A CMS banner, ;PQ challenge, or FF mailbox command is not
                # proof that the Winlink credentials were accepted.  Pat can
                # reach these points before the server rejects an invalid
                # callsign/password.  Handle rejection first so the login
                # waiter cannot turn a failed attempt into a mailbox session.
                if FAILURE_RE.search(line):
                    job["state"] = "ERROR"
                    job["stage"] = "failed"
                    job["stage_label"] = "Authentication rejected"
                    job["message"] = meaningful_pat_error(line, job.get("stage", ""))
                    job["error_event"].set()
                elif re.search(r";FW:\s*([A-Z0-9][A-Z0-9-]{2,15})", line, re.I):
                    fw_match = re.search(r";FW:\s*([A-Z0-9][A-Z0-9-]{2,15})", line, re.I)
                    if fw_match.group(1).upper() != callsign.upper():
                        job["state"] = "ERROR"
                        job["stage"] = "failed"
                        job["stage_label"] = "Authentication rejected"
                        job["message"] = "Winlink rejected the callsign identity. Verify the callsign and Winlink password, then try again."
                        job["error_event"].set()
                    else:
                        job["identity_confirmed"] = True
                        job["stage"] = "authenticating"
                        job["stage_label"] = "Secure login in progress"
                        job["message"] = "Winlink callsign confirmed; waiting for the RMS secure-login result."
                elif "Connected to CMS" in line:
                    job["stage"] = "username_sent"
                    job["stage_label"] = "Username sent"
                    job["message"] = "RMS connected; Winlink callsign sent. Waiting for the RMS packet-session response."
                elif re.search(r"Stream .*Connected to|Connected to [A-Z0-9-]+(?:\s|$)|Connected to .*\(v", line, re.I):
                    job["stage"] = "rms_connected"
                    job["stage_label"] = "RMS connected"
                    job["message"] = "RMS connected; establishing the packet session."
                elif re.search(r"Login \[\d+\]|;PQ", line, re.I):
                    job["stage"] = "password_sent"
                    job["stage_label"] = "Secure response sent"
                    job["message"] = "RMS secure-login challenge received; protected response sent."
                    if line.startswith(";PQ"):
                        job["stage"] = "authenticating"
                        job["stage_label"] = "Secure login in progress"
                        job["message"] = "Winlink secure-login challenge exchanged; waiting for the authenticated mailbox response."
                elif line.lstrip(">") == "FF" and job.get("stage") not in {"downloading", "uploading", "complete"}:
                    job["ff_seen"] = True
                    job["stage"] = "authenticating"
                    job["stage_label"] = "Secure login in progress"
                    job["message"] = "Secure login is still in progress; waiting for the RMS authorization result."
                elif re.match(r"^>?PM(?:\s|:)|^;PM", line, re.I):
                    job["post_auth_deadline"] = time.time() + PAT_PROGRESS_GRACE
                    proposal = re.match(r"^(?:;PM:|>?PM:?)\s+\S+\s+([A-Z0-9]+)\b", line, re.I)
                    if proposal:
                        job.setdefault("proposal_ids", set()).add(proposal.group(1).upper())
                        job["proposal_count"] = len(job["proposal_ids"])
                        job["pending_count"] = max(int(job.get("pending_count") or 0), job["proposal_count"])
                        job["message"] = f"Mailbox proposal received; {job['proposal_count']} new message(s) offered, {job['proposal_count']} remaining."
                    # A PM proposal is not by itself the final secure-login
                    # result. Keep the login gate closed until the RMS sends
                    # a later authenticated mailbox record/transfer result.
                    job["stage"] = "authenticating"
                    job["stage_label"] = "Secure login in progress"
                    job["message"] = "RMS mailbox proposal received; waiting for authenticated mailbox records."
                elif line.startswith(">FC EM"):
                    job["post_auth_deadline"] = time.time() + PAT_PROGRESS_GRACE
                    job["outgoing_count"] = int(job.get("outgoing_count") or 0) + 1
                    job["stage"] = "uploading"
                    job["stage_label"] = "Uploading queued mail"
                    job["message"] = f"RMS accepted the mailbox session; sending {job['outgoing_count']} queued message(s)."
                elif re.match(r"^>?F>(?:\s|$)|^>?FC(?:\s|$)", line, re.I):
                    job["post_auth_deadline"] = time.time() + PAT_PROGRESS_GRACE
                    job["stage"] = "mailbox_records"
                    job["stage_label"] = "Mailbox records received"
                    job["message"] = "RMS mailbox records received; waiting for the mailbox summary (F>)."
                elif line.startswith(">FS"):
                    job["post_auth_deadline"] = time.time() + PAT_PROGRESS_GRACE
                    job["stage"] = "mailbox_selection"
                    job["stage_label"] = "Message selection sent"
                    job["message"] = "Message selection sent; waiting for the RMS download."
                elif re.search(r"^Accepting\s+\S+|^>Accepting\s+\S+", line, re.I):
                    # This is Pat's client-side confirmation that the RMS
                    # proposal was accepted for transfer. It is the first
                    # safe point at which the browser may receive a mailbox
                    # session; PM/FC/F>/FS are only intermediate protocol
                    # traffic and must not release the login gate.
                    job["post_auth_deadline"] = time.time() + PAT_PROGRESS_GRACE
                    job["state"] = "AUTHENTICATED"
                    job["stage"] = "downloading"
                    job["stage_label"] = "Mailbox opened"
                    job["message"] = "Winlink secure login accepted; mailbox transfer is starting."
                    job["auth_event"].set()
                elif re.search(r"\d+ proposal\(s\) received", line, re.I):
                    # This is the current FBB transfer window, not the
                    # mailbox total. Keep the total learned from PM records.
                    job["window_count"] = int(re.search(r"(\d+) proposal", line, re.I).group(1))
                    if not job.get("proposal_count"):
                        job["pending_count"] = job["window_count"]
                    job["stage"] = "downloading"
                    job["stage_label"] = "Mailbox opened"
                    job["message"] = "RMS proposals received; waiting for the client to accept the mailbox transfer."
                elif "No messages" in line or "0 proposal(s)" in line:
                    job["state"] = "AUTHENTICATED"
                    job["stage"] = "no_messages"
                    job["stage_label"] = "Mailbox checked"
                    job["message"] = "Mailbox synchronization complete; 0 new emails were received."
                    if not job.get("proposal_count"):
                        job["pending_count"] = 0
                    job["auth_event"].set()
                elif line.lstrip(">").strip() == "FQ":
                    if job.get("state") != "AUTHENTICATED":
                        if not job.get("ff_seen"):
                            job["state"] = "ERROR"
                            job["stage"] = "failed"
                            job["stage_label"] = "Authentication rejected"
                            job["message"] = "The RMS closed the session before secure login was accepted. Verify the callsign and Winlink password, then try again."
                            job["error_event"].set()
                            continue
                        # Some RMS sessions close with FQ immediately after
                        # the authenticated FF exchange when the mailbox has
                        # no proposals. Rejection/warning lines are handled
                        # before this branch, so this clean close is safe to
                        # treat as an authenticated empty mailbox.
                        job["state"] = "AUTHENTICATED"
                        job["stage"] = "no_messages"
                        job["stage_label"] = "Mailbox checked"
                        job["message"] = "Winlink secure login accepted; 0 new emails were received."
                        job["pending_count"] = 0
                        job["auth_event"].set()
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
                    job["stage"] = "complete"
                    job["stage_label"] = "Mailbox synchronization complete"
                    job["message"] = "Mailbox synchronization complete; 0 new emails were received and the packet session closed cleanly."
                    job["auth_event"].set()
                elif re.search(r"Receiving \[", line, re.I):
                    job["post_auth_deadline"] = time.time() + PAT_PROGRESS_GRACE
                    job["received"] = int(job.get("received") or 0) + 1
                    if job.get("download_limit") and job["received"] >= job["download_limit"]:
                        job["trial_limit_reached"] = True
                        job["message"] = "Trial limit reached; one message was downloaded. Register WES for unlimited message downloads."
                    remaining = max(int(job.get("pending_count") or 0) - job["received"], 0) if job.get("pending_count") is not None else None
                    job["state"] = "AUTHENTICATED"
                    if job.get("pending_count") and job["received"] >= job["pending_count"]:
                        job["stage"] = "finalizing"
                        job["stage_label"] = "Messages received"
                        job["message"] = f"All {job['received']} accepted message(s) received; finalizing the RMS session."
                    else:
                        job["stage"] = "downloading"
                        job["stage_label"] = "Downloading messages"
                        if remaining is None:
                            job["message"] = f"Mailbox opened; received {job['received']} message(s)."
                        else:
                            job["message"] = f"Downloading messages; received {job['received']} of {job['pending_count']}, {remaining} remaining."
                    job["auth_event"].set()
                elif re.search(r"\b(?:DISC|UA)\b|Disconnected|connection lost", line, re.I) and job.get("received", 0):
                    job["state"] = "AUTHENTICATED"
                    job["stage"] = "complete"
                    job["stage_label"] = "Transfer complete"
                    job["message"] = f"Mailbox transfer complete; received {job['received']} message(s) before the RMS session closed."
                    job["auth_event"].set()
        returncode = process.wait()
        with LOCK:
            if returncode == 0 and job["state"] == "AUTHENTICATED":
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE mailbox_queue SET state='SENT' WHERE callsign=? AND state='STAGED'", (callsign,))
                    db.commit()
                job["stage"] = "complete"
                job["stage_label"] = "Complete"
                job["message"] = "Mailbox synchronization complete."
            if returncode != 0 and job["state"] == "AUTHENTICATED":
                # Pat may return a non-zero status when the RMS closes a
                # long packet exchange after the mailbox data has already
                # been transferred.  Once the mailbox protocol has reached
                # a real result or data-transfer stage, this is a completed
                # (possibly partial) sync, not an authentication failure.
                # Treating every post-auth socket close as fatal logged users
                # out even though the newly received files were on disk.
                progressed_stages = {"downloading", "uploading", "finalizing", "no_messages", "complete"}
                if job.get("stage") in progressed_stages:
                    with sqlite3.connect(DB_PATH) as db:
                        db.execute("UPDATE mailbox_queue SET state='SENT' WHERE callsign=? AND state='STAGED'", (callsign,))
                        db.commit()
                    detail = job.get("last_line") or "The RMS closed the packet session after mailbox progress."
                    received = int(job.get("received") or 0)
                    offered = int(job.get("pending_count") or 0)
                    job["stage"] = "complete"
                    job["stage_label"] = "Transfer complete"
                    if offered and received < offered:
                        job["message"] = f"Mailbox transfer partially completed; received {received} of {offered} offered message(s). The RMS closed the packet session. {detail}"
                    elif received:
                        job["message"] = f"Mailbox synchronization completed; received {received} message(s) before the RMS closed the packet session. {detail}"
                    else:
                        job["message"] = f"Mailbox synchronization completed; the RMS closed the packet session after mailbox progress. {detail}"
                else:
                    offered = int(job.get("proposal_count") or 0)
                    if offered and not int(job.get("received") or 0):
                        mark_post_auth_sync_failure(job, f"RMS offered {offered} message(s), but the mailbox transfer did not begin. The client did not receive the RMS FC/F> mailbox summary; check the WES RF downlink and Dire Wolf receive path.")
                    else:
                        detail = job.get("last_line") or "Pat stopped after secure login before the mailbox index was received."
                        mark_post_auth_sync_failure(job, meaningful_pat_error(detail, job.get("stage", "")))
            elif job["state"] not in ("AUTHENTICATED", "ERROR"):
                if returncode == 0:
                    job["state"] = "ERROR"
                    job["stage"] = "failed"
                    job["stage_label"] = "Failed"
                    job["message"] = "Winlink exchange ended before the mailbox result was confirmed."
                    job["error_event"].set()
                else:
                    job["state"] = "ERROR"
                    job["stage"] = "failed"
                    job["stage_label"] = "Failed"
                    detail = job.get("last_line") or "The client stopped before reporting a connection attempt."
                    job["message"] = meaningful_pat_error(detail, job.get("stage", ""))
                    job["error_event"].set()
    except Exception as exc:
        with LOCK:
            authenticated = job.get("state") == "AUTHENTICATED"
        if authenticated:
            mark_post_auth_sync_failure(job, meaningful_pat_error(str(exc), job.get("stage", "")))
        else:
            with LOCK:
                job["state"] = "ERROR"
                job["stage"] = "failed"
                job["stage_label"] = "Failed"
                job["message"] = meaningful_pat_error(str(exc), job.get("stage", ""))
                job["error_event"].set()
    finally:
        _resume_gps_after_rf(bool(job.get("gps_paused")))
        config.unlink(missing_ok=True)


def _pat_sync_watchdog(job):
    """Stop a post-auth exchange that has stopped producing mailbox progress."""
    while True:
        process = job.get("process")
        if not process or process.poll() is not None:
            return
        with LOCK:
            deadline = job.get("post_auth_deadline")
            state = job.get("state")
            stage = job.get("stage")
        if deadline and time.time() >= deadline and state == "AUTHENTICATED" and stage in ("authenticating", "mailbox_index", "mailbox_records"):
            if stage == "mailbox_records":
                timeout_message = "RMS mailbox records were received, but the mailbox summary (F>) did not arrive before timeout. Check the RMS/KISS/Dire Wolf downlink path, then try Refresh."
            else:
                timeout_message = "Secure login was accepted, but the RMS did not return the mailbox index in time. Verify the selected gateway and RF receive path, then try Refresh."
            mark_post_auth_sync_failure(job, timeout_message)
            terminate_pat_process(process)
            return
        time.sleep(1)


def start_pat_sync(callsign, password, restart=False):
    mailbox_dir = STATE_DIR / "mailbox" / callsign
    mailbox_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(mailbox_dir, 0o700)
    with LOCK:
        existing = SYNC_JOBS.get(callsign)
        if existing and existing.get("process") and existing["process"].poll() is None:
            if not restart:
                return existing
            # A new login must never inherit a Pat process left at an old
            # CMS prompt. Refresh/sync calls reuse a healthy active process,
            # but authentication starts a completely fresh RF session.
            existing["state"] = "ERROR"
            existing["stage"] = "cancelled"
            existing["stage_label"] = "Restarting"
            existing["message"] = "Previous Packet RMS attempt stopped; starting a fresh authentication session."
            existing["error_event"].set()
            old_process = existing.get("process")
        else:
            old_process = None
    terminate_pat_process(old_process)
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
    registration = registration_status()
    gps_paused = _pause_gps_for_rf()
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env={**os.environ, "PAT_MYCALL": callsign, "PAT_SECURE_LOGIN_PASSWORD": password})
    except FileNotFoundError:
        alternate = shutil.which("pat")
        if not alternate:
            _resume_gps_after_rf(gps_paused)
            config.unlink(missing_ok=True)
            raise RuntimeError("Pat client is not installed on the appliance.")
        command[0] = alternate
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env={**os.environ, "PAT_MYCALL": callsign, "PAT_SECURE_LOGIN_PASSWORD": password})
    job = {"callsign": callsign, "process": process, "state": "CONNECTING", "stage": "connecting", "stage_label": "Contacting RMS", "message": f"Contacting Packet RMS gateway {profile_target or 'configured target'}.", "rms_target": profile_target, "started_at": time.time(), "monotonic_started": time.monotonic(), "updated_at": time.time(), "received": 0, "sent": 0, "pending_count": None, "proposal_count": 0, "proposal_ids": set(), "window_count": None, "outgoing_count": 0, "download_limit": None if registration["registered"] else 1, "trial_limit_reached": False, "last_line": "", "events": [], "auth_event": threading.Event(), "error_event": threading.Event(), "gps_paused": gps_paused}
    with LOCK:
        SYNC_JOBS[callsign] = job
    record_sync_event(job, "Pat/RMS", f"Session started; target={profile_target or 'configured target'}")
    threading.Thread(target=_pat_sync_worker, args=(callsign, password, config, process, job), daemon=True, name=f"pat-sync-{callsign}").start()
    threading.Thread(target=_pat_sync_watchdog, args=(job,), daemon=True, name=f"pat-watchdog-{callsign}").start()
    return job


def wait_for_pat_auth(job, timeout=35):
    deadline = time.monotonic() + timeout
    process_exit_grace_deadline = None
    while time.monotonic() < deadline:
        if job["auth_event"].is_set() and job.get("state") == "AUTHENTICATED":
            return True, "Winlink secure login accepted; mailbox synchronization is continuing."
        if job["error_event"].is_set():
            return False, job.get("message", "Winlink authentication failed. The RMS server stopped responding before secure login completed.")
        process = job.get("process")
        if process and process.poll() is not None:
            # stdout is consumed by a separate worker. Pat may exit directly
            # after emitting the final FQ or rejection line, so give the
            # worker a brief drain window before treating process exit as a
            # failed authentication.
            if process_exit_grace_deadline is None:
                process_exit_grace_deadline = time.monotonic() + 2
            elif time.monotonic() >= process_exit_grace_deadline:
                return False, job.get("message", "Winlink authentication failed before secure login was accepted.")
        time.sleep(0.25)
    stage = job.get("stage", "connecting")
    if stage == "connecting":
        message = "No RMS connection was confirmed before timeout. Verify the target, frequency, radio mode, audio, and PTT path."
    elif stage == "rms_connected":
        message = "The RMS server connection was established, but the gateway did not complete the packet session before timeout."
    elif stage == "username_sent":
        message = "The Winlink callsign was sent, but the RMS server did not return a usable packet-session response before timeout."
    else:
        message = "The RMS server did not complete the secure-login exchange before timeout. Review the operator RF log for retries or a disconnected session."
    cancel_pat_job(job, message)
    return False, message


def decode_pat_response(body):
    """Decode Pat mailbox API responses, including legacy Winlink text.

    Most responses are UTF-8 JSON. Some older or forwarded messages retain
    Windows-1252 punctuation such as byte 0x92 (a curly apostrophe). Decode
    those responses without rejecting the entire mailbox request.
    """
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


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
                    body = decode_pat_response(response.read())
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


CALIBRATION_LOCK = threading.Lock()
CALIBRATION_STOP = threading.Event()
CALIBRATION_PROCESS = None
CALIBRATION_THREAD = None
CALIBRATION_STATE = {
    "active": False,
    "started_at": None,
    "latest_level": None,
    "average_level": None,
    "rms_dbfs": None,
    "peak_dbfs": None,
    "sample_count": 0,
    "error": None,
}
AUTOGAIN_LOCK = threading.RLock()
AUTOGAIN_PROCESS = None
AUTOGAIN_STATE = {"active": False, "started_at": None, "message": "Ready to run.", "output": [], "last_attempt": None, "result": None}
AUTOGAIN_GAIN_RE = re.compile(r"(?:gain(?:=| to )|final gain )(?P<gain>\d+)/35")


def autogain_status():
    global AUTOGAIN_PROCESS
    with AUTOGAIN_LOCK:
        process = AUTOGAIN_PROCESS
        state = dict(AUTOGAIN_STATE)
    if process is not None and process.poll() is not None:
        with AUTOGAIN_LOCK:
            AUTOGAIN_STATE["active"] = False
            AUTOGAIN_STATE["message"] = AUTOGAIN_STATE.get("last_attempt") or f"Calibration finished (exit {process.returncode})"
            AUTOGAIN_PROCESS = None
            state = dict(AUTOGAIN_STATE)
    # Keep the idle response meaningful for older cached operator pages too.
    # Those pages display state.message when their polling callback runs; the
    # former "Not armed"/"Not running" text made a healthy control look dead.
    if not state.get("active") and state.get("message") in ("Not armed.", "Not running"):
        state["message"] = "Ready to run."
    state["gain"] = None
    for line in reversed(state.get("output") or []):
        match = AUTOGAIN_GAIN_RE.search(line)
        if match:
            state["gain"] = int(match.group("gain"))
            break
    return state


def start_autogain():
    global AUTOGAIN_PROCESS
    with AUTOGAIN_LOCK:
        if AUTOGAIN_PROCESS is not None and AUTOGAIN_PROCESS.poll() is None:
            return autogain_status()
        process = subprocess.Popen(
            ["/usr/local/sbin/n0jcg-wes-direwolf-autogain"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        AUTOGAIN_PROCESS = process
        AUTOGAIN_STATE.update({"active": True, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "message": "Waiting for operator RMS test", "output": [], "last_attempt": None, "result": None})
        threading.Thread(target=_autogain_output_worker, args=(process,), daemon=True, name="wes-autogain-output").start()
    return autogain_status()


def _autogain_output_worker(process):
    for line in process.stdout or ():
        with AUTOGAIN_LOCK:
            clean = line.rstrip()
            AUTOGAIN_STATE["output"] = (AUTOGAIN_STATE.get("output") or [])[-99:] + [clean]
            if clean.startswith("Attempt "):
                AUTOGAIN_STATE["last_attempt"] = clean
            if clean.startswith(("Calibration complete:", "Calibration incomplete", "S/N stopped", "Calibration error")):
                AUTOGAIN_STATE["result"] = clean
            AUTOGAIN_STATE["message"] = clean or AUTOGAIN_STATE.get("message", "")


def stop_autogain():
    global AUTOGAIN_PROCESS
    with AUTOGAIN_LOCK:
        process = AUTOGAIN_PROCESS
        AUTOGAIN_PROCESS = None
        AUTOGAIN_STATE["active"] = False
        AUTOGAIN_STATE["message"] = "Stopped by operator"
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    return autogain_status()


def _restart_direwolf(action):
    result = subprocess.run(["sudo", "-n", "systemctl", action, "n0jcg-direwolf.service"], capture_output=True, text=True, timeout=8)
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown systemctl error").strip()
        raise RuntimeError(f"Could not {action} Dire Wolf: {detail}")


def _audio_calibration_worker():
    global CALIBRATION_PROCESS
    try:
        process = subprocess.Popen(
            ["arecord", "-D", "plughw:Device,0", "-f", "S16_LE", "-r", "44100", "-c", "1", "-t", "raw"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        with CALIBRATION_LOCK:
            CALIBRATION_PROCESS = process
        total_samples = 0
        window_rms = []
        while not CALIBRATION_STOP.is_set():
            raw = process.stdout.read(1024)
            if not raw:
                break
            if len(raw) % 2:
                raw = raw[:-1]
            values = struct.unpack(f"<{len(raw) // 2}h", raw)
            if not values:
                continue
            rms = math.sqrt(sum(value * value for value in values) / len(values))
            peak = max(abs(value) for value in values)
            total_samples += len(values)
            window_rms.append(rms)
            window_rms = window_rms[-5:]
            with CALIBRATION_LOCK:
                CALIBRATION_STATE.update({
                    "latest_level": round(100 * peak / 32768),
                    "average_level": round(100 * sum(window_rms) / len(window_rms) / 32768, 1),
                    "rms_dbfs": round(20 * math.log10(max(rms, 1) / 32768), 1),
                    "peak_dbfs": round(20 * math.log10(max(peak, 1) / 32768), 1),
                    "sample_count": total_samples,
                })
    except (OSError, subprocess.SubprocessError, ValueError, struct.error) as exc:
        with CALIBRATION_LOCK:
            CALIBRATION_STATE["error"] = str(exc)
    finally:
        with CALIBRATION_LOCK:
            process = CALIBRATION_PROCESS
            CALIBRATION_PROCESS = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()


def stop_audio_calibration():
    global CALIBRATION_THREAD
    with CALIBRATION_LOCK:
        was_active = bool(CALIBRATION_STATE["active"])
        CALIBRATION_STATE["active"] = False
    CALIBRATION_STOP.set()
    thread = CALIBRATION_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=3)
    CALIBRATION_THREAD = None
    if was_active:
        _restart_direwolf("start")


def start_audio_calibration():
    global CALIBRATION_THREAD
    stop_audio_calibration()
    _restart_direwolf("stop")
    CALIBRATION_STOP.clear()
    with CALIBRATION_LOCK:
        CALIBRATION_STATE.update({"active": True, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "latest_level": None, "average_level": None, "rms_dbfs": None, "peak_dbfs": None, "sample_count": 0, "error": None})
    CALIBRATION_THREAD = threading.Thread(target=_audio_calibration_worker, daemon=True, name="audio-calibration")
    CALIBRATION_THREAD.start()


atexit.register(stop_audio_calibration)


def audio_calibration_status():
    """Observe Dire Wolf without opening a second audio device or transmitter."""
    with CALIBRATION_LOCK:
        state = dict(CALIBRATION_STATE)
    lines = []
    try:
        result = subprocess.run(
            ["journalctl", "-u", "n0jcg-direwolf.service", "-n", "160", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=4,
        )
        if result.returncode == 0:
            lines = result.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        pass
    levels = [int(match.group(1)) for line in lines if (match := re.search(r"audio level = (\d+)", line))]
    frames = [line for line in lines if re.search(r"\] .*\((?:I|RR|UA|SABM|DISC)", line)]
    if state["active"] and state["sample_count"]:
        rms_dbfs = state["rms_dbfs"]
        peak_dbfs = state["peak_dbfs"]
        # Keep a safety margin from both clipping and the noise floor.  The
        # former -3 dBFS/-45 dBFS boundaries made the green band too wide;
        # readings at either edge produced unreliable packet decoding.
        if peak_dbfs is not None and peak_dbfs >= -6:
            quality = "too hot / clipping"
            guidance = "Noise is clipping the DigiRig input. Lower the WES radio volume, then wait for a stable reading."
        elif rms_dbfs is not None and rms_dbfs <= -40:
            quality = "too quiet"
            guidance = "Noise is very low. Raise the WES radio volume one step at a time until the noise is visible but not clipping."
        else:
            quality = "usable noise floor"
            guidance = "Noise floor is usable. Stop the test, restore Dire Wolf, then send a short carrier and voice check."
        return {
            "active": True,
            "started_at": state["started_at"],
            "receive_only": True,
            "audio": {"latest_level": state["latest_level"], "average_level": state["average_level"], "rms_dbfs": rms_dbfs, "peak_dbfs": peak_dbfs, "sample_count": state["sample_count"], "quality": quality, "guidance": guidance},
            "decoded_events": 0,
            "recent_events": [],
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    guidance = (
        "Adjust the physical radio volume. Aim for a stable mid-range level without clipping."
        if levels else
        "Waiting for an incoming RF/audio burst. Dire Wolf reports audio levels when it detects activity; transmit a brief test carrier or wait for a packet while adjusting volume."
    )
    return {
        "active": bool(state["active"]),
        "started_at": state["started_at"],
        "receive_only": True,
        "audio": {
            "latest_level": levels[-1] if levels else None,
            "average_level": round(sum(levels[-10:]) / min(10, len(levels)), 1) if levels else None,
            "sample_count": len(levels),
            "rms_dbfs": None,
            "peak_dbfs": None,
            "guidance": guidance,
        },
        "decoded_events": len(frames),
        "recent_events": lines[-30:],
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def operator_diagnostics():
    serial_device = _digirig_serial_device()
    devices = {
        "audio": {"path": "/dev/snd", "present": Path("/dev/snd").exists()},
        "serial": {"path": serial_device or "/dev/digirig-serial", "present": bool(serial_device)},
        "ptt": {"path": serial_device or "/dev/digirig-ptt", "present": bool(serial_device)},
    }
    templates = template_catalog()
    profile = radio_profile()
    return {
        "source": "local_probe",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "transmit_policy": "PACKET_SESSION_CONTROLLED",
        "services": {"webmail": service_state("n0jcg-webmail.service"), "pat": "on-demand", "direwolf": service_state("n0jcg-direwolf.service"), "agwpe_bridge": service_state("n0jcg-agwpe-identity-bridge.service")},
        "binaries": {"pat": bool(shutil.which("pat-winlink") or shutil.which("pat")), "direwolf": bool(shutil.which("direwolf"))},
        "devices": devices,
        "standard_forms": {"available": bool(templates.get("available")), "version": templates.get("version", ""), "count": len(templates.get("templates", []))},
        "packet": {"local_id": profile["N0JCG_PACKET_CALLSIGN"], "frequency_mhz": profile["N0JCG_PACKET_FREQUENCY"], "mode": "1200-AFSK", "rms_target": profile["N0JCG_RMS_TARGET"], "digipeater_path": profile["N0JCG_DIGIPEATER_PATH"], "auto_sync_minutes": int(profile["N0JCG_AUTO_SYNC_MINUTES"])},
    }


def radio_profile():
    values = {"N0JCG_PACKET_CALLSIGN": PAT_PACKET_CALLSIGN or "N0JCG-3", "N0JCG_PACKET_FREQUENCY": "145.070", "N0JCG_AUDIO_DEVICE": "plughw:Device,0", "N0JCG_PTT_DEVICE": "/dev/digirig-ptt", "N0JCG_AUTO_SYNC_MINUTES": "30", "N0JCG_RMS_TARGET": "N0JCG-10", "N0JCG_DIGIPEATER_PATH": ""}
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
    digipeater_path = str(data.get("digipeater_path") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9-]{3,15}", callsign):
        raise ValueError("packet station ID is invalid")
    if not re.fullmatch(r"[0-9]{2,3}(?:\.[0-9]{1,6})?", frequency):
        raise ValueError("frequency must be entered in MHz, for example 145.070")
    if not re.fullmatch(r"[0-9]+", auto_sync_minutes) or not 5 <= int(auto_sync_minutes) <= 1440:
        raise ValueError("automatic mailbox sync must be between 5 and 1440 minutes")
    if not re.fullmatch(r"[A-Z0-9-]{3,15}", rms_target):
        raise ValueError("RMS gateway target is invalid")
    if digipeater_path and not re.fullmatch(r"[A-Z0-9-]{3,15}(?:,[A-Z0-9-]{3,15})*", digipeater_path):
        raise ValueError("digipeater path is invalid")
    RADIO_PROFILE_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    profile_lines = [
        f"N0JCG_PACKET_CALLSIGN={callsign}",
        f"N0JCG_PACKET_FREQUENCY={frequency}",
        f"N0JCG_AUDIO_DEVICE={current['N0JCG_AUDIO_DEVICE']}",
        f"N0JCG_PTT_DEVICE={current['N0JCG_PTT_DEVICE']}",
        f"N0JCG_AUTO_SYNC_MINUTES={auto_sync_minutes}",
        f"N0JCG_RMS_TARGET={rms_target}",
        f"N0JCG_DIGIPEATER_PATH={digipeater_path}",
        "N0JCG_PACKET_MODE=1200-AFSK",
        f"N0JCG_PAT_CONNECT_URL=ax25+agwpe:///{digipeater_path + '/' if digipeater_path else ''}{rms_target}",
        "",
    ]
    RADIO_PROFILE_PATH.write_text("\n".join(profile_lines), encoding="utf-8")
    result = subprocess.run(["sudo", "/opt/n0jcg-winlink/tools/apply_radio_profile.sh"], capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "radio profile could not be applied").strip()[-500:]
        if "read-only" in detail.lower() or "os error 30" in detail.lower() or "errno 30" in detail.lower():
            raise RuntimeError("The Pi system filesystem is read-only. Remount / read-write, then save the radio profile again.")
        raise RuntimeError(detail)
    return radio_profile()


def operator_connectivity():
    config = {}
    try:
        path = Path("/etc/n0jcg-winlink/network.conf")
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                parsed = shlex.split(value, posix=True)
                config[key] = parsed[0] if parsed else ""
    except (OSError, ValueError):
        pass
    try:
        saved = subprocess.run(
            ["sudo", "-n", OPERATOR_SETTINGS_SCRIPT, "--read-network"],
            capture_output=True, text=True, timeout=5,
        )
        if saved.returncode == 0:
            config.update(json.loads(saved.stdout))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    operator_user = ""
    try:
        for line in Path("/etc/n0jcg-winlink/operator.conf").read_text(encoding="utf-8").splitlines():
            if line.startswith("N0JCG_OPERATOR_USER="):
                operator_user = line.split("=", 1)[1].strip().strip("'\"")
                break
    except OSError:
        try:
            first = Path("/etc/nginx/.htpasswd-n0jcg-winlink").read_text(encoding="utf-8").splitlines()[0]
            operator_user = first.split(":", 1)[0] if ":" in first else ""
        except (OSError, IndexError):
            pass
    def active(service):
        return subprocess.run(["systemctl", "is-active", "--quiet", service], capture_output=True).returncode == 0
    def enabled(service):
        return subprocess.run(["systemctl", "is-enabled", "--quiet", service], capture_output=True).returncode == 0
    return {
        "wifi_ssid": config.get("N0JCG_WIFI_SSID", ""),
        "wifi_password": config.get("wifi_password", ""),
        "wifi_device": config.get("N0JCG_WIFI_DEVICE", ""),
        "wifi_disabled": config.get("N0JCG_WIFI_DISABLED", "1") == "1",
        "hotspot_ssid": config.get("N0JCG_AP_SSID", "N0JCG-WES"),
        "hotspot_password": config.get("hotspot_password", ""),
        "auto_hotspot": config.get("N0JCG_AUTO_HOTSPOT", "1") == "1",
        "hotspot_active": active("n0jcg-network-fallback.service"),
        "usb_gadget_enabled": enabled("n0jcg-usb-gadget.service"),
        "usb_gadget_active": active("n0jcg-usb-gadget.service"),
        "operator_auth_configured": Path("/etc/nginx/.htpasswd-n0jcg-winlink").exists(),
        "operator_user": operator_user,
    }


def apply_operator_settings(data):
    operation = str(data.get("operation") or "network")
    settings = {
        "operation": operation,
        "wifi_ssid": str(data.get("wifi_ssid") or "").strip(),
        "wifi_password": str(data.get("wifi_password") or ""),
        "hotspot_ssid": str(data.get("hotspot_ssid") or "").strip(),
        "hotspot_password": str(data.get("hotspot_password") or ""),
        "auto_hotspot": data.get("auto_hotspot"),
        "disable_wifi": data.get("disable_wifi"),
        "usb_gadget": data.get("usb_gadget"),
        "operator_user": str(data.get("operator_user") or "").strip(),
        "operator_password": str(data.get("operator_password") or ""),
    }
    if operation not in {"network", "auth"}:
        raise ValueError("unknown operator settings operation")
    STATE_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    request = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=STATE_DIR, prefix="operator-settings-", suffix=".json", delete=False)
    try:
        json.dump(settings, request)
        request.close()
        result = subprocess.run(["sudo", "-n", OPERATOR_SETTINGS_SCRIPT, request.name], capture_output=True, text=True, timeout=45)
    finally:
        try:
            Path(request.name).unlink()
        except OSError:
            pass
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "operator settings could not be applied").strip()[-500:]
        raise RuntimeError(detail)
    return operator_connectivity()


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
    return {"authenticated": True, "email": session["email"], "callsign": session["callsign"], "source": "pat", "auth_state": sync_status(session["callsign"])["state"], "expires_at": expires_at, "registration": registration_status()}


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
                # This appliance is intentionally single-user. A new login
                # always invalidates prior browser sessions and stops every
                # Pat/RMS process, including jobs for a different callsign.
                reset_single_user_appliance("A new Winlink user signed in; the previous mailbox session was closed.")
                staged = stage_queued_messages({"callsign": callsign, "password": password})
                job = start_pat_sync(callsign, password, restart=True)
                authenticated, evidence = wait_for_pat_auth(job, PAT_LOGIN_WAIT)
                if not authenticated:
                    cancel_pat_job(job, evidence)
                    # Do not leave a failed login's AGWPE/RF session active
                    # while the browser reports the error or retries.
                    if PAT_RF_COOLDOWN_SECONDS > 0:
                        time.sleep(PAT_RF_COOLDOWN_SECONDS)
                    record_login_failure(client_id)
                    # Remove any browser cookie from the previous user as
                    # part of the failed attempt. A rejected replacement
                    # login must not leave an old mailbox session restorable.
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": evidence, "source": "pat"}, clear_session_cookie())
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
                # Trial access is intentionally one mailbox transfer per
                # login. Refreshing would otherwise provide a second
                # transfer without a fresh credential validation, so make it
                # an explicit re-login boundary. Registered installations
                # retain normal refresh behavior.
                if not registration_status()["registered"]:
                    with LOCK:
                        active_job = SYNC_JOBS.get(session["callsign"])
                        SESSIONS.pop(session["token"], None)
                    cancel_pat_job(active_job, "Trial mailbox session ended; sign in again for the next trial message.")
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "Trial mode requires a new Winlink login before another mailbox download.", "trial_relogin": True}, clear_session_cookie())
                    return
                restart = bool(data.get("restart")) if isinstance(data, dict) else False
                job = start_pat_sync(session["callsign"], session["password"], restart=restart)
                self.send_json(HTTPStatus.ACCEPTED, {"state": "SYNCING", "sync": sync_status(session["callsign"])})
                return
            if self.path == "/api/v1/auth/logout":
                session = session_from(self)
                if session:
                    with LOCK:
                        active_job = SYNC_JOBS.get(session["callsign"])
                    cancel_pat_job(active_job, "Mailbox synchronization stopped when the user logged out.")
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
            if self.path == "/api/v1/operator/registration":
                try:
                    result = activate_license(Path("/var/lib/n0jcg-winlink/registration.json"), data.get("license_serial") or data.get("license_key"), data.get("email"))
                except Exception as exc:
                    self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self.send_json(HTTPStatus.OK, {"registered": True, "registration": result})
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
                try:
                    payload = apply_radio_profile(data)
                except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "source": "radio_profile"})
                    return
                self.send_json(HTTPStatus.OK, {"saved": True, "profile": payload})
                return
            if self.path in {"/api/v1/operator/settings", "/api/v1/operator/network", "/api/v1/operator/auth"}:
                if self.path.endswith("/network"):
                    data["operation"] = "network"
                elif self.path.endswith("/auth"):
                    data["operation"] = "auth"
                try:
                    payload = apply_operator_settings(data)
                except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "source": "operator_settings"})
                    return
                self.send_json(HTTPStatus.OK, {"saved": True, "settings": payload, "warning": "Network changes may disconnect this browser."})
                return
            if self.path == "/api/v1/operator/audio-calibration":
                enabled = bool(data.get("enabled"))
                try:
                    start_audio_calibration() if enabled else stop_audio_calibration()
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
                    return
                self.send_json(HTTPStatus.OK, {"ok": True, "message": "Live receive-only audio meter started." if enabled else "Receive-only audio calibration stopped.", "calibration": audio_calibration_status()})
                return
            if self.path == "/api/v1/operator/audio-autogain":
                enabled = bool(data.get("enabled"))
                state = start_autogain() if enabled else stop_autogain()
                self.send_json(HTTPStatus.OK, {"ok": True, "message": "Automatic RMS receive calibration armed." if enabled else "Automatic RMS receive calibration stopped.", "autogain": state})
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
            if self.path == "/api/v1/account/contacts":
                session = session_from(self)
                if not session:
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                    return
                contact_id = data.get("id")
                name = str(data.get("name") or "").strip()
                email = str(data.get("email") or "").strip().lower()
                notes = str(data.get("notes") or "").strip()
                if not email or not CONTACT_EMAIL_RE.fullmatch(email):
                    raise ValueError("enter a valid contact email address")
                if len(name) > 120 or len(email) > 320 or len(notes) > 500:
                    raise ValueError("contact fields exceed the allowed length")
                now = int(time.time())
                with sqlite3.connect(DB_PATH) as db:
                    if contact_id:
                        try:
                            cursor = db.execute("UPDATE mailbox_contacts SET name=?,email=?,notes=?,updated_at=? WHERE id=? AND callsign=?", (name, email, notes, now, int(contact_id), session["callsign"]))
                        except sqlite3.IntegrityError:
                            self.send_json(HTTPStatus.CONFLICT, {"error": "that email address is already in the address book"})
                            return
                        if cursor.rowcount == 0:
                            self.send_json(HTTPStatus.NOT_FOUND, {"error": "contact not found"})
                            return
                    else:
                        try:
                            cursor = db.execute("INSERT INTO mailbox_contacts(callsign,name,email,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)", (session["callsign"], name, email, notes, now, now))
                            contact_id = cursor.lastrowid
                        except sqlite3.IntegrityError:
                            self.send_json(HTTPStatus.CONFLICT, {"error": "that email address is already in the address book"})
                            return
                    db.commit()
                self.send_json(HTTPStatus.OK, {"saved": True, "contact": {"id": int(contact_id), "name": name, "email": email, "notes": notes, "updated_at": now}})
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
        if self.path in {"/api/v1/operator/settings", "/api/v1/operator/network", "/api/v1/operator/auth"}:
            self.send_json(HTTPStatus.OK, {"settings": operator_connectivity()})
            return
        if self.path == "/api/v1/operator/registration":
            registration = registration_status()
            self.send_json(HTTPStatus.OK, {"registration": registration, "device_id": registration.get("serial_number")})
            return
        if self.path == "/api/v1/operator/audio-calibration":
            self.send_json(HTTPStatus.OK, audio_calibration_status())
            return
        if self.path == "/api/v1/operator/audio-autogain":
            self.send_json(HTTPStatus.OK, autogain_status())
            return
        if self.path == "/api/v1/auth/session":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"authenticated": False})
            else:
                self.send_json(HTTPStatus.OK, session_view(session))
            return
        if self.path.startswith("/api/v1/auth/progress?"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            callsign = str((query.get("callsign") or [""])[0]).strip().upper()
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{2,15}", callsign):
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid callsign"})
                return
            progress = sync_status(callsign)
            self.send_json(HTTPStatus.OK, {"callsign": callsign, **progress})
            return
        if self.path == "/api/v1/mail/sync":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            else:
                self.send_json(HTTPStatus.OK, sync_status(session["callsign"]))
            return
        if self.path == "/api/v1/mail/sync/debug":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            else:
                self.send_json(HTTPStatus.OK, sync_debug(session["callsign"]))
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
        if self.path == "/api/v1/account/registration":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            else:
                self.send_json(HTTPStatus.OK, registration_status())
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
        if self.path == "/api/v1/account/contacts":
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT id,name,email,notes,created_at,updated_at FROM mailbox_contacts WHERE callsign=? ORDER BY name COLLATE NOCASE,email COLLATE NOCASE", (session["callsign"],)).fetchall()
            self.send_json(HTTPStatus.OK, {"callsign": session["callsign"], "contacts": [{"id": row[0], "name": row[1], "email": row[2], "notes": row[3], "created_at": row[4], "updated_at": row[5]} for row in rows]})
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
        contact_prefix = "/api/v1/account/contacts/"
        if self.path.startswith(contact_prefix):
            if not session:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            contact_id = self.path[len(contact_prefix):]
            if not contact_id.isdigit():
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid contact id"})
                return
            with sqlite3.connect(DB_PATH) as db:
                cursor = db.execute("DELETE FROM mailbox_contacts WHERE id=? AND callsign=?", (int(contact_id), session["callsign"]))
                db.commit()
            if cursor.rowcount == 0:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "contact not found"})
            else:
                self.send_json(HTTPStatus.OK, {"deleted": True})
            return
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
