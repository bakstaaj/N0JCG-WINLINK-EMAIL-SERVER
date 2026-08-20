import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agwpe_identity_bridge", ROOT / "tools" / "agwpe_identity_bridge.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AGWPEIdentityBridgeTests(unittest.TestCase):
    def frame(self, kind, source, destination, data=b""):
        return MODULE.Frame([
            0,
            b"\0\0\0",
            kind,
            0,
            b"\0",
            0,
            MODULE.call_bytes(source),
            MODULE.call_bytes(destination),
            len(data),
            0,
        ], data)

    def test_inbound_rms_data_is_retargeted_to_mailbox_identity(self):
        frame = self.frame(b"D", "N0JCG-10", "N0JCG-3", b"[WL2K-5.0-B2FWIHJM$]")
        rewritten = MODULE.rewrite_inbound(frame, MODULE.call_bytes("N0JCG"), MODULE.call_bytes("N0JCG-3"))
        self.assertEqual(MODULE.clean_call(rewritten.destination), "N0JCG")
        self.assertEqual(rewritten.data, b"[WL2K-5.0-B2FWIHJM$]")

    def test_inbound_matching_ignores_agw_padding_variation(self):
        frame = self.frame(b"D", "N0JCG-10", "N0JCG-3", b";PQ: 1234")
        frame.raw_header[7] = b"N0JCG-3   "
        rewritten = MODULE.rewrite_inbound(frame, MODULE.call_bytes("N0JCG"), MODULE.call_bytes("N0JCG-3"))
        self.assertEqual(MODULE.clean_call(rewritten.destination), "N0JCG")

    def test_outbound_registration_uses_packet_identity(self):
        frame = self.frame(b"X", "N0JCG", "N0JCG-10", b"")
        rewritten = MODULE.rewrite_outbound(frame, MODULE.call_bytes("N0JCG"), MODULE.call_bytes("N0JCG-3"))
        self.assertEqual(MODULE.clean_call(rewritten.source), "N0JCG-3")

    def test_second_winlink_user_is_not_mapped_to_n0jcg_mailbox(self):
        frame = self.frame(b"D", "N0JCG-10", "K1ABC-3", b"CMS via K1ABC")
        rewritten = MODULE.rewrite_inbound(frame, MODULE.call_bytes("K1ABC"), MODULE.call_bytes("K1ABC-3"))
        self.assertEqual(MODULE.clean_call(rewritten.destination), "K1ABC")
        self.assertEqual(rewritten.data, b"CMS via K1ABC")

    def test_second_user_outbound_identity_stays_per_login(self):
        frame = self.frame(b"C", "K1ABC", "N0JCG-10", b"")
        rewritten = MODULE.rewrite_outbound(frame, MODULE.call_bytes("K1ABC"), MODULE.call_bytes("N0JCG-3"))
        self.assertEqual(MODULE.clean_call(rewritten.source), "N0JCG-3")


if __name__ == "__main__":
    unittest.main()
