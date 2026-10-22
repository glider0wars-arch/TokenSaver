"""Local API gateway with conservative, bucketed context compaction."""
from __future__ import annotations

import hmac
import http.client
import json
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
    r"\b(?:error|exception|traceback|failed|failure|regression|decision|todo|fixme|"
    r"constraint|requirement|critical|blocker|rollback|security issue)\b|"
    r"\b(?:do not|don't|never|must not|should not|cannot|can't|remember this)\b|"
    r"(?:^|[\s:،.!؟])(?:خطا|اشکال|تصمیم|محدودیت|الزام|مهم است|فراموش نکن|انجام نشود)(?=$|[\s:،.!؟])|"
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
    parsed = urlsplit(upstream)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
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
    if timeout <= 0:
        raise ValueError("TOKENSAVER_UPSTREAM_TIMEOUT must be greater than zero.")
    host = os.getenv("TOKENSAVER_HOST", "127.0.0.1")
    port = _env_int("TOKENSAVER_PORT", 8765, minimum=1, maximum=65535)
    allow_remote = remote_raw == "1"
    token = os.getenv("TOKENSAVER_GATEWAY_TOKEN", "")
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
        upstream_api_key=os.getenv("TOKENSAVER_UPSTREAM_API_KEY", ""),
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
                kind = part.get("type", "")
                value = part.get("text")
                if isinstance(value, str):
                    pieces.append(value)
                elif kind in {"tool_use", "tool_result", "function_call", "function_call_output"}:
                    pieces.append(json.dumps(part, ensure_ascii=False, separators=(",", ":")))
                elif isinstance(part.get("content"), (str, list, dict)):
                    pieces.append(_content_text(part["content"]))
                else:
                    # Preserve non-text structured blocks in the compacted summary.
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


def _contains_cache_control(value: Any) -> bool:
    if isinstance(value, dict):
        return "cache_control" in value or any(_contains_cache_control(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_cache_control(v) for v in value)
    return False


def _select_summary_lines(text: str, budget: int) -> str:
    """Extract readable, deterministic lines, preferring explicit high-signal items."""
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
    selected = {i for i, line in enumerate(lines) if _IMPORTANT.search(line)}
    selected.update({0, len(lines) - 1})
    output: list[str] = []
    used = 0
    for i in sorted(selected):
        line = lines[i]
        remaining = budget - used
        if remaining <= 0:
            break
        if len(line) > remaining:
            if _IMPORTANT.search(line) and remaining > 40:
                line = line[:max(1, remaining - 12)] + "…[trimmed]"
            else:
                continue
        output.append(line)
        used += len(line) + 1
    if not output:
        return lines[-1][:budget]
    return "\n".join(output)


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
    if _contains_cache_control(messages):
        total = sum(len(_message_text(m)) for m in messages if isinstance(m, dict))
        return messages, _stats(total, total)
    static: list[dict[str, Any]] = []
    conversation: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            conversation.append({"role": "user", "content": _content_text(item)})
        elif item.get("role") in {"system", "developer"}:
            static.append(item)
        else:
            conversation.append(item)
    original_chars = sum(len(_message_text(m)) for m in messages if isinstance(m, dict))
    if original_chars <= min_total_chars or len(conversation) <= keep_recent:
        return messages, _stats(original_chars, original_chars)
    cut = _bucketed_cut(len(conversation), keep_recent, cut_step)
    if cut <= 0:
        return messages, _stats(original_chars, original_chars)
    old, recent = conversation[:cut], conversation[cut:]
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
    if _contains_cache_control(system) or _contains_cache_control(messages):
        total = len(_content_text(system)) + sum(len(_message_text(m)) for m in messages if isinstance(m, dict))
        return messages, _stats(total, total)
    static: list[dict[str, Any]] = []
    conversation = [m for m in messages if isinstance(m, dict)]
    total = len(_content_text(system)) + sum(len(_message_text(m)) for m in conversation)
    if total <= min_total_chars or len(conversation) <= keep_recent:
        return messages, _stats(total, total)
    cut = _bucketed_cut(len(conversation), keep_recent, cut_step)
    if cut <= 0:
        return messages, _stats(total, total)
    uses, results = _anthropic_pair_ids(conversation)
    # If a tool pair straddles the cut, move it so both halves remain together.
    for tool_id in set(uses) & set(results):
        use_i, result_i = uses[tool_id], results[tool_id]
        if use_i < cut <= result_i:
            cut = result_i + 1  # Both messages become old context.
        elif result_i < cut <= use_i:
            cut = result_i  # Keep the user result and its following tool_use together in recent context.
    # The retained window must begin at a user turn, never at an assistant/tool message.
    while cut < len(conversation) and conversation[cut].get("role") != "user":
        cut += 1
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
    if _contains_cache_control(items):
        total = sum(len(_response_item_text(x)) for x in items if isinstance(x, dict))
        return items, _stats(total, total)
    static: list[dict[str, Any]] = []
    conversation: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            conversation.append({"type": "message", "role": "user", "content": _content_text(item)})
        elif item.get("type", "message") == "message" and item.get("role") in {"system", "developer"}:
            static.append(item)
        else:
            conversation.append(item)
    before = sum(len(_response_item_text(item)) for item in items if isinstance(item, dict))
    if before <= min_total_chars or len(conversation) <= keep_recent:
        return items, _stats(before, before)
    cut = _bucketed_cut(len(conversation), keep_recent, cut_step)
    if cut <= 0:
        return items, _stats(before, before)
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
    if _contains_cache_control(body):
        return body, empty
    if path.endswith("/chat/completions") and isinstance(body.get("messages"), list):
        body["messages"], stats = compact_chat_messages(
            body["messages"], keep_recent=keep_recent, max_summary_chars=max_summary_chars,
            min_old_chars=min_old_chars, min_total_chars=min_total_chars, cut_step=cut_step)
        return body, stats
    if path.endswith("/messages") and isinstance(body.get("messages"), list):
        original_messages = body["messages"]
        compacted, stats = compact_anthropic_messages(
            original_messages, system=body.get("system"), keep_recent=keep_recent,
            max_summary_chars=max_summary_chars, min_old_chars=min_old_chars,
            min_total_chars=min_total_chars, cut_step=cut_step)
        if not stats.get("compressed"):
            return body, stats
        body["messages"] = compacted
        summary_text = str(stats.pop("summary_text"))
        current_system = body.get("system", "")
        if isinstance(current_system, str):
            body["system"] = (current_system + "\n\n" + summary_text).strip()
        elif isinstance(current_system, list):
            body["system"] = current_system + [{"type": "text", "text": summary_text}]
        else:
            body["system"] = summary_text
        return body, stats
    if path.endswith("/responses") and isinstance(body.get("input"), list):
        body["input"], stats = compact_response_input(
            body["input"], keep_recent=keep_recent, max_summary_chars=max_summary_chars,
            min_old_chars=min_old_chars, min_total_chars=min_total_chars, cut_step=cut_step)
        return body, stats
    return body, empty


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
            length = int(self.headers.get("Content-Length", "0") or "0")
        except (TypeError, ValueError):
            self._json_response(400, {"error": "Content-Length must be a valid integer."})
            return False
        if length < 0:
            self._json_response(400, {"error": "Content-Length must not be negative."})
            return False
        try:
            config = self._config()
        except ValueError as exc:
            self._json_response(500, {"error": str(exc)})
            return False
        if config.allow_remote:
            supplied = self.headers.get("X-TokenSaver-Token", "")
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                supplied = auth[7:]
            if not supplied or not hmac.compare_digest(supplied, config.gateway_token):
                self._json_response(401, {"error": "Missing or invalid TokenSaver gateway token."})
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
            return self._json_response(200, stats)
        self._forward()

    def do_HEAD(self) -> None:
        if not self._preflight():
            return
        self._forward()

    def do_OPTIONS(self) -> None:
        if not self._preflight():
            return
        self._json_response(204, {})

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
                if isinstance(payload, dict) and config.enabled:
                    payload, stats = transform_payload(
                        self.path.split("?", 1)[0], payload,
                        keep_recent= config.keep_recent_messages,
                        max_summary_chars=config.summary_max_chars,
                        min_old_chars=config.min_old_chars,
                        min_total_chars=config.min_total_chars,
                        cut_step=config.cut_step,
                    )
                    outgoing = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                # Malformed JSON or payload shape is forwarded unchanged, not silently destroyed.
                outgoing = raw
        with self.server.stats_lock:  # type: ignore[attr-defined]
            st = self.server.stats  # type: ignore[attr-defined]
            st["requests"] += 1
            st["compressed_requests"] += int(bool(stats.get("compressed")))
            st["chars_saved"] += max(0, int(stats.get("chars_saved", 0)))
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
        headers_sent = False
        try:
            conn.request(self.command, target_path, body=outgoing if self.command not in {"GET", "HEAD"} else None, headers=headers)
            upstream = conn.getresponse()
            self.send_response(upstream.status, upstream.reason)
            for key, value in upstream.getheaders():
                if key.lower() not in _HOP_BY_HOP:
                    self.send_header(key, value)
            has_body = self.command != "HEAD" and upstream.status not in {204, 304}
            if has_body:
                self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            headers_sent = True
            if has_body:
                while True:
                    chunk = upstream.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                    self.wfile.write(chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
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
    server.stats = {"requests": 0, "compressed_requests": 0, "chars_saved": 0, "upstream_errors": 0}  # type: ignore[attr-defined]
    print(f"TokenSaver Gateway listening on http://{config.host}:{config.port}")
    print("Health: /health | Stats: /stats | Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping TokenSaver Gateway...")
    finally:
        server.server_close()
