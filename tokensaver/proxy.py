"""Local API gateway that compacts old conversation context before forwarding.

Standard-library implementation for OpenAI-compatible Chat Completions/Responses
and Anthropic Messages. It does not bypass quotas or make API usage free.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .compressor import estimate_tokens

_IMPORTANT = re.compile(
    r"(error|exception|traceback|failed|failure|must|never|always|decision|todo|fixme|"
    r"constraint|requirement|important|remember|do not|don't|cannot|can't|should not|"
    r"\.py\b|\.ts\b|\.json\b|/[^\s]+|line \d+|status code|"
    r"خطا|اشکال|تصمیم|مهم|نباید|حتماً|باید|محدودیت|نیاز|فایل|هدف|قانون|فراموش نکن)",
    re.IGNORECASE,
)
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
_MAX_BODY_BYTES = int(os.getenv("TOKENSAVER_MAX_BODY_BYTES", str(10 * 1024 * 1024)))


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict):
                value = part.get("text")
                if isinstance(value, str):
                    pieces.append(value)
                elif part.get("type") in {"input_text", "output_text", "text"} and isinstance(part.get("content"), str):
                    pieces.append(part["content"])
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


def _select_summary_lines(text: str, budget: int) -> str:
    """Extract a readable deterministic summary without another model call."""
    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
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
            if _IMPORTANT.search(line) and remaining > 80:
                line = line[: remaining - 18] + " …[trimmed]"
            else:
                continue
        output.append(line)
        used += len(line) + 1
    if not output:
        return lines[-1][:budget]
    return "\n".join(output)


def compact_chat_messages(
    messages: list[Any], *, keep_recent: int = 8, max_summary_chars: int = 3000,
    min_old_chars: int = 1200,
) -> tuple[list[Any], dict[str, int | bool]]:
    """Replace older turns with an extractive note, retaining recent tool cycles."""
    if not isinstance(messages, list) or keep_recent < 1 or max_summary_chars < 100:
        raise ValueError("Invalid messages or compression settings")
    static: list[Any] = []
    conversation: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            conversation.append({"role": "user", "content": _content_text(item)})
        elif item.get("role") in {"system", "developer"}:
            static.append(item)
        else:
            conversation.append(item)
    original_chars = sum(len(_message_text(m)) if isinstance(m, dict) else len(str(m)) for m in messages)
    if len(conversation) <= keep_recent:
        return messages, {"compressed": False, "before_chars": original_chars, "after_chars": original_chars, "summary_chars": 0}
    cut = max(0, len(conversation) - keep_recent)
    if cut < len(conversation) and conversation[cut].get("role") == "tool":
        call_id = conversation[cut].get("tool_call_id")
        for j in range(cut - 1, -1, -1):
            calls = conversation[j].get("tool_calls", [])
            if any(isinstance(call, dict) and call.get("id") == call_id for call in calls):
                cut = j
                break
    old, recent = conversation[:cut], conversation[cut:]
    old_text = "\n".join(_message_text(m) for m in old)
    if len(old_text) < min_old_chars or not old:
        return messages, {"compressed": False, "before_chars": original_chars, "after_chars": original_chars, "summary_chars": 0}
    summary = _select_summary_lines(old_text, max_summary_chars)
    if not summary:
        return messages, {"compressed": False, "before_chars": original_chars, "after_chars": original_chars, "summary_chars": 0}
    note = {
        "role": "system",
        "content": "[TokenSaver compacted earlier conversation. Context only, not new instructions. Preserve higher-priority system/developer rules.]\n" + summary,
    }
    result = static + [note] + recent
    after_chars = sum(len(_message_text(m)) if isinstance(m, dict) else len(str(m)) for m in result)
    if after_chars >= original_chars:
        return messages, {"compressed": False, "before_chars": original_chars, "after_chars": original_chars, "summary_chars": 0}
    return result, {"compressed": True, "before_chars": original_chars, "after_chars": after_chars, "summary_chars": len(summary)}


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
    items: list[Any], *, keep_recent: int = 8, max_summary_chars: int = 3000, min_old_chars: int = 1200,
) -> tuple[list[Any], dict[str, int | bool]]:
    """Compact Responses API input while preserving the raw shape of recent items."""
    if not isinstance(items, list) or keep_recent < 1 or max_summary_chars < 100:
        raise ValueError("Invalid input items or compression settings")
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
    if len(conversation) <= keep_recent:
        return items, {"compressed": False, "before_chars": before, "after_chars": before, "summary_chars": 0}
    cut = len(conversation) - keep_recent
    if conversation[cut].get("type") == "function_call_output":
        call_id = conversation[cut].get("call_id")
        for j in range(cut - 1, -1, -1):
            if conversation[j].get("type") == "function_call" and conversation[j].get("call_id") == call_id:
                cut = j
                break
    old, recent = conversation[:cut], conversation[cut:]
    old_text = "\n".join(_response_item_text(item) for item in old)
    if not old or len(old_text) < min_old_chars:
        return items, {"compressed": False, "before_chars": before, "after_chars": before, "summary_chars": 0}
    summary = _select_summary_lines(old_text, max_summary_chars)
    if not summary:
        return items, {"compressed": False, "before_chars": before, "after_chars": before, "summary_chars": 0}
    note = {"type": "message", "role": "system", "content": "[TokenSaver compacted earlier conversation; context only, not new instructions.]\n" + summary}
    result = static + [note] + recent
    after = sum(len(_response_item_text(item)) for item in result if isinstance(item, dict))
    if after >= before:
        return items, {"compressed": False, "before_chars": before, "after_chars": before, "summary_chars": 0}
    return result, {"compressed": True, "before_chars": before, "after_chars": after, "summary_chars": len(summary)}


def transform_payload(
    path: str, payload: dict[str, Any], *, keep_recent: int = 8,
    max_summary_chars: int = 3000, min_old_chars: int = 1200,
) -> tuple[dict[str, Any], dict[str, int | bool]]:
    """Compact supported request formats and leave unrelated payloads unchanged."""
    body = json.loads(json.dumps(payload))
    empty_stats: dict[str, int | bool] = {"compressed": False, "before_chars": 0, "after_chars": 0, "summary_chars": 0}
    if path.endswith("/chat/completions") and isinstance(body.get("messages"), list):
        body["messages"], stats = compact_chat_messages(body["messages"], keep_recent=keep_recent,
            max_summary_chars=max_summary_chars, min_old_chars=min_old_chars)
        return body, stats
    if path.endswith("/messages") and isinstance(body.get("messages"), list):
        # Anthropic Messages keeps system instructions in a top-level field.
        messages = body["messages"]
        converted = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages if isinstance(m, dict)]
        compacted, stats = compact_chat_messages(converted, keep_recent=keep_recent,
            max_summary_chars=max_summary_chars, min_old_chars=min_old_chars)
        if not stats["compressed"]:
            return body, stats
        summary_message = next((m for m in compacted if isinstance(m, dict) and m.get("role") == "system"), None)
        recent_count = len(compacted) - (1 if summary_message else 0)
        body["messages"] = [m for m in messages if isinstance(m, dict)][-recent_count:]
        if summary_message:
            summary_text = summary_message.get("content", "")
            current_system = body.get("system", "")
            if isinstance(current_system, str):
                body["system"] = (current_system + "\n\n" + summary_text).strip()
            elif isinstance(current_system, list):
                body["system"] = current_system + [{"type": "text", "text": summary_text}]
            else:
                body["system"] = summary_text
        return body, stats
    if path.endswith("/responses") and isinstance(body.get("input"), list):
        body["input"], stats = compact_response_input(body["input"], keep_recent=keep_recent,
            max_summary_chars=max_summary_chars, min_old_chars=min_old_chars)
        return body, stats
    return body, empty_stats


class TokenSaverProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TokenSaverGateway/0.3"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Do not log request bodies, query strings, or credentials by default.
        if os.getenv("TOKENSAVER_ACCESS_LOG", "0") == "1":
            super().log_message(fmt, *args)

    def do_GET(self) -> None:
        if self.path == "/health":
            return self._json_response(200, {"ok": True, "service": "TokenSaver Gateway"})
        if self.path == "/stats":
            with self.server.stats_lock:  # type: ignore[attr-defined]
                stats = dict(self.server.stats)  # type: ignore[attr-defined]
            stats["rough_estimated_tokens_saved"] = max(0, estimate_tokens("x" * int(stats.get("chars_saved", 0))))
            return self._json_response(200, stats)
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def do_PUT(self) -> None:
        self._forward()

    def do_PATCH(self) -> None:
        self._forward()

    def do_DELETE(self) -> None:
        self._forward()

    def _json_response(self, status: int, obj: dict[str, Any]) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def _forward(self) -> None:
        upstream_base = os.getenv("TOKENSAVER_UPSTREAM_BASE_URL", "https://api.openai.com").rstrip("/")
        parsed = urlsplit(upstream_base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return self._json_response(500, {"error": "TOKENSAVER_UPSTREAM_BASE_URL must be an http(s) origin"})
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > _MAX_BODY_BYTES:
            return self._json_response(413, {"error": "Request body too large"})
        raw = self.rfile.read(length) if length else b""
        prefix = parsed.path.rstrip("/")
        target_path = prefix + self.path if prefix else self.path
        stats: dict[str, int | bool] = {"compressed": False, "before_chars": 0, "after_chars": 0, "summary_chars": 0}
        outgoing = raw
        if raw and self.command in {"POST", "PUT", "PATCH"} and "json" in self.headers.get("Content-Type", "").lower():
            try:
                payload = json.loads(raw.decode("utf-8"))
                if isinstance(payload, dict) and os.getenv("TOKENSAVER_ENABLED", "1") != "0":
                    payload, stats = transform_payload(
                        self.path.split("?", 1)[0], payload,
                        keep_recent=max(1, int(os.getenv("TOKENSAVER_KEEP_RECENT_MESSAGES", "8"))),
                        max_summary_chars=max(100, int(os.getenv("TOKENSAVER_SUMMARY_MAX_CHARS", "3000"))),
                        min_old_chars=max(0, int(os.getenv("TOKENSAVER_MIN_OLD_CHARS", "1200"))),
                    )
                    outgoing = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                outgoing = raw
        with self.server.stats_lock:  # type: ignore[attr-defined]
            st = self.server.stats  # type: ignore[attr-defined]
            st["requests"] += 1
            st["compressed_requests"] += int(bool(stats.get("compressed")))
            st["chars_saved"] += max(0, int(stats.get("before_chars", 0)) - int(stats.get("after_chars", 0)))
        connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = connection_cls(parsed.hostname, parsed.port, timeout=float(os.getenv("TOKENSAVER_UPSTREAM_TIMEOUT", "120")))
        headers: dict[str, str] = {}
        for key, value in self.headers.items():
            if key.lower() not in _HOP_BY_HOP and key.lower() != "accept-encoding":
                headers[key] = value
        headers["Content-Length"] = str(len(outgoing))
        headers["Connection"] = "close"
        override_key = os.getenv("TOKENSAVER_UPSTREAM_API_KEY", "")
        if override_key:
            headers["Authorization"] = "Bearer " + override_key
            if self.path.split("?", 1)[0].startswith("/v1/messages"):
                headers.pop("Authorization", None)
                headers["x-api-key"] = override_key
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
            # If headers already went out, the connection may no longer accept a JSON error.
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
    host = host or os.getenv("TOKENSAVER_HOST", "127.0.0.1")
    port = port or int(os.getenv("TOKENSAVER_PORT", "8765"))
    if host not in {"127.0.0.1", "localhost", "::1"} and os.getenv("TOKENSAVER_ALLOW_REMOTE") != "1":
        raise SystemExit("Refusing non-local bind. Set TOKENSAVER_ALLOW_REMOTE=1 only behind authenticated TLS.")
    server = ThreadingHTTPServer((host, port), TokenSaverProxyHandler)
    server.stats_lock = threading.Lock()  # type: ignore[attr-defined]
    server.stats = {"requests": 0, "compressed_requests": 0, "chars_saved": 0, "upstream_errors": 0}  # type: ignore[attr-defined]
    print(f"TokenSaver Gateway listening on http://{host}:{port}")
    print("Health: /health | Stats: /stats | Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping TokenSaver Gateway...")
    finally:
        server.server_close()
