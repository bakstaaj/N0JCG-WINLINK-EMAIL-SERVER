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


if __name__ == "__main__":
    unittest.main()
