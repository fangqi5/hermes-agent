"""Integration coverage with the actual Feishu adapter and registry loader."""
import json
from types import SimpleNamespace as NS

from gateway.config import PlatformConfig
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from plugins.platforms.feishu.adapter import FeishuAdapter


def test_actual_adapter_native_peer_admission_and_persistent_stop(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "version": 1, "enabled_chat_ids": ["oc_test"], "max_peer_turns": 2,
        "bots": [
            {"key": "codex", "name": "Hermes", "open_id": "ou_self"},
            {"key": "pi", "name": "OpenClaw", "open_id": "ou_peer"},
        ],
    }))
    monkeypatch.setenv("FEISHU_A2A_REGISTRY", str(registry))
    token = set_hermes_home_override(tmp_path / "home")
    try:
        adapter = FeishuAdapter(PlatformConfig())
        adapter._bot_open_id = "ou_self"
        adapter._bot_user_id = "self"
        adapter._allow_bots = "all"
        adapter._require_mention_for = lambda _: False
        adapter._allow_group_message = lambda *a, **kw: True
        peer = NS(sender_type="app", sender_id=NS(open_id="ou_peer"))
        human = NS(sender_type="user", sender_id=NS(open_id="ou_human"))
        def message(mid, text, mentioned=True):
            mentions = [NS(id=NS(open_id="ou_self"), name="Hermes")] if mentioned else []
            return NS(chat_type="group", chat_id="oc_test", message_id=mid,
                      content=json.dumps({"text": text}), mentions=mentions)
        assert adapter._admit(peer, message("m1", "普通讨论")) is None
        assert adapter._admit(peer, message("m2", "新意见")) is None
        assert adapter._admit(peer, message("m3", "超预算")) == "peer_turn_limit"
        assert adapter._admit(human, message("h1", "停止讨论")) is None
        assert adapter._admit(peer, message("m4", "继续")) == "peer_discussion_stopped"
        assert adapter._admit(human, message("h2", "重新讨论")) is None
        assert adapter._admit(peer, message("m5", "新一轮意见")) is None
        assert adapter._admit(peer, message("m6", "没有原生艾特", False)) == "bot_not_mentioned"
    finally:
        reset_hermes_home_override(token)
