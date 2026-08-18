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
    def test_webmail_session_cookie_is_not_persistent(self):
        cookie = MODULE.session_cookie("test-token")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Max-Age", cookie)

    def test_session_expiry_is_disabled_by_default(self):
        self.assertEqual(MODULE.SESSION_IDLE, 0)

    def test_webmail_clears_authentication_on_protected_api_unauthorized(self):
        script = (ROOT / "ui" / "webmail" / "webmail.js").read_text(encoding="utf-8")
        self.assertIn("async function requireLogin", script)
        self.assertIn("nativeFetch('/api/v1/auth/logout'", script)
        self.assertIn("response.status === 401 && protectedWebmailRequest", script)
        self.assertIn("document.getElementById('login-form').reset()", script)

    def test_normalize_account_returns_callsign(self):
        email, callsign = MODULE.normalize_account("n0jcg@winlink.org")
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
