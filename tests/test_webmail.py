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

    def test_session_expiry_is_disabled_by_default(self):
        self.assertEqual(MODULE.SESSION_IDLE, 0)

    def test_successful_login_path_clears_rate_limit_counter(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        success_path = source.index("authenticated, evidence = wait_for_pat_auth")
        clear_path = source.index("clear_login_failures(client_id)", success_path)
        token_path = source.index("token = create_session", success_path)
        self.assertLess(clear_path, token_path)

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

    def test_authentication_progress_has_safe_granular_stages(self):
        source = (ROOT / "api" / "n0jcg_webmail.py").read_text(encoding="utf-8")
        self.assertIn('"stage_label": "Contacting RMS"', source)
        self.assertIn("Mailbox proposal received", source)
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
        self.assertIn("Mailbox index requested; waiting for RMS message records", source)
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
