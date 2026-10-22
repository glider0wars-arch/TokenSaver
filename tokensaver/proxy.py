"""Local API gateway with conservative, bucketed context compaction."""
from __future__ import annotations

import copy
import hmac
import http.client
import json
import math
import os
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .compressor import estimate_tokens

# Strong signals only. A slash or a generic modal verb is not enough to promote a line.
_IMPORTANT = re.compile(
    r"\b(?:error|exception|traceback|failed|failure|regression|decision|decisions|todo|fixme|"
    r"constraint|requirement|critical|blocker|rollback|security issue|current request|durable decisions|windows compatibility|python\s+3\.\d+)\b|"
    r"\b(?:do not|don't|never|must not|should not|cannot|can't|remember this)\b|"
    r"(?:^|[\s:،.!؟])(?:خطا|اشکال|تصمیم|محدودیت|الزام|مهم است|فراموش نکن|انجام نشود)(?=$|[\s:،.!؟])|"
    r"\b(?:tool_use|tool_result|function_call|function_call_output)\b|"
    r"\b(?:[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|json|yaml|yml|toml|md))\b|"
    r"\bline\s+\d+\b|\bstatus code\s+\d{3}\b",
    re.IGNORECASE,
)
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
_DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class GatewayConfig:
    upstream_base_url: str
    keep_recent_messages: int
    summary_max_chars: int
    min_old_chars: int
    min_total_chars: int
    cut_step: int
    max_body_bytes: int
    upstream_timeout: float
    enabled: bool
    host: str
    port: int
    allow_remote: bool
    gateway_token: str
    access_log: bool
    upstream_api_key: str


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer; received {raw!r}.") from exc
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{name} must be {bound}; received {value}.")
    return value


def load_config() -> GatewayConfig:
    """Validate every supported environment setting with actionable errors."""
    upstream = os.getenv("TOKENSAVER_UPSTREAM_BASE_URL", "https://api.openai.com").rstrip("/")
    try:
        parsed = urlsplit(upstream)
        hostname = parsed.hostname
        upstream_port = parsed.port
    except ValueError as exc:
        raise ValueError(f"TOKENSAVER_UPSTREAM_BASE_URL is not a valid URL or port: {exc}") from exc
    if upstream_port == 0:
        raise ValueError("TOKENSAVER_UPSTREAM_BASE_URL port must be between 1 and 65535.")
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        raise ValueError("TOKENSAVER_UPSTREAM_BASE_URL must be an http(s) origin without embedded credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("TOKENSAVER_UPSTREAM_BASE_URL must not include a query string or fragment.")
    enabled_raw = os.getenv("TOKENSAVER_ENABLED", "1")
    remote_raw = os.getenv("TOKENSAVER_ALLOW_REMOTE", "0")
    access_raw = os.getenv("TOKENSAVER_ACCESS_LOG", "0")
    for name, raw in (("TOKENSAVER_ENABLED", enabled_raw), ("TOKENSAVER_ALLOW_REMOTE", remote_raw),
                      ("TOKENSAVER_ACCESS_LOG", access_raw)):
        if raw not in {"0", "1"}:
            raise ValueError(f"{name} must be exactly '0' or '1'.")
    timeout_raw = os.getenv("TOKENSAVER_UPSTREAM_TIMEOUT", "120")
    try:
        timeout = float(timeout_raw)
    except ValueError as exc:
        raise ValueError(f"TOKENSAVER_UPSTREAM_TIMEOUT must be a positive number; received {timeout_raw!r}.") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("TOKENSAVER_UPSTREAM_TIMEOUT must be a finite number greater than zero.")
    host = os.getenv("TOKENSAVER_HOST", "127.0.0.1")
    port = _env_int("TOKENSAVER_PORT", 8765, minimum=1, maximum=65535)
    allow_remote = remote_raw == "1"
    token = os.getenv("TOKENSAVER_GATEWAY_TOKEN", "")
    upstream_key = os.getenv("TOKENSAVER_UPSTREAM_API_KEY", "")
    if any(ch in token for ch in "\r\n") or any(ch in upstream_key for ch in "\r\n"):
        raise ValueError("TOKENSAVER_GATEWAY_TOKEN and TOKENSAVER_UPSTREAM_API_KEY must not contain newlines.")
    if allow_remote and not token:
        raise ValueError("TOKENSAVER_GATEWAY_TOKEN is required when TOKENSAVER_ALLOW_REMOTE=1.")
    if host not in {"127.0.0.1", "localhost", "::1"} and not allow_remote:
        raise ValueError("Refusing non-local bind. Set TOKENSAVER_ALLOW_REMOTE=1 and TOKENSAVER_GATEWAY_TOKEN only behind authenticated TLS.")
    return GatewayConfig(
        upstream_base_url=upstream,
        keep_recent_messages=_env_int("TOKENSAVER_KEEP_RECENT_MESSAGES", 8, minimum=1),
        summary_max_chars=_env_int("TOKENSAVER_SUMMARY_MAX_CHARS", 3000, minimum=100),
        min_old_chars=_env_int("TOKENSAVER_MIN_OLD_CHARS", 1200, minimum=0),
        min_total_chars=_env_int("TOKENSAVER_MIN_TOTAL_CHARS", 5000, minimum=1),
        cut_step=_env_int("TOKENSAVER_CUT_STEP", 4, minimum=1),
        max_body_bytes=_env_int("TOKENSAVER_MAX_BODY_BYTES", _DEFAULT_MAX_BODY_BYTES, minimum=1),
        upstream_timeout=timeout,
        enabled=enabled_raw == "1",
        host=host,
        port=port,
        allow_remote=allow_remote,
        gateway_token=token,
        access_log=access_raw == "1",
        upstream_api_key=upstream_key,
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict):
                kind = str(part.get("type", "")).lower()
                source = part.get("source")
                if kind in {"image", "image_url", "input_image"} or "image_url" in part:
                    pieces.append("[image omitted]")
                    continue
                if isinstance(source, dict) and source.get("type") == "base64" and isinstance(source.get("data"), str):
                    pieces.append("[image omitted]")
                    continue
                if any(key in part for key in ("data_base64", "binary_data", "bytes")):
                    pieces.append("[binary omitted]")
                    continue
                value = part.get("text")
                if isinstance(value, str):
                    pieces.append(value)
                elif kind in {"tool_use", "tool_result", "function_call", "function_call_output"}:
                    pieces.append(json.dumps(part, ensure_ascii=False, separators=(",", ":")))
                elif isinstance(part.get("content"), (str, list, dict)):
                    pieces.append(_content_text(part["content"]))
                else:
                    pieces.append(json.dumps(part, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(pieces)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, separators=(",", ":"))


def _message_text(message: dict[str, Any]) -> str:
    role = str(message.get("role", "unknown"))
    body = _content_text(message.get("content", ""))
    extras: list[str] = []
    if message.get("name"):
        extras.append("name=" + str(message["name"]))
    if message.get("tool_calls"):
        extras.append("tool_calls=" + json.dumps(message["tool_calls"], ensure_ascii=False, separators=(",", ":")))
    if message.get("function_call"):
        extras.append("function_call=" + json.dumps(message["function_call"], ensure_ascii=False, separators=(",", ":")))
    if extras:
        body += ("\n" if body else "") + "\n".join(extras)
    return f"{role}: {body}".strip()



_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_DEFAULT_TOOL_OUTPUT_MAX_CHARS = 4000
_TOOL_OUTPUT_REFERENCE = "[identical tool output repeated later; see the later occurrence]"


def _compress_tool_output_text(text: str, max_chars: int = _DEFAULT_TOOL_OUTPUT_MAX_CHARS,
                               head_lines: int = 8, tail_lines: int = 8) -> str:
    """Shrink noisy output, retaining all important lines even if they exceed the cap."""
    if max_chars < 1:
        raise ValueError("max_chars must be a positive integer.")
    if head_lines < 0 or tail_lines < 0:
        raise ValueError("head_lines and tail_lines must be non-negative.")
    cleaned = _ANSI_ESCAPE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = cleaned.split("\n")
    collapsed: list[str] = []
    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        line = lines[i]
        if j - i > 1 and line.strip():
            line = f"{line} (repeated x{j - i})"
        collapsed.append(line)
        i = j
    if len("\n".join(collapsed)) <= max_chars:
        return "\n".join(collapsed)
    important = {i for i, line in enumerate(collapsed) if _IMPORTANT.search(line)}
    def render(keep: set[int]) -> str:
        output: list[str] = []
        omitted = 0
        in_omitted = False
        for index, line in enumerate(collapsed):
            if index in keep:
                if in_omitted:
                    output.append(f"[{omitted} lines omitted]")
                    omitted = 0
                    in_omitted = False
                output.append(line)
            else:
                omitted += 1
                in_omitted = True
        if in_omitted:
            output.append(f"[{omitted} lines omitted]")
        return "\n".join(output)
    result = render(important)
    if len(result) > max_chars:
        # Important facts outrank the size target; never silently discard them.
        return "\n".join(line for i, line in enumerate(collapsed) if i in important)
    priority = list(range(min(head_lines, len(collapsed))))
    priority.extend(i for i in range(max(0, len(collapsed) - tail_lines), len(collapsed)) if i not in priority)
    priority.extend(i for i in range(len(collapsed)) if i not in important and i not in priority)
    keep = set(important)
    for index in priority:
        if index in keep:
            continue
        candidate = set(keep)
        candidate.add(index)
        if len(render(candidate)) <= max_chars:
            keep = candidate
    return render(keep)


def _tool_output_slots(items: list[Any], kind: str) -> list[tuple[int, Any, str, str]]:
    """Return message index, mutable content owner, key, and tool-output text."""
    slots: list[tuple[int, Any, str, str]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if kind == "chat" and item.get("role") == "tool" and isinstance(item.get("content"), str):
            slots.append((i, item, "content", item["content"]))
        elif kind == "responses" and item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
            slots.append((i, item, "output", item["output"]))
        elif kind == "anthropic" and item.get("role") == "user" and isinstance(item.get("content"), list):
            for block in item["content"]:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                if isinstance(block.get("content"), str):
                    slots.append((i, block, "content", block["content"]))
                elif isinstance(block.get("content"), list):
                    for part in block["content"]:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            slots.append((i, part, "text", part["text"]))
    return slots


def _compress_payload_tool_outputs(items: list[Any] | str, kind: str | dict[str, Any],
                                 max_chars: int | None = None) -> int:
    """Compress tool outputs; accepts either (items, kind, max_chars) or (path, payload)."""
    if isinstance(items, str) and isinstance(kind, dict):
        path, payload = items, kind
        if path.endswith("/responses"):
            items, kind = payload.get("input", []), "responses"
        elif path.endswith("/messages"):
            items, kind = payload.get("messages", []), "anthropic"
        else:
            items, kind = payload.get("messages", []), "chat"
    if max_chars is None:
        max_chars = _env_int("TOKENSAVER_TOOL_OUTPUT_MAX_CHARS", 4000, minimum=1)
    if not isinstance(items, list) or not isinstance(kind, str):
        raise ValueError("Expected a list of request items and a supported tool-output kind.")
    if max_chars < 1:
        raise ValueError("TOKENSAVER_TOOL_OUTPUT_MAX_CHARS must be a positive integer.")
    protected_start = len(items)
    if kind == "chat":
        protected_start = next((i for i in range(len(items) - 1, -1, -1)
                                if isinstance(items[i], dict) and items[i].get("role") == "assistant"
                                and items[i].get("tool_calls")), len(items))
    elif kind == "responses":
        protected_start = next((i for i in range(len(items) - 1, -1, -1)
                                if isinstance(items[i], dict) and items[i].get("type") == "function_call"), len(items))
    elif kind == "anthropic":
        protected_start = next((i for i in range(len(items) - 1, -1, -1)
                                if isinstance(items[i], dict) and items[i].get("role") == "assistant"
                                and isinstance(items[i].get("content"), list)
                                and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in items[i]["content"])), len(items))
    slots = _tool_output_slots(items, kind)
    # Only reference output in the protected latest cycle, which survives compaction.
    later: set[str] = set()
    for index, _owner, _key, raw_text in slots:
        if index >= protected_start:
            later.add(_ANSI_ESCAPE.sub("", raw_text).replace("\r\n", "\n").replace("\r", "\n"))
    saved = 0
    for index, owner, key, raw_text in slots:
        cleaned = _ANSI_ESCAPE.sub("", raw_text).replace("\r\n", "\n").replace("\r", "\n")
        if index < protected_start:
            if cleaned in later:
                replacement = _TOOL_OUTPUT_REFERENCE
            elif len(cleaned) > max_chars:
                replacement = _compress_tool_output_text(cleaned, max_chars=max_chars)
            else:
                replacement = cleaned
            owner[key] = replacement
            saved += max(0, len(raw_text) - len(replacement))
    return saved


def _contains_cache_control(value: Any) -> bool:
    if isinstance(value, dict):
        return "cache_control" in value or any(_contains_cache_control(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_cache_control(v) for v in value)
    return False


def _select_summary_lines(text: str, budget: int) -> str:
    """Select high-signal lines by importance, not merely their position in history."""
    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if not line:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    if not lines:
        return ""

    def score(line: str) -> int:
        if re.search(r"\b(?:error|exception|traceback|failed|failure|status code|valueerror|typeerror)\b|\bline\s+\d+\b", line, re.I):
            return 100
        if re.search(r"\b(?:constraint|requirement|decision|decisions|current request|durable decisions|never|must not|do not|windows compatibility|python\s+3\.\d+)\b", line, re.I):
            return 90
        if re.search(r"(?:خطا|اشکال|تصمیم|محدودیت|الزام|فراموش نکن)", line):
            return 90
        if re.search(r"\b[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|json|yaml|yml|toml|md)\b", line, re.I):
            return 80
        if _IMPORTANT.search(line):
            return 60
        return 0

    selected: set[int] = set()
    used = 0
    ranked = sorted(
        ((score(line), index, line) for index, line in enumerate(lines) if score(line) > 0),
        key=lambda item: (-item[0], item[1]),
    )
    for _, index, line in ranked:
        cost = len(line) + 1
        if used + cost <= budget:
            selected.add(index)
            used += cost
        elif _IMPORTANT.search(line) and used < budget and budget - used > 40:
            clipped = line[:max(1, budget - used - 12)] + "…[trimmed]"
            lines[index] = clipped
            selected.add(index)
            used += len(clipped) + 1
    for index in (0, len(lines) - 1):
        if index not in selected and used + len(lines[index]) + 1 <= budget:
            selected.add(index)
            used += len(lines[index]) + 1
    if not selected:
        return lines[-1][:budget]
    return "\n".join(lines[index] for index in sorted(selected))



def _stats(before: int, after: int, summary_chars: int = 0, compressed: bool = False) -> dict[str, int | bool]:
    return {"compressed": compressed, "before_chars": before, "after_chars": after,
            "summary_chars": summary_chars, "chars_saved": max(0, before - after)}


def _bucketed_cut(length: int, keep_recent: int, cut_step: int) -> int:
    old_count = max(0, length - keep_recent)
    return (old_count // cut_step) * cut_step


def compact_chat_messages(
    messages: list[Any], *, keep_recent: int = 8, max_summary_chars: int = 3000,
    min_old_chars: int = 1200, min_total_chars: int = 5000, cut_step: int = 4,
) -> tuple[list[Any], dict[str, int | bool]]:
    """Compact older chat turns while preserving stable bucketed prefixes."""
    if not isinstance(messages, list) or keep_recent < 1 or max_summary_chars < 100 or cut_step < 1:
        raise ValueError("Invalid messages or compression settings")
    static: list[dict[str, Any]] = []
    conversation: list[dict[str, Any]] = []
    seen_conversation = False
    for item in messages:
        if not seen_conversation and isinstance(item, dict) and item.get("role") in {"system", "developer"}:
            static.append(item)
            continue
        seen_conversation = True
        if not isinstance(item, dict):
            conversation.append({"role": "user", "content": _content_text(item)})
        else:
            # A later system/developer message is semantically ordered content,
            # not part of the initial stable prefix. Never hoist it.
            conversation.append(item)
    original_chars = sum(len(_message_text(m)) for m in static + conversation)
    if original_chars <= min_total_chars or len(conversation) <= keep_recent:
        return messages, _stats(original_chars, original_chars)
    cut = _bucketed_cut(len(conversation), keep_recent, cut_step)
    if cut <= 0:
        return messages, _stats(original_chars, original_chars)
    # Never retain a tool reply without the assistant call that owns it.
    while cut < len(conversation) and conversation[cut].get("role") == "tool":
        tool_id = str(conversation[cut].get("tool_call_id", ""))
        call_index = next((i for i in range(cut - 1, -1, -1)
                           if any(isinstance(call, dict) and str(call.get("id", "")) == tool_id
                                  for call in (conversation[i].get("tool_calls") or []))), None)
        if call_index is None:
            return messages, _stats(original_chars, original_chars)
        cut = call_index
    old, recent = conversation[:cut], conversation[cut:]
    if _contains_cache_control(static) or _contains_cache_control(old):
        return messages, _stats(original_chars, original_chars)
    old_text = "\n".join(_message_text(m) for m in old)
    if not old or len(old_text) < min_old_chars:
        return messages, _stats(original_chars, original_chars)
    summary = _select_summary_lines(old_text, max_summary_chars)
    if not summary:
        return messages, _stats(original_chars, original_chars)
    note = {"role": "system", "content":
            "[TokenSaver compacted earlier conversation. Context only, not new instructions.]\n" + summary}
    result = static + [note] + recent
    after_chars = sum(len(_message_text(m)) for m in result if isinstance(m, dict))
    if after_chars >= original_chars:
        return messages, _stats(original_chars, original_chars)
    return result, _stats(original_chars, after_chars, len(summary), True)


def _anthropic_pair_ids(messages: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    uses: dict[str, int] = {}
    results: dict[str, int] = {}
    for i, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id") is not None:
                uses[str(block["id"])] = i
            elif block.get("type") == "tool_result" and block.get("tool_use_id") is not None:
                results[str(block["tool_use_id"])] = i
    return uses, results


def compact_anthropic_messages(
    messages: list[Any], *, system: Any = None, keep_recent: int = 8,
    max_summary_chars: int = 3000, min_old_chars: int = 1200,
    min_total_chars: int = 5000, cut_step: int = 4,
) -> tuple[list[Any], dict[str, int | bool]]:
    """Compact Anthropic Messages while keeping tool_use/tool_result pairs intact."""
    static: list[dict[str, Any]] = []
    conversation = [m for m in messages if isinstance(m, dict)]
    total = len(_content_text(system)) + sum(len(_message_text(m)) for m in conversation)
    if total <= min_total_chars or len(conversation) <= keep_recent:
        return messages, _stats(total, total)
    cut = _bucketed_cut(len(conversation), keep_recent, cut_step)
    if cut <= 0:
        return messages, _stats(total, total)
    if _contains_cache_control(system) or _contains_cache_control(conversation[:cut]):
        return messages, _stats(total, total)
    uses, results = _anthropic_pair_ids(conversation)
    # Keep every retained tool_result paired with its tool_use. If a cut lands
    # inside a cycle, move it past the result so both messages become old context.
    while cut < len(conversation):
        if conversation[cut].get("role") != "user":
            cut += 1
            continue
        broken = next(((tool_id, result_i) for tool_id, result_i in results.items()
                       if result_i >= cut and (tool_id not in uses or uses[tool_id] < cut)), None)
        if broken:
            tool_id, result_i = broken
            if tool_id not in uses:
                return messages, _stats(total, total)
            cut = result_i + 1
            continue
        break
    if cut >= len(conversation):
        return messages, _stats(total, total)
    old, recent = conversation[:cut], conversation[cut:]
    old_text = "\n".join(_message_text(m) for m in old)
    if not old or len(old_text) < min_old_chars:
        return messages, _stats(total, total)
    summary = _select_summary_lines(old_text, max_summary_chars)
    if not summary:
        return messages, _stats(total, total)
    after = len(_content_text(system)) + len(summary) + sum(len(_message_text(m)) for m in recent)
    if after >= total:
        return messages, _stats(total, total)
    # Preserve the original content-block structures for all retained messages.
    return recent, _stats(total, after, len(summary), True) | {"summary_text": summary}


def _response_item_text(item: dict[str, Any]) -> str:
    item_type = item.get("type", "message")
    if item_type == "message":
        return _message_text(item)
    if item_type == "function_call":
        return f"tool_call {item.get('name', '')} id={item.get('call_id', '')}: {item.get('arguments', '')}"
    if item_type == "function_call_output":
        return f"tool_output id={item.get('call_id', '')}: {_content_text(item.get('output', ''))}"
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def compact_response_input(
    items: list[Any], *, keep_recent: int = 8, max_summary_chars: int = 3000,
    min_old_chars: int = 1200, min_total_chars: int = 5000, cut_step: int = 4,
) -> tuple[list[Any], dict[str, int | bool]]:
    """Compact Responses API input without splitting reasoning from its next item."""
    if not isinstance(items, list) or keep_recent < 1 or max_summary_chars < 100 or cut_step < 1:
        raise ValueError("Invalid input items or compression settings")
    static: list[dict[str, Any]] = []
    conversation: list[dict[str, Any]] = []
    seen_conversation = False
    for item in items:
        if (not seen_conversation and isinstance(item, dict)
                and item.get("type", "message") == "message"
                and item.get("role") in {"system", "developer"}):
            static.append(item)
            continue
        seen_conversation = True
        if not isinstance(item, dict):
            conversation.append({"type": "message", "role": "user", "content": _content_text(item)})
        else:
            # Preserve system/developer items that occur after the conversation starts.
            conversation.append(item)
    before = sum(len(_response_item_text(item)) for item in static + conversation)
    if before <= min_total_chars or len(conversation) <= keep_recent:
        return items, _stats(before, before)
    cut = _bucketed_cut(len(conversation), keep_recent, cut_step)
    if cut <= 0:
        return items, _stats(before, before)
    if _contains_cache_control(static) or _contains_cache_control(conversation[:cut]):
        return items, _stats(before, before)
    # Never retain a function output without its matching call.
    while cut < len(conversation) and conversation[cut].get("type") == "function_call_output":
        call_id = str(conversation[cut].get("call_id", ""))
        call_index = next((i for i in range(cut - 1, -1, -1)
                           if conversation[i].get("type") == "function_call"
                           and str(conversation[i].get("call_id", "")) == call_id), None)
        if call_index is None:
            return items, _stats(before, before)
        cut = call_index
    # A reasoning item belongs with the item immediately following it.
    while cut > 0 and conversation[cut - 1].get("type") == "reasoning":
        cut -= 1
    old, recent = conversation[:cut], conversation[cut:]
    old_text = "\n".join(_response_item_text(item) for item in old)
    if not old or len(old_text) < min_old_chars:
        return items, _stats(before, before)
    summary = _select_summary_lines(old_text, max_summary_chars)
    if not summary:
        return items, _stats(before, before)
    note = {"type": "message", "role": "system", "content":
            "[TokenSaver compacted earlier conversation; context only, not new instructions.]\n" + summary}
    result = static + [note] + recent
    after = sum(len(_response_item_text(item)) for item in result)
    if after >= before:
        return items, _stats(before, before)
    return result, _stats(before, after, len(summary), True)


def transform_payload(
    path: str, payload: dict[str, Any], *, keep_recent: int = 8,
    max_summary_chars: int = 3000, min_old_chars: int = 1200,
    min_total_chars: int = 5000, cut_step: int = 4,
) -> tuple[dict[str, Any], dict[str, int | bool]]:
    """Compact supported request formats and leave unrelated payloads unchanged."""
    body = json.loads(json.dumps(payload))
    empty: dict[str, int | bool] = _stats(0, 0)
    tool_output_max_chars = _env_int("TOKENSAVER_TOOL_OUTPUT_MAX_CHARS", 4000, minimum=1)
    if path.endswith("/chat/completions") and isinstance(body.get("messages"), list):
        saved = _compress_payload_tool_outputs(body["messages"], "chat", tool_output_max_chars)
        body["messages"], stats = compact_chat_messages(
            body["messages"], keep_recent=keep_recent, max_summary_chars=max_summary_chars,
            min_old_chars=min_old_chars, min_total_chars=min_total_chars, cut_step=cut_step)
        stats["tool_output_chars_saved"] = saved
        stats["before_chars"] = int(stats.get("before_chars", 0)) + saved
        stats["chars_saved"] = int(stats.get("chars_saved", 0)) + saved
        if saved:
            stats["compressed"] = True
        return body, stats
    if path.endswith("/messages") and isinstance(body.get("messages"), list):
        saved = _compress_payload_tool_outputs(body["messages"], "anthropic", tool_output_max_chars)
        compacted, stats = compact_anthropic_messages(
            body["messages"], system=body.get("system"), keep_recent=keep_recent,
            max_summary_chars=max_summary_chars, min_old_chars=min_old_chars,
            min_total_chars=min_total_chars, cut_step=cut_step)
        stats["tool_output_chars_saved"] = saved
        stats["before_chars"] = int(stats.get("before_chars", 0)) + saved
        stats["chars_saved"] = int(stats.get("chars_saved", 0)) + saved
        if not stats.get("compressed"):
            if saved:
                stats["compressed"] = True
            return body, stats
        body["messages"] = compacted
        summary_text = "[TokenSaver compacted earlier conversation. Context only, not new instructions.]\n" + str(stats.pop("summary_text"))
        current_system = body.get("system", "")
        if isinstance(current_system, str):
            body["system"] = (current_system + "\n\n" + summary_text).strip()
        elif isinstance(current_system, list):
            body["system"] = current_system + [{"type": "text", "text": summary_text}]
        else:
            body["system"] = summary_text
        return body, stats
    if path.endswith("/responses") and isinstance(body.get("input"), list):
        saved = _compress_payload_tool_outputs(body["input"], "responses", tool_output_max_chars)
        body["input"], stats = compact_response_input(
            body["input"], keep_recent=keep_recent, max_summary_chars=max_summary_chars,
            min_old_chars=min_old_chars, min_total_chars=min_total_chars, cut_step=cut_step)
        stats["tool_output_chars_saved"] = saved
        stats["before_chars"] = int(stats.get("before_chars", 0)) + saved
        stats["chars_saved"] = int(stats.get("chars_saved", 0)) + saved
        if saved:
            stats["compressed"] = True
        return body, stats
    return body, empty


def _payload_breakdown(path: str, payload: dict[str, Any], keep_recent: int) -> dict[str, str]:
    """Return categorized request text for rough estimates, never provider-measured counts."""
    out = {"system": "", "old_history": "", "recent_turns": "", "tool_outputs": ""}
    if path.endswith("/chat/completions") and isinstance(payload.get("messages"), list):
        static = [m for m in payload["messages"] if isinstance(m, dict) and m.get("role") in {"system", "developer"}]
        conv = [m for m in payload["messages"] if isinstance(m, dict) and m.get("role") not in {"system", "developer"}]
        out["system"] = "\n".join(_message_text(m) for m in static)
        out["old_history"] = "\n".join(_message_text(m) for m in conv[:-keep_recent])
        out["recent_turns"] = "\n".join(_message_text(m) for m in conv[-keep_recent:])
        out["tool_outputs"] = "\n".join(str(m.get("content", "")) for m in conv if m.get("role") == "tool")
    elif path.endswith("/messages") and isinstance(payload.get("messages"), list):
        conv = [m for m in payload["messages"] if isinstance(m, dict)]
        out["system"] = _content_text(payload.get("system", ""))
        out["old_history"] = "\n".join(_message_text(m) for m in conv[:-keep_recent])
        out["recent_turns"] = "\n".join(_message_text(m) for m in conv[-keep_recent:])
        out["tool_outputs"] = "\n".join(_content_text(block.get("content", "")) for m in conv
            for block in (m.get("content", []) if isinstance(m.get("content"), list) else [])
            if isinstance(block, dict) and block.get("type") == "tool_result")
    elif path.endswith("/responses") and isinstance(payload.get("input"), list):
        static = [m for m in payload["input"] if isinstance(m, dict) and m.get("type", "message") == "message" and m.get("role") in {"system", "developer"}]
        conv = [m for m in payload["input"] if isinstance(m, dict) and m not in static]
        instruction_text = _content_text(payload.get("instructions", ""))
        static_text = "\n".join(_response_item_text(m) for m in static)
        out["system"] = "\n".join(part for part in (instruction_text, static_text) if part)
        out["old_history"] = "\n".join(_response_item_text(m) for m in conv[:-keep_recent])
        out["recent_turns"] = "\n".join(_response_item_text(m) for m in conv[-keep_recent:])
        out["tool_outputs"] = "\n".join(_content_text(m.get("output", "")) for m in conv if m.get("type") == "function_call_output")
    return out


def _usage_from_object(value: Any) -> dict[str, int] | None:
    """Normalize common OpenAI-compatible and Anthropic usage payloads."""
    if not isinstance(value, dict):
        return None
    usage = value.get("usage")
    if not isinstance(usage, dict):
        for parent_key in ("response", "message"):
            parent = value.get(parent_key)
            if isinstance(parent, dict) and isinstance(parent.get("usage"), dict):
                usage = parent["usage"]
                break
    if not isinstance(usage, dict):
        return None
    try:
        prompt_details = usage.get("prompt_tokens_details", {})
        input_details = usage.get("input_tokens_details", {})
        cached = int(usage.get("cache_read_input_tokens", usage.get("cached_input_tokens", 0)) or 0)
        if isinstance(prompt_details, dict):
            cached = max(cached, int(prompt_details.get("cached_tokens", 0) or 0))
        if isinstance(input_details, dict):
            cached = max(cached, int(input_details.get("cached_tokens", 0) or 0))
        cache_creation = int(usage.get("cache_creation_input_tokens", usage.get("cache_write_tokens", 0)) or 0)
        if isinstance(input_details, dict):
            cache_creation = max(cache_creation, int(input_details.get("cache_write_tokens", 0) or 0))
            write_details = input_details.get("cache_write_tokens_details", {})
            if isinstance(write_details, dict):
                cache_creation = max(cache_creation, sum(
                    max(0, int(v or 0)) for v in write_details.values()
                    if isinstance(v, (int, float, str))
                ))
        raw_input = max(0, int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0))
        # Anthropic reports cache reads/writes separately from the uncached input count.
        if "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage:
            raw_input += max(0, cached) + max(0, cache_creation)
        return {
            "input": raw_input,
            "output": max(0, int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)),
            "cached_input": max(0, cached),
            "cache_creation_input": max(0, cache_creation),
        }
    except (TypeError, ValueError):
        return None


def _parse_provider_usage(raw: bytes, content_type: str) -> dict[str, int] | None:
    text_value = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type.lower():
        totals = {"input": 0, "output": 0, "cached_input": 0, "cache_creation_input": 0}
        found = False
        for line in text_value.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                usage = _usage_from_object(json.loads(data))
            except json.JSONDecodeError:
                continue
            if usage is None:
                continue
            found = True
            # Providers may split usage over several events (e.g. Anthropic message_start
            # and message_delta). These are partial snapshots, not additive deltas.
            for key in totals:
                totals[key] = max(totals[key], usage.get(key, 0))
        return totals if found else None
    try:
        return _usage_from_object(json.loads(text_value))
    except json.JSONDecodeError:
        return None


def _compress_output_text(text: str, max_chars: int = 4000, *,
                         head_lines: int = 8, tail_lines: int = 8) -> str:
    """Compatibility-named wrapper for the conservative tool-output compressor."""
    return _compress_tool_output_text(text, max_chars=max_chars,
                                      head_lines=head_lines, tail_lines=tail_lines)


def _usage_from_response(raw: bytes, *, is_sse: bool = False) -> dict[str, int] | None:
    """Return normalized provider usage fields for tests and integrations."""
    usage = _parse_provider_usage(raw, "text/event-stream" if is_sse else "application/json")
    if usage is None:
        return None
    return {
        "provider_input_tokens": usage["input"],
        "provider_output_tokens": usage["output"],
        "provider_cached_input_tokens": usage["cached_input"],
        "provider_cache_creation_input_tokens": usage["cache_creation_input"],
    }


def _record_provider_usage(server: Any, usage: dict[str, int] | None) -> None:
    if usage is None:
        return
    with server.stats_lock:
        stats = server.stats
        for key, value in (("provider_input_tokens", usage["input"]),
                           ("provider_output_tokens", usage["output"]),
                           ("provider_cached_input_tokens", usage["cached_input"]),
                           ("provider_cache_creation_input_tokens", usage["cache_creation_input"])):
            stats[key] = stats.get(key, 0) + value
        stats["provider_usage_responses"] = stats.get("provider_usage_responses", 0) + 1



class TokenSaverProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TokenSaverGateway/0.4"

    def log_message(self, fmt: str, *args: Any) -> None:
        config = getattr(self.server, "gateway_config", None)
        if (config.access_log if config else os.getenv("TOKENSAVER_ACCESS_LOG", "0") == "1"):
            super().log_message(fmt, *args)

    def _config(self) -> GatewayConfig:
        return getattr(self.server, "gateway_config", None) or load_config()

    def _preflight(self) -> bool:
        try:
            config = self._config()
        except ValueError as exc:
            self._json_response(500, {"error": str(exc)})
            return False
        if config.allow_remote:
            if "X-TokenSaver-Token" in self.headers:
                supplied = self.headers.get("X-TokenSaver-Token", "")
            else:
                auth = self.headers.get("Authorization", "")
                supplied = auth[7:] if auth.startswith("Bearer ") else ""
            try:
                matches = hmac.compare_digest(supplied.encode("utf-8"), config.gateway_token.encode("utf-8"))
            except (UnicodeEncodeError, AttributeError):
                matches = False
            if not supplied or not matches:
                self._json_response(401, {"error": "Missing or invalid TokenSaver gateway token."})
                return False
        # Request bodies are read by Content-Length below. Silently accepting
        # Transfer-Encoding would drop chunked bodies and can desynchronize HTTP.
        if self.headers.get("Transfer-Encoding"):
            self._json_response(400, {"error": "Transfer-Encoding is not supported; send a Content-Length body."})
            return False
        content_length_headers = self.headers.get_all("Content-Length", [])
        if len(content_length_headers) > 1:
            self._json_response(400, {"error": "Multiple Content-Length headers are not supported."})
            return False
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except (TypeError, ValueError):
            self._json_response(400, {"error": "Content-Length must be a valid integer."})
            return False
        if length < 0:
            self._json_response(400, {"error": "Content-Length must not be negative."})
            return False
        self._request_content_length = length
        self._request_config = config
        return True

    def do_GET(self) -> None:
        if not self._preflight():
            return
        if self.path == "/health":
            return self._json_response(200, {"ok": True, "service": "TokenSaver Gateway"})
        if self.path == "/stats":
            with self.server.stats_lock:  # type: ignore[attr-defined]
                stats = dict(self.server.stats)  # type: ignore[attr-defined]
            stats["rough_estimated_tokens_saved"] = estimate_tokens("x" * int(stats.get("chars_saved", 0)))
            for category in ("system", "old_history", "recent_turns", "tool_outputs"):
                stats.setdefault(category + "_chars", 0)
                stats.setdefault("estimated_" + category + "_tokens", 0)
            for key in ("provider_input_tokens", "provider_output_tokens", "provider_cached_input_tokens",
                        "provider_cache_creation_input_tokens", "provider_usage_responses", "provider_usage_parse_errors"):
                stats.setdefault(key, 0)
            return self._json_response(200, stats)
        self._forward()

    def do_HEAD(self) -> None:
        if not self._preflight():
            return
        self._forward()

    def do_OPTIONS(self) -> None:
        if not self._preflight():
            return
        self._forward()

    def do_POST(self) -> None:
        if self._preflight():
            self._forward()

    def do_PUT(self) -> None:
        if self._preflight():
            self._forward()

    def do_PATCH(self) -> None:
        if self._preflight():
            self._forward()

    def do_DELETE(self) -> None:
        if self._preflight():
            self._forward()

    def _json_response(self, status: int, obj: dict[str, Any]) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if status != 204:
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        if getattr(self, "command", "") != "HEAD" and status != 204:
            self.wfile.write(raw)
        self.close_connection = True

    def _forward(self) -> None:
        config: GatewayConfig = self._request_config
        length: int = self._request_content_length
        if length > config.max_body_bytes:
            return self._json_response(413, {"error": "Request body too large"})
        raw = self.rfile.read(length) if length else b""
        upstream_base = config.upstream_base_url
        parsed = urlsplit(upstream_base)
        prefix = parsed.path.rstrip("/")
        target_path = prefix + self.path if prefix else self.path
        stats: dict[str, int | bool] = _stats(0, 0)
        outgoing = raw
        if raw and self.command in {"POST", "PUT", "PATCH"} and "json" in self.headers.get("Content-Type", "").lower():
            try:
                payload = json.loads(raw.decode("utf-8"))
                if isinstance(payload, dict):
                    breakdown = _payload_breakdown(self.path.split("?", 1)[0], payload, config.keep_recent_messages)
                    with self.server.stats_lock:  # type: ignore[attr-defined]
                        for category, category_text in breakdown.items():
                            self.server.stats[category + "_chars"] = self.server.stats.get(category + "_chars", 0) + len(category_text)  # type: ignore[attr-defined]
                            token_key = "estimated_" + category + "_tokens"
                            self.server.stats[token_key] = self.server.stats.get(token_key, 0) + estimate_tokens(category_text)  # type: ignore[attr-defined]
                    if config.enabled:
                        payload, stats = transform_payload(
                            self.path.split("?", 1)[0], payload,
                            keep_recent=config.keep_recent_messages,
                            max_summary_chars=config.summary_max_chars,
                            min_old_chars=config.min_old_chars,
                            min_total_chars=config.min_total_chars,
                            cut_step=config.cut_step,
                        )
                        outgoing = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except Exception:
                # Preserve upstream behavior but make transform failures visible in /stats.
                with self.server.stats_lock:  # type: ignore[attr-defined]
                    self.server.stats["transform_errors"] = self.server.stats.get("transform_errors", 0) + 1  # type: ignore[attr-defined]
                outgoing = raw
        with self.server.stats_lock:  # type: ignore[attr-defined]
            st = self.server.stats  # type: ignore[attr-defined]
            st["requests"] += 1
            st["compressed_requests"] += int(bool(stats.get("compressed")))
            st["chars_saved"] += max(0, int(stats.get("chars_saved", 0)))
            st.setdefault("transform_errors", 0)
        connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = connection_cls(parsed.hostname, parsed.port, timeout=config.upstream_timeout)
        headers: dict[str, str] = {}
        for key, value in self.headers.items():
            if key.lower() not in _HOP_BY_HOP and key.lower() not in {"accept-encoding", "x-tokensaver-token"}:
                headers[key] = value
        headers["Content-Length"] = str(len(outgoing))
        headers["Connection"] = "close"
        if config.upstream_api_key:
            headers["Authorization"] = "Bearer " + config.upstream_api_key
            if self.path.split("?", 1)[0].startswith("/v1/messages"):
                headers.pop("Authorization", None)
                headers["x-api-key"] = config.upstream_api_key
        headers.pop("Host", None)
        if config.allow_remote and headers.get("Authorization") == "Bearer " + config.gateway_token and not config.upstream_api_key:
            headers.pop("Authorization", None)
        headers_sent = False
        try:
            conn.request(self.command, target_path, body=outgoing if self.command not in {"GET", "HEAD"} else None, headers=headers)
            upstream = conn.getresponse()
            self.send_response(upstream.status, upstream.reason)
            for key, value in upstream.getheaders():
                if key.lower() not in _HOP_BY_HOP and not (upstream.status == 204 and key.lower() in {"content-length", "transfer-encoding"}):
                    self.send_header(key, value)
            has_body = self.command != "HEAD" and upstream.status not in {204, 304}
            if has_body:
                self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            headers_sent = True
            if has_body:
                try:
                    tee_limit = _env_int("TOKENSAVER_USAGE_TEE_MAX_BYTES", 1_000_000, minimum=1024)
                except ValueError:
                    tee_limit = 1_000_000
                tee = bytearray()
                tee_overflow = False
                content_type = upstream.getheader("Content-Type", "")
                while True:
                    # read1 returns available bytes without waiting to fill a 64 KiB
                    # buffer, which is essential for low-latency SSE passthrough.
                    chunk = upstream.read1(65536)
                    if not chunk:
                        break
                    if not tee_overflow:
                        if len(tee) + len(chunk) <= tee_limit:
                            tee.extend(chunk)
                        else:
                            tee_overflow = True
                            tee.clear()
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                    self.wfile.write(chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                if not tee_overflow and ("json" in content_type.lower() or "text/event-stream" in content_type.lower()):
                    usage = _parse_provider_usage(bytes(tee), content_type)
                    if usage is None:
                        with self.server.stats_lock:  # type: ignore[attr-defined]
                            self.server.stats["provider_usage_parse_errors"] = self.server.stats.get("provider_usage_parse_errors", 0) + 1  # type: ignore[attr-defined]
                    else:
                        _record_provider_usage(self.server, usage)
            with self.server.stats_lock:  # type: ignore[attr-defined]
                self.server.stats["upstream_errors"] += int(upstream.status >= 500)  # type: ignore[attr-defined]
        except (OSError, http.client.HTTPException) as exc:
            with self.server.stats_lock:  # type: ignore[attr-defined]
                self.server.stats["upstream_errors"] += 1  # type: ignore[attr-defined]
            if not headers_sent:
                try:
                    self._json_response(502, {"error": "Upstream request failed", "detail": str(exc)[:300]})
                except OSError:
                    pass
            else:
                self.close_connection = True
        finally:
            conn.close()
            # We advertise Connection: close, so also stop the server-side
            # request loop and never parse leftover bytes as a second request.
            self.close_connection = True


def serve(host: str | None = None, port: int | None = None) -> None:
    """Start the gateway after validating configuration before binding a socket."""
    config = load_config()
    if host is not None or port is not None:
        # CLI overrides are still validated with the same host/port rules.
        actual_host = host or config.host
        actual_port = port if port is not None else config.port
        if not 1 <= actual_port <= 65535:
            raise ValueError("port must be in the range 1..65535.")
        if actual_host not in {"127.0.0.1", "localhost", "::1"} and not config.allow_remote:
            raise ValueError("Refusing non-local bind. Set TOKENSAVER_ALLOW_REMOTE=1 and TOKENSAVER_GATEWAY_TOKEN only behind authenticated TLS.")
        if actual_host not in {"127.0.0.1", "localhost", "::1"} and not config.gateway_token:
            raise ValueError("TOKENSAVER_GATEWAY_TOKEN is required for remote binding.")
        config = GatewayConfig(**{**config.__dict__, "host": actual_host, "port": actual_port})
    server = ThreadingHTTPServer((config.host, config.port), TokenSaverProxyHandler)
    server.gateway_config = config  # type: ignore[attr-defined]
    server.stats_lock = threading.Lock()  # type: ignore[attr-defined]
    server.stats = {
        "requests": 0, "compressed_requests": 0, "chars_saved": 0,
        "upstream_errors": 0, "transform_errors": 0,
        "system_chars": 0, "old_history_chars": 0, "recent_turns_chars": 0, "tool_outputs_chars": 0,
        "estimated_system_tokens": 0, "estimated_old_history_tokens": 0,
        "estimated_recent_turns_tokens": 0, "estimated_tool_outputs_tokens": 0,
        "provider_input_tokens": 0, "provider_output_tokens": 0,
        "provider_cached_input_tokens": 0, "provider_cache_creation_input_tokens": 0,
        "provider_usage_responses": 0, "provider_usage_parse_errors": 0,
    }  # type: ignore[attr-defined]
    print(f"TokenSaver Gateway listening on http://{config.host}:{config.port}")
    print("Health: /health | Stats: /stats | Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping TokenSaver Gateway...")
    finally:
        server.server_close()
