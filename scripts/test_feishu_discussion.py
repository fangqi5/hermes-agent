"""Dependency-free collaboration contracts; executed only in GitHub Actions."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
from typing import Any, Optional
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "plugins/platforms/feishu" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


a2a = load("feishu_discussion_a2a", "a2a.py")
discussion = load("feishu_discussion_guard", "discussion.py")
PEER = a2a.A2ABot("pi", "OpenClaw", "ou_peer", ("Pi",), app_id="cli_peer")
SELF = a2a.A2ABot("codex", "Hermes", "ou_self")
REGISTRY = a2a.A2ARegistry(frozenset({"oc_team"}), (SELF, PEER))


class NativeDiscussionContract(unittest.TestCase):
    def test_plain_answer_never_invents_return_mention_or_protocol(self):
        with patch.object(a2a, "load_registry", return_value=REGISTRY):
            text, targets, kind = a2a.format_outbound("同意，讨论结束。", result_peer_open_id="ou_peer")
        self.assertEqual(text, "同意，讨论结束。")
        self.assertEqual(targets, ())
        self.assertIsNone(kind)

    def test_explicit_alias_becomes_native_mention_without_marker(self):
        with patch.object(a2a, "load_registry", return_value=REGISTRY):
            text, targets, kind = a2a.format_outbound("@Pi 请核对边界。")
        self.assertEqual(text, '<at user_id="ou_peer">OpenClaw</at> 请核对边界。')
        self.assertEqual(targets, (PEER,))
        self.assertIsNone(kind)

    def test_registry_missing_or_invalid_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            self.assertEqual(a2a.load_registry(str(path)).bots, ())
            for value in [None, [], {"version": 1, "bots": "bad"}, {"version": 1, "enabled_chat_ids": None}]:
                path.write_text(json.dumps(value))
                self.assertEqual(a2a.load_registry(str(path)).bots, ())

    def test_identity_not_display_name_is_authoritative(self):
        with patch.object(a2a, "load_registry", return_value=REGISTRY):
            self.assertIs(a2a.find_peer_by_sender_ids(["cli_peer"]), PEER)
            self.assertIsNone(a2a.find_peer_by_sender_ids(["ou_self"]))
            self.assertFalse(a2a.mention_targets_bot(["ou_unknown"], "Hermes"))
            self.assertFalse(a2a.allows_peer_message("oc_other", ["ou_peer"]))

    def test_new_prompt_supports_bounded_native_discussion(self):
        with patch.object(a2a, "load_registry", return_value=REGISTRY):
            text = a2a.build_channel_prompt()
        self.assertIn("普通原生 @", text)
        self.assertIn("两轮", text)
        self.assertNotIn("[A2A_REQUEST]", text)
        self.assertNotIn("[A2A_RESULT]", text)


class PersistentGuardContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.guard = discussion.PeerDiscussionGuard(self.path, max_turns=2, window_seconds=60)

    def test_budget_survives_new_instance_and_content_dedup(self):
        self.assertIsNone(self.guard.admit("c", "p", "m1", "意见一", now=100))
        other = discussion.PeerDiscussionGuard(self.path, max_turns=2, window_seconds=60)
        self.assertEqual(other.admit("c", "p", "m1", "不同文本", now=101), "peer_discussion_duplicate")
        self.assertEqual(other.admit("c", "p", "m2", "意见一", now=101), "peer_discussion_duplicate")
        self.assertIsNone(other.admit("c", "p", "m3", "意见二", now=102))
        self.assertEqual(other.admit("c", "p", "m4", "意见三", now=103), "peer_turn_limit")

    def test_expiration_cannot_restart_bot_only_loop(self):
        self.assertIsNone(self.guard.admit("c", "p", "m1", "开始", now=100))
        self.assertEqual(self.guard.admit("c", "p", "m2", "超时继续", now=160), "peer_discussion_expired")
        self.assertEqual(self.guard.admit("c", "p", "m3", "仍继续", now=1000), "peer_discussion_expired")
        self.guard.human_turn("c", "human1", "继续当前讨论")
        self.assertIsNone(self.guard.admit("c", "p", "m4", "新意见", now=1001))

    def test_stop_persists_and_new_human_turn_resumes(self):
        self.guard.human_turn("c", "human1", json.dumps({"text": "停止讨论"}))
        self.assertEqual(self.guard.admit("c", "p", "m1", "继续", now=100), "peer_discussion_stopped")
        other = discussion.PeerDiscussionGuard(self.path)
        self.assertEqual(other.admit("c", "p", "m2", "继续", now=100), "peer_discussion_stopped")
        other.human_turn("c", "human2", "重新讨论")
        self.assertIsNone(other.admit("c", "p", "m3", "新任务", now=100))

    def test_duplicate_human_event_does_not_reset_budget(self):
        self.guard.human_turn("c", "h", "讨论")
        self.assertIsNone(self.guard.admit("c", "p", "m1", "意见一", now=100))
        self.guard.human_turn("c", "h", "讨论")
        self.assertEqual(self.guard.admit("c", "p", "m1", "意见一", now=101), "peer_discussion_duplicate")

    def test_corrupt_state_fails_closed(self):
        for content in ['not json', '{"version":1,"chats":[]}', '{"version":1,"chats":{"c":null}}']:
            self.path.write_text(content)
            self.assertEqual(self.guard.admit("c", "p", "m1", "意见", now=100), "peer_state_unavailable")

    def test_concurrent_instances_cannot_exceed_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        def admit(i):
            return discussion.PeerDiscussionGuard(self.path, max_turns=2).admit("c", "p", str(i), f"意见{i}", now=100)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(admit, range(12)))
        self.assertEqual(results.count(None), 2)
        self.assertEqual(results.count("peer_turn_limit"), 10)


class AdapterAdmissionContract(unittest.TestCase):
    """Execute actual _admit source with only platform I/O collaborators replaced."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tree = ast.parse((ROOT / "plugins/platforms/feishu/adapter.py").read_text())
        adapter = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FeishuAdapter")
        admit = next(n for n in adapter.body if isinstance(n, ast.FunctionDef) and n.name == "_admit")
        namespace = {
            "Any": Any, "Optional": Optional, "RejectReason": str,
            "_sender_identity": lambda s: frozenset({s.sender_id.open_id}),
            "_is_bot_sender": lambda s: s.sender_type == "app",
            "_allows_a2a_peer_message": lambda c, ids, **kw: c == "oc_team" and "ou_peer" in ids,
            "_find_a2a_peer_by_sender_ids": lambda ids, **kw: PEER if "ou_peer" in ids else None,
            "_is_a2a_enabled_chat": lambda c: c == "oc_team",
        }
        exec(compile(ast.Module(body=[admit], type_ignores=[]), "actual_adapter_admit", "exec"), namespace)
        self.admit = namespace["_admit"]
        self.adapter = NS(
            _bot_open_id="ou_self", _bot_user_id="self", _allow_bots="all",
            _require_mention_for=lambda c: False, _mentions_self=lambda m: m.mentioned,
            _allow_group_message=lambda *a, **k: True,
            _peer_discussion_guard=discussion.PeerDiscussionGuard(Path(self.tmp.name) / "state.json"),
        )

    def message(self, content="普通讨论", mentioned=True):
        return NS(chat_type="group", chat_id="oc_team", message_id="m1", content=content, mentioned=mentioned)

    def test_native_mention_without_marker_is_admitted(self):
        sender = NS(sender_type="app", sender_id=NS(open_id="ou_peer"))
        self.assertIsNone(self.admit(self.adapter, sender, self.message()))

    def test_legacy_result_marker_is_not_a_transport_gate(self):
        sender = NS(sender_type="app", sender_id=NS(open_id="ou_peer"))
        self.assertIsNone(self.admit(self.adapter, sender, self.message("[A2A_RESULT] 需要核对")))

    def test_unmentioned_or_untrusted_or_self_bot_is_rejected(self):
        sender = NS(sender_type="app", sender_id=NS(open_id="ou_peer"))
        self.assertEqual(self.admit(self.adapter, sender, self.message(mentioned=False)), "bot_not_mentioned")
        sender.sender_id.open_id = "ou_unknown"
        self.assertEqual(self.admit(self.adapter, sender, self.message()), "bot_not_allowed")
        sender.sender_id.open_id = "ou_self"
        self.assertEqual(self.admit(self.adapter, sender, self.message()), "self_echo")


if __name__ == "__main__":
    unittest.main()
