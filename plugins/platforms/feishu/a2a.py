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


A2A_REQUEST_MARKER = "[A2A_REQUEST]"
A2A_RESULT_MARKER = "[A2A_RESULT]"


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


_EMPTY_REGISTRY = A2ARegistry(frozenset(), ())


def load_registry(path: Optional[str] = None) -> A2ARegistry:
    registry_path = path or os.getenv("FEISHU_A2A_REGISTRY", "").strip() or "/srv/feishu-a2a/registry.json"
    try:
        raw = json.loads(Path(registry_path).read_text(encoding="utf-8"))
        if raw.get("version") != 1:
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
        return A2ARegistry(chats, tuple(bots))
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


def is_a2a_result_content(content: str) -> bool:
    return A2A_RESULT_MARKER in (content or "")


def is_a2a_request_content(content: str) -> bool:
    """Only an explicit request marker may start an A2A turn."""
    value = content or ""
    return A2A_REQUEST_MARKER in value and A2A_RESULT_MARKER not in value


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

    if result_peer_open_id:
        peer = next((bot for bot in peers if bot.open_id == result_peer_open_id), None)
        if peer and all(bot.open_id != peer.open_id for bot in targets):
            targets.append(peer)
            rendered = f"{_native_mention(peer)} {rendered}".strip()
        rendered = rendered.replace(A2A_REQUEST_MARKER, A2A_RESULT_MARKER)
        if A2A_RESULT_MARKER not in rendered:
            rendered = f"{A2A_RESULT_MARKER} {rendered}".strip()
        return rendered, tuple(targets), "result"

    if not targets:
        return rendered, (), None
    kind = (
        "result" if A2A_RESULT_MARKER in rendered
        else "request" if A2A_REQUEST_MARKER in rendered
        else None
    )
    return rendered, tuple(targets), kind


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
        f"需要委派时，最终回复必须写成：@机器人名 {A2A_REQUEST_MARKER} 任务描述。",
        "另一机器人普通 @你 时可以正常回答，但回复不得 @任何机器人，也不得添加 A2A 标记。",
        f"只有明确 @你 且带 {A2A_REQUEST_MARKER} 才按协作任务执行，并回复：@机器人名 {A2A_RESULT_MARKER} 结果。",
        f"{A2A_RESULT_MARKER} 只是结果通知，禁止再次委派或回复，协作只允许一跳。",
        "不要伪造其他机器人已经执行完成；只有看到它的结果消息才能确认完成。",
    ))
