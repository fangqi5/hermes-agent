"""Standalone contract tests; run in GitHub Actions, not on the host."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("feishu_discussion_under_test", ROOT / "plugins/platforms/feishu/a2a.py")
a2a = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = a2a
spec.loader.exec_module(a2a)


class NativeDiscussionContract(unittest.TestCase):
    def test_plain_answer_never_invents_return_mention_or_protocol(self):
        from unittest.mock import patch
        peer = a2a.A2ABot("pi", "OpenClaw", "ou_peer")
        registry = a2a.A2ARegistry(frozenset({"oc_team"}), (peer,))
        with patch.object(a2a, "load_registry", return_value=registry):
            text, targets, kind = a2a.format_outbound("同意，讨论结束。", result_peer_open_id="ou_peer")
        self.assertEqual(text, "同意，讨论结束。")
        self.assertEqual(targets, ())
        self.assertIsNone(kind)


if __name__ == "__main__":
    unittest.main()
