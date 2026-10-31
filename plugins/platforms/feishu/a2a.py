"""Feishu bot-to-bot collaboration helpers.

The registry contains only public bot identities and enabled group IDs. Missing
or malformed configuration fails closed for inbound bot traffic.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class A2ABot:
    key: str
    name: str
    open_id: str
    aliases: tuple[str, ...] = ()
    role: str = ""
    app_id: str = ""
    identity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class A2ARegistry:
    enabled_chat_ids: frozenset[str]
    bots: tuple[A2ABot, ...]
    max_peer_turns: int = 6
    turn_window_seconds: int = 300


_EMPTY_REGISTRY = A2ARegistry(frozenset(), ())


def load_registry(path: Optional[str] = None) -> A2ARegistry:
    registry_path = path or os.getenv("FEISHU_A2A_REGISTRY", "").strip() or "/srv/feishu-a2a/registry.json"
    try:
        raw = json.loads(Path(registry_path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return _EMPTY_REGISTRY
        if not isinstance(raw.get("enabled_chat_ids"), list) or not isinstance(raw.get("bots"), list):
            return _EMPTY_REGISTRY
        chats = frozenset(value for value in raw.get("enabled_chat_ids", ()) if isinstance(value, str) and value)
        bots = []
        for value in raw.get("bots", ()):
            if not isinstance(value, dict):
                continue
            key = str(value.get("key", "")).strip()
            name = str(value.get("name", "")).strip()
            open_id = str(value.get("open_id", "")).strip()
            if not key or not name or not open_id:
                continue
            aliases = tuple(str(alias).strip() for alias in value.get("aliases", ()) if str(alias).strip())
            bots.append(A2ABot(
                key=key,
                name=name,
                open_id=open_id,
                aliases=aliases,
                role=str(value.get("role", "")).strip(),
                app_id=str(value.get("app_id", "")).strip(),
                identity_ids=tuple(
                    str(identity).strip()
                    for identity in value.get("identity_ids", ())
                    if str(identity).strip()
                ),
            ))
        def bounded_positive(value, default, upper):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                return default
            return min(value, upper)
        return A2ARegistry(
            chats, tuple(bots),
            bounded_positive(raw.get("max_peer_turns"), 6, 20),
            bounded_positive(raw.get("turn_window_seconds"), 300, 1800),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _EMPTY_REGISTRY


def find_bot_by_key(key: str) -> Optional[A2ABot]:
    return next((bot for bot in load_registry().bots if bot.key == key), None)


def mention_targets_bot(
    observed_ids: Iterable[str],
    mention_name: str,
    self_key: str = "codex",
) -> bool:
    """Match mention IDs across Feishu's app-relative identity variants."""
    bot = find_bot_by_key(self_key)
    if bot is None:
        return False
    observed = frozenset(value for value in observed_ids if value)
    accepted = frozenset((bot.open_id, bot.app_id, *bot.identity_ids)) - {""}
    if observed:
        return bool(observed & accepted)
    return mention_name.strip() in frozenset((bot.name, *bot.aliases))


def find_peer_by_sender_ids(sender_ids: Iterable[str], self_key: str = "codex") -> Optional[A2ABot]:
    identities = frozenset(value for value in sender_ids if value)
    return next(
        (
            bot
            for bot in load_registry().bots
            if bot.key != self_key and bool(
                identities & frozenset((bot.open_id, bot.app_id, *bot.identity_ids))
            )
        ),
        None,
    )


def is_enabled_chat(chat_id: str) -> bool:
    return chat_id in load_registry().enabled_chat_ids


def allows_peer_message(chat_id: str, sender_ids: Iterable[str], self_key: str = "codex") -> bool:
    registry = load_registry()
    if chat_id not in registry.enabled_chat_ids:
        return False
    identities = frozenset(value for value in sender_ids if value)
    return any(
        bot.key != self_key and bool(
            identities & frozenset((bot.open_id, bot.app_id, *bot.identity_ids))
        )
        for bot in registry.bots
    )


def _native_mention(bot: A2ABot) -> str:
    return f'<at user_id="{bot.open_id}">{bot.name}</at>'


def _replace_alias(text: str, bot: A2ABot) -> tuple[str, bool]:
    rendered = text
    matched = f'<at user_id="{bot.open_id}">' in rendered
    aliases = sorted((bot.name, *bot.aliases), key=len, reverse=True)
    for alias in aliases:
        pattern = re.compile(
            rf"(^|[\s(（\[【])@{re.escape(alias)}(?=$|[\s,，。:：;；!?！？)）\]】])",
            re.MULTILINE,
        )

        def replace(match: re.Match[str]) -> str:
            nonlocal matched
            matched = True
            return f"{match.group(1)}{_native_mention(bot)}"

        rendered = pattern.sub(replace, rendered)
    return rendered, matched


def format_outbound(
    text: str,
    *,
    self_key: str = "codex",
    result_peer_open_id: Optional[str] = None,
) -> tuple[str, tuple[A2ABot, ...], Optional[str]]:
    peers = tuple(bot for bot in load_registry().bots if bot.key != self_key)
    rendered = (text or "").strip()
    targets = []
    for peer in peers:
        rendered, matched = _replace_alias(rendered, peer)
        if matched:
            targets.append(peer)

    # Compatibility argument only: a reply is not consent to notify the peer.
    # Only an explicit @ in model output becomes a native mention. Legacy
    # markers remain ordinary text; the transport neither adds nor interprets them.
    return rendered, tuple(targets), None


def build_channel_prompt(self_key: str = "codex") -> str:
    registry = load_registry()
    self_bot = next((bot for bot in registry.bots if bot.key == self_key), None)
    peers = tuple(bot for bot in registry.bots if bot.key != self_key)
    if not self_bot or not peers:
        return ""
    peer_lines = "\n".join(
        f"- {bot.name}（写作 @{bot.name}；职责：{bot.role or '协作任务'}）"
        for bot in peers
    )
    return "\n".join((
        "飞书群内 Agent 协作规则：",
        f"你是 {self_bot.name}。可协作的机器人：",
        peer_lines,
        "机器人通过普通原生 @ 协作，不要求正文协议标记，也不强制请求/结果交替。",
        "先核对用户任务、引用消息、已有结论和待决项，不得换题；用户纠偏优先。",
        "默认 OpenClaw 主答汇总、Hermes 技术评审；各改各的实现，不交叉覆盖或同时重启。",
        "只有提出新增意见且需要对方回应时才 @；同意、感谢、收尾和最终汇总不 @。",
        "用户授权的讨论可追问，建议最多两轮往返；无新增信息就结束，不自动再委派。",
        f"运行时每侧每个讨论最多接收 {registry.max_peer_turns} 条，期限 {registry.turn_window_seconds} 秒；到限后等待用户新指令。",
        "用户说停止讨论或 /stop 时不再继续机器人讨论；不得自行重启已停止或到限的讨论。",
        "保留普通引用关系，不将 root_id 当 thread_id。",
        "请求发送、接收、执行、完成和验证是不同状态；只能按实际证据汇报。",
    ))
