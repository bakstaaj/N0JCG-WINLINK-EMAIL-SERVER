import base64
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("n0jcg_webmail", ROOT / "api" / "n0jcg_webmail.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TEMPLATE_SPEC = importlib.util.spec_from_file_location("winlink_templates", ROOT / "api" / "winlink_templates.py")
TEMPLATE_MODULE = importlib.util.module_from_spec(TEMPLATE_SPEC)
TEMPLATE_SPEC.loader.exec_module(TEMPLATE_MODULE)
RMS_SPEC = importlib.util.spec_from_file_location("rms_gateways", ROOT / "api" / "rms_gateways.py")
RMS_MODULE = importlib.util.module_from_spec(RMS_SPEC)
RMS_SPEC.loader.exec_module(RMS_MODULE)


class WebmailHelpersTests(unittest.TestCase):
    def test_registration_uses_standard_n0jcg_license_contract(self):
        registration = (ROOT / "tools" / "registration.py").read_text(encoding="utf-8")
        ui = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('PRODUCT_ID = "winlink-email-appliance"', registration)
        self.assertIn('LICENSE_PREFIX = "N0JCG-WLA-"', registration)
        self.assertIn("def activate(path: Path, license_serial: str, email: str)", registration)
        self.assertIn('name="license_serial"', ui)
        self.assertIn('name="email"', ui)
        self.assertIn("Registered email", ui)

    def test_address_book_is_callsign_scoped_and_available_in_compose(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        ui = (ROOT / "ui" / "webmail" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS mailbox_contacts", source)
        self.assertIn("/api/v1/account/contacts", source)
        self.assertIn("WHERE callsign=?", source)
        self.assertIn('data-action="contacts"', ui)
        self.assertIn('id="contacts-view"', ui)
        self.assertIn('id="contact-options"', ui)
        self.assertIn("loadContacts()", script)
        self.assertIn("data-edit-contact", script)
        self.assertIn("data-delete-contact", script)

    def test_trial_download_limit_is_enforced_by_sync_worker(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('"download_limit": None if registration["registered"] else 1', source)
        self.assertIn('job["trial_limit_reached"] = True', source)
        self.assertIn("Register WES for unlimited message downloads", source)

    def test_trial_refresh_requires_a_new_login(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        ui = (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8")
        self.assertIn("Trial mode requires a new Winlink login", source)
        self.assertIn('"trial_relogin": True', source)
        self.assertIn("if (response.status === 401 && body.trial_relogin)", ui)
        self.assertIn("authGate.hidden = false", ui)

    def test_pat_response_accepts_legacy_windows_punctuation(self):
        body = b'{"subject":"Don\x92t miss out"}'
        self.assertEqual(MODULE.decode_pat_response(body), '{"subject":"Don\u2019t miss out"}')

    def test_pat_response_prefers_utf8(self):
        body = '{"subject":"Don\u2019t miss out"}'.encode("utf-8")
        self.assertEqual(MODULE.decode_pat_response(body), '{"subject":"Don\u2019t miss out"}')

    def test_webmail_session_cookie_is_not_persistent(self):
        cookie = MODULE.session_cookie("test-token")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Max-Age", cookie)

    def test_messages_require_double_click_to_open(self):
        script = (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8")
        self.assertEqual(script.count("button.addEventListener('dblclick', () => showMessage"), 2)
        self.assertNotIn("button.addEventListener('click', () => showMessage", script)
        self.assertIn("Double-click to open this message", script)

    def test_session_expiry_is_disabled_by_default(self):
        self.assertEqual(MODULE.SESSION_IDLE, 0)

    def test_successful_login_path_clears_rate_limit_counter(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        success_path = source.index("authenticated, evidence = wait_for_pat_auth")
        clear_path = source.index("clear_login_failures(client_id)", success_path)
        token_path = source.index("token = create_session", success_path)
        self.assertLess(clear_path, token_path)

    def test_login_wait_does_not_treat_challenge_or_ff_as_authentication(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        pq_block = source[source.index('elif re.search(r"Login \\[\\d+\\]|;PQ"'):source.index('elif line.lstrip(">") == "FF"')]
        ff_block = source[source.index('elif line.lstrip(">") == "FF"'):source.index('elif re.match(r"^>?PM')]
        self.assertNotIn('job["auth_event"].set()', pq_block)
        self.assertNotIn('job["auth_event"].set()', ff_block)
        self.assertIn("Accepting\\s+\\S+", source)
        self.assertIn("PM/FC/F>/FS are only intermediate protocol", source)
        self.assertIn("Handle rejection first", source)
        self.assertIn('if FAILURE_RE.search(line):', source)
        self.assertIn('job["stage_label"] = "Authentication rejected"', source)
        self.assertIn('if job.get("state") != "AUTHENTICATED":', source)
        self.assertIn("before secure login was accepted", source)
        self.assertIn("stdout is consumed by a separate worker", source)
        self.assertIn("process_exit_grace_deadline", source)
        self.assertIn("does not match login callsign", source)
        self.assertIn("Some RMS sessions close with FQ immediately", source)
        self.assertIn('job["ff_seen"] = True', source)

    def test_failed_login_stays_at_login_gate(self):
        script = (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8")
        login_success = script.index("if (!response.ok) throw new Error")
        workspace_open = script.index("workspace.hidden = false", login_success)
        self.assertLess(login_success, workspace_open)
        self.assertIn("No mailbox data was opened", script)
        self.assertIn("loginRetryUntil = Date.now() + 60000", script)
        self.assertIn("RMS session-clear wait is complete", script)
        self.assertIn("A new credential attempt must start with a blank, hidden mailbox", script)
        self.assertIn("currentCallsign = '';", script)
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('self.send_json(HTTPStatus.UNAUTHORIZED, {"error": evidence, "source": "pat"}, clear_session_cookie())', source)

    def test_sync_status_exposes_safe_transport_diagnostics(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('"rms_target", "last_line"', source)
        self.assertIn("The client stopped before reporting a connection attempt.", source)

    def test_mailbox_progress_keeps_total_separate_from_fbb_windows(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('"window_count"', source)
        self.assertIn('payload["offered_count"] = total_offered', source)
        self.assertIn('payload["progress_percent"] = min(100', source)
        self.assertIn("This is the current FBB transfer window, not the", source)
        ui = (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8")
        self.assertIn("Current RMS transfer window", ui)

    def test_post_auth_pat_exit_is_not_left_in_authenticated_state(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('if returncode != 0 and job["state"] == "AUTHENTICATED":', source)
        self.assertIn("PAT_POST_AUTH_TIMEOUT", source)
        self.assertIn("pat-watchdog", source)

    def test_post_auth_transport_close_after_progress_is_not_reported_as_login_failure(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('progressed_stages = {"downloading", "uploading", "finalizing", "no_messages", "complete"}', source)
        self.assertIn("RMS closed the packet session", source)

    def test_packet_login_wait_allows_rf_cms_handoff(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('N0JCG_PAT_LOGIN_WAIT_SECONDS", "180"', source)
        self.assertIn("PAT_LOGIN_WAIT", source)

    def test_operator_diagnostics_use_actual_packet_services(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('service_state("n0jcg-direwolf.service")', source)
        self.assertIn('service_state("n0jcg-agwpe-identity-bridge.service")', source)
        self.assertIn('"pat": "on-demand"', source)

    def test_gps_reads_multiple_reports_and_uses_stable_device_setup(self):
        source = (ROOT / "api" / "rms_gateways.py").read_text(encoding="utf-8")
        self.assertIn('["gpspipe", "-w", "-n", "10"]', source)
        script = (ROOT / "deploy" / "configure_gps.sh").read_text(encoding="utf-8")
        self.assertIn("/dev/serial/by-id", script)
        self.assertIn("gpsd.socket", script)
        self.assertIn("n0jcg-gps", script)
        self.assertIn("readlink -f", script)
        self.assertIn("gpsd treats DEVICES literally", script)

    def test_authentication_progress_has_safe_granular_stages(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('"stage_label": "Contacting RMS"', source)
        self.assertIn("RMS mailbox proposal received; waiting for authenticated mailbox records", source)
        self.assertIn('job["stage"] = "complete"', source)
        self.assertIn("packet session closed cleanly", source)
        self.assertIn('job["stage"] = "finalizing"', source)
        self.assertIn('job["received"] = int(job.get("received") or 0) + 1', source)
        self.assertIn("Mailbox transfer partially completed", source)
        self.assertIn("0 new emails were received", source)
        self.assertIn("def mark_post_auth_sync_failure(job, message):", source)
        self.assertIn('job["state"] = "AUTHENTICATED"', source)
        self.assertIn('"stage_label"] = "RMS connected"', source)
        self.assertIn("Waiting for the RMS packet-session response", source)
        self.assertIn("Secure login is still in progress; waiting for the RMS authorization result", source)
        self.assertNotIn("The client requested the mailbox index; waiting for authenticated RMS records", source)
        self.assertIn("RMS mailbox records received; waiting for the mailbox summary (F>)", source)
        self.assertIn("Message selection sent; waiting for the RMS download", source)
        self.assertIn("RMS mailbox records received; waiting for the mailbox summary (F>)", source)
        self.assertIn("RMS mailbox records were received, but the mailbox summary (F>) did not arrive", source)
        self.assertIn("The RMS server did not return a final mailbox/authentication result", (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8"))
        self.assertIn('"stage_label"] = "Username sent"', source)
        self.assertIn('"stage_label"] = "Secure response sent"', source)
        self.assertIn('/api/v1/auth/progress?', source)
        self.assertNotIn('job["message"] = f"Password', source)

    def test_nginx_allows_slow_packet_rms_login_to_finish(self):
        source = (ROOT / "deploy" / "nginx" / "n0jcg-winlink.conf").read_text(encoding="utf-8")
        self.assertIn("proxy_read_timeout 240s", source)
        self.assertIn("proxy_send_timeout 240s", source)

    def test_deployment_reboots_and_verifies_service_after_return(self):
        installer = (ROOT / "deploy" / "install_static_ui.sh").read_text(encoding="utf-8")
        deployer = (ROOT / "deploy" / "push_to_pi.sh").read_text(encoding="utf-8")
        self.assertIn("systemd-run", installer)
        self.assertIn("systemctl reboot", installer)
        self.assertIn("waiting for the Pi to reboot", deployer)
        self.assertIn("verified after reboot", deployer)

    def test_installer_installs_and_resolves_direwolf_binary(self):
        installer = (ROOT / "deploy" / "install_static_ui.sh").read_text(encoding="utf-8")
        service = (ROOT / "deploy" / "n0jcg-direwolf.service").read_text(encoding="utf-8")
        self.assertIn("apt-get install -y direwolf", installer)
        self.assertIn('DIREWOLF_BIN="$(command -v direwolf)"', installer)
        self.assertIn("@DIREWOLF_BIN@", service)

    def test_direwolf_audio_is_runtime_resolved_and_gain_is_nonfatal(self):
        service = (ROOT / "deploy" / "n0jcg-direwolf.service").read_text(encoding="utf-8")
        installer = (ROOT / "deploy" / "install_static_ui.sh").read_text(encoding="utf-8")
        gain = (ROOT / "tools" / "wes_apply_gain.py").read_text(encoding="utf-8")
        resolver = (ROOT / "tools" / "wes_audio_device.py").read_text(encoding="utf-8")
        self.assertIn("n0jcg-wes-audio-device", service)
        self.assertIn('RUNTIME_CONF="/run/n0jcg-winlink/direwolf.conf"', service)
        self.assertNotIn("amixer -c Device", service)
        self.assertIn("wes_audio_device.py", installer)
        self.assertIn("except (OSError, subprocess.CalledProcessError)", gain)
        self.assertIn("no ALSA capture cards", resolver)

    def test_connectivity_setup_detects_netplan_renderer_and_wifi_device(self):
        setup = (ROOT / "deploy" / "setup_connectivity.sh").read_text(encoding="utf-8")
        fallback = (ROOT / "deploy" / "n0jcg-network-fallback.sh").read_text(encoding="utf-8")
        self.assertIn("netplan get network.renderer", setup)
        self.assertIn("99-n0jcg-wes-networkmanager.yaml", setup)
        self.assertIn("N0JCG_WIFI_DEVICE", setup)
        self.assertIn('chmod 0600 "$NETPLAN_OVERRIDE"', setup)
        self.assertNotIn("netplan apply", setup)
        self.assertIn("ip route show default dev", fallback)

    def test_digirig_installer_creates_stable_serial_ptt_aliases(self):
        script = (ROOT / "deploy" / "setup_digirig.sh").read_text(encoding="utf-8")
        self.assertIn("70-n0jcg-digirig.rules", script)
        self.assertIn('idVendor}=="10c4"', script)
        self.assertIn('idVendor}=="1a86"', script)
        self.assertIn('SYMLINK+="digirig-serial"', script)
        self.assertIn('udevadm trigger --action=add --subsystem-match=tty', script)
        api = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn("def _digirig_serial_device():", api)
        self.assertIn("/dev/serial/by-id", api)

    def test_installer_checks_root_filesystem_is_writable(self):
        installer = (ROOT / "deploy" / "install_static_ui.sh").read_text(encoding="utf-8")
        self.assertIn('findmnt -T / -no OPTIONS', installer)
        self.assertIn('mount -o remount,rw /', installer)
        self.assertIn('root filesystem remains read-only', installer)
        self.assertIn('touch', installer)
        self.assertIn('/etc is not writable even though the root mount reports rw', installer)

    def test_webmail_sandbox_allows_radio_profile_config_only(self):
        service = (ROOT / "deploy" / "n0jcg-webmail.service").read_text(encoding="utf-8")
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ReadWritePaths=/etc/n0jcg-winlink", service)
        self.assertIn("ReadWritePaths=/var/lib/n0jcg-winlink-webmail/mailbox", service)
        self.assertIn("ReadWritePaths=/home/@APP_USER@/.config/pat", service)
        self.assertIn("ReadWritePaths=/home/@APP_USER@/.local/share/pat", service)

    def test_radio_profile_returns_actionable_apply_errors(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('"source": "radio_profile"', source)
        self.assertIn('HTTPStatus.SERVICE_UNAVAILABLE', source)
        self.assertIn("The Pi system filesystem is read-only", source)
        script = (ROOT / "deploy" / "apply_radio_profile.sh").read_text(encoding="utf-8")
        self.assertIn("remount,rw /", script)
        self.assertIn('mkdir -p "$CONFIG_DIR"', script)
        self.assertIn('for attempt in 1 2 3', script)
        self.assertIn('systemctl enable --now n0jcg-direwolf.service', script)

    def test_packet_profile_enables_direwolf_persistently(self):
        script = (ROOT / "deploy" / "configure_packet_radio.sh").read_text(encoding="utf-8")
        self.assertIn("systemctl enable --now n0jcg-direwolf.service", script)

    def test_common_pat_failures_have_actionable_explanations(self):
        self.assertIn("Verify the RMS target, frequency, 1200-AFSK mode", MODULE.meaningful_pat_error("Unable to establish connection to remote: port closed"))
        self.assertIn("secure login stage completed", MODULE.meaningful_pat_error("Exchange failed: connection lost", "mailbox_index"))
        self.assertIn("Winlink rejected", MODULE.meaningful_pat_error("Login failed - invalid password"))
        self.assertIn("protocol response", MODULE.meaningful_pat_error("Got unexpected protocol line: 'CMS via N0JCG >'"))

    def test_deployment_uses_official_pat_with_fbb_turnover_fix(self):
        source = (ROOT / "deploy" / "install_static_ui.sh").read_text(encoding="utf-8")
        self.assertIn('PAT_VERSION="${N0JCG_PAT_VERSION:-0.17.0}"', source)
        self.assertIn("bundled client-side build", source)
        self.assertIn('PAT_CLIENT_BINARY="$REPO_ROOT/tools/pat-winlink-client-rms"', source)

    def test_new_login_restarts_a_stale_pat_session(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn("def start_pat_sync(callsign, password, restart=False):", source)
        self.assertIn("start_pat_sync(callsign, password, restart=True)", source)
        self.assertIn("new login must never inherit a Pat process", source)
        self.assertIn("PAT_RF_COOLDOWN_SECONDS", source)
        self.assertIn("def stop_all_pat_sessions(message, cooldown=True):", source)
        self.assertIn("time.sleep(PAT_RF_COOLDOWN_SECONDS)", source)

    def test_new_login_is_appliance_wide_single_user(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn("def reset_single_user_appliance(message):", source)
        self.assertIn("SESSIONS.clear()", source)
        self.assertIn("reset_single_user_appliance(\"A new Winlink user signed in", source)

    def test_webmail_clears_authentication_on_protected_api_unauthorized(self):
        script = (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8")
        self.assertIn("async function requireLogin", script)
        self.assertIn("nativeFetch('/api/v1/auth/logout'", script)
        self.assertIn("response.status === 401 && protectedWebmailRequest", script)
        self.assertIn("document.getElementById('login-form').reset()", script)

    def test_post_auth_sync_failure_does_not_redirect_to_login(self):
        script = (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8")
        self.assertIn("syncFailedAfterLogin", script)
        self.assertIn("syncStatus.hidden = !(active || syncFailedAfterLogin)", script)

    def test_refresh_remains_available_during_mailbox_transfer(self):
        script = (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8")
        self.assertIn("function setRefreshAvailability(transferActive)", script)
        self.assertIn("button.disabled = false", script)
        self.assertIn("setRefreshAvailability(active)", script)
        self.assertIn("body: JSON.stringify({ restart: true })", script)

    def test_normalize_account_returns_callsign(self):
        email, callsign = MODULE.normalize_account("n0jcg@winlink.org")
        self.assertEqual(email, "N0JCG@WINLINK.ORG")
        self.assertEqual(callsign, "N0JCG")
        email, callsign = MODULE.normalize_account("n0jcg")
        self.assertEqual(email, "N0JCG@WINLINK.ORG")
        self.assertEqual(callsign, "N0JCG")

    def test_normalize_account_rejects_non_winlink_address(self):
        with self.assertRaises(ValueError):
            MODULE.normalize_account("operator@example.net")

    def test_attachment_round_trip(self):
        content = b"hello attachment"
        name, content_type, decoded = MODULE.parse_attachment({
            "attachment": {
                "name": "note.txt",
                "type": "text/plain",
                "data": base64.b64encode(content).decode("ascii"),
            }
        })
        self.assertEqual((name, content_type, decoded), ("note.txt", "text/plain", content))

    def test_attachment_limit_is_enforced(self):
        oversized = base64.b64encode(b"x" * (100 * 1024 + 1)).decode("ascii")
        with self.assertRaisesRegex(ValueError, "100 KB"):
            MODULE.parse_attachment({"attachment": {"data": oversized}})

    def test_empty_attachment_is_allowed(self):
        self.assertEqual(MODULE.parse_attachment({}), ("", "", b""))

    def test_folder_name_validation(self):
        self.assertEqual(MODULE.folder_name(" Field Notes "), "Field Notes")
        with self.assertRaises(ValueError):
            MODULE.folder_name("Inbox")
        with self.assertRaises(ValueError):
            MODULE.folder_name("bad/name")

    def test_standard_form_descriptor_renders_plain_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "standard" / "1.0.0"
            library.mkdir(parents=True)
            (root / "standard" / "current.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
            (library / "Test.txt").write_text("Form: Test.html\nTo: <var recipient>\nSubject: Test <var subject>\n\nMsg:\nHello <var name>\nFrom <var MsgSender>", encoding="utf-8")
            catalog = TEMPLATE_MODULE.catalog(root)
            self.assertEqual(catalog["templates"][0]["fields"], ["subject", "recipient", "name"])
            rendered = TEMPLATE_MODULE.render("Test", {"recipient": "N0JCG@winlink.org", "subject": "Status", "name": "Operator"}, "N0JCG", root)
            self.assertEqual(rendered["recipient"], "N0JCG@winlink.org")
            self.assertIn("Hello Operator", rendered["body"])
            self.assertIn("From N0JCG", rendered["body"])

    def test_rms_gateway_records_are_sorted_by_distance(self):
        payload = {"Gateways": [{"Callsign": "NEAR-10", "Latitude": 39.75, "Longitude": -105.0, "Channels": [{"Frequency": 145070000, "Mode": "Packet", "Baud": 1200}]}, {"Callsign": "FAR-10", "Latitude": 40.75, "Longitude": -105.0, "Channels": [{"Frequency": 145090000, "Mode": "Packet", "Baud": 1200}]}]}
        records = RMS_MODULE.normalize_gateways(payload)
        nearest = RMS_MODULE.enrich_nearest(records, 39.74, -105.0)
        self.assertEqual(nearest[0]["callsign"], "NEAR-10")
        self.assertEqual(nearest[0]["frequency_mhz"], 145.07)

    def test_rms_gateway_frequency_normalizes_hz_and_mhz(self):
        payload = {"gateways": [{"callsign": "TEST-10", "lat": 1, "lon": 2, "channels": [{"frequency": 145.07, "mode": "1200-AFSK"}]}]}
        record = RMS_MODULE.normalize_gateways(payload)[0]
        self.assertEqual(record["frequency_mhz"], 145.07)


if __name__ == "__main__":
    unittest.main()
