import base64
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("n0jcg_webmail", ROOT / "api" / "n0jcg_webmail.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WebmailHelpersTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
