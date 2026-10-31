"""Persistent, cross-process budgets for native Feishu peer mentions.

This controls admission, not model instructions. Only an admitted human turn can
start a fresh discussion budget. Expiration never reopens a bot-only loop.
"""
from __future__ import annotations

from contextlib import contextmanager
try:
    import fcntl
except ImportError:  # Windows: fail peer admission closed, preserve human chats.
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


def plain_content(content: str) -> str:
    try:
        value = json.loads(content)
        if isinstance(value, dict):
            content = str(value.get("text", content))
    except (ValueError, TypeError):
        pass
    content = re.sub(r"<at\b[^>]*>.*?</at>|@_user_\d+", "", content)
    return " ".join(content.split()).strip()


class PeerDiscussionGuard:
    def __init__(self, path: Path, *, max_turns: int = 6, window_seconds: int = 300):
        self.path = Path(path)
        self.max_turns = max_turns
        self.window_seconds = window_seconds

    @contextmanager
    def _state(self):
        if fcntl is None:
            raise OSError("persistent peer locking is unavailable on this platform")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if self.path.exists():
                    if self.path.stat().st_size > 2_000_000:
                        raise ValueError("peer state exceeds limit")
                    state = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(state, dict) or state.get("version") != 1:
                        raise ValueError("invalid peer state")
                    if not isinstance(state.get("chats"), dict):
                        raise ValueError("invalid peer chats")
                else:
                    state = {"version": 1, "chats": {}}
                yield state
                staged = self.path.with_name(self.path.name + "." + uuid.uuid4().hex)
                try:
                    with staged.open("x", encoding="utf-8") as output:
                        json.dump(state, output, ensure_ascii=False)
                        output.flush()
                        os.fsync(output.fileno())
                    staged.replace(self.path)
                finally:
                    staged.unlink(missing_ok=True)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def human_turn(self, chat_id: str, message_id: str, content: str) -> None:
        text = plain_content(content)
        stop = text in {"/stop", "停止讨论", "停止协作", "结束讨论", "停止机器人讨论"}
        with self._state() as state:
            chats = state["chats"]
            old = chats.get(chat_id, {})
            if old.get("human_message_id") == message_id:
                return
            if len(chats) >= 256 and chat_id not in chats:
                raise ValueError("peer chat state full")
            chats[chat_id] = {
                "human_message_id": message_id,
                "stopped": stop,
                "peers": {},
            }

    def admit(self, chat_id: str, peer_key: str, message_id: str, content: str, *, now: float | None = None) -> str | None:
        if not message_id:
            return "peer_missing_message_id"
        now = time.time() if now is None else now
        digest = hashlib.sha256(plain_content(content).encode()).hexdigest()
        try:
            with self._state() as state:
                chats = state["chats"]
                if chat_id not in chats and len(chats) >= 256:
                    return "peer_state_full"
                chat = chats.setdefault(chat_id, {"stopped": False, "peers": {}})
                if chat.get("stopped"):
                    return "peer_discussion_stopped"
                peers = chat["peers"]
                if peer_key not in peers and len(peers) >= 16:
                    return "peer_state_full"
                entry = peers.setdefault(peer_key, {
                    "started_at": now, "messages": [], "digests": [],
                })
                if now < entry["started_at"] or now - entry["started_at"] >= self.window_seconds:
                    return "peer_discussion_expired"
                if message_id in entry["messages"] or digest in entry["digests"]:
                    return "peer_discussion_duplicate"
                if len(entry["messages"]) >= self.max_turns:
                    return "peer_turn_limit"
                entry["messages"].append(message_id)
                entry["digests"].append(digest)
                return None
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return "peer_state_unavailable"
