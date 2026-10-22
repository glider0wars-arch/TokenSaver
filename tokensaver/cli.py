"""Command-line interface for TokenSaver."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .compressor import compress_text
from .memory import CATEGORIES, RollingMemory, build_request, memory_file_lock


def _configure_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _read_input(path: str | None) -> str:
    if path and path != "-":
        return Path(path).expanduser().read_text(encoding="utf-8")
    return sys.stdin.read()


def main() -> int:
    _configure_utf8()
    parser = argparse.ArgumentParser(prog="tokensaver", description="Local prompt compression and rolling memory.")
    sub = parser.add_subparsers(dest="command", required=True)

    cp = sub.add_parser("compress", help="Normalize whitespace while preserving code formatting")
    cp.add_argument("file", nargs="?", help="Input file; omit or use - for stdin")
    cp.add_argument("--keep-duplicates", action="store_true")
    cp.add_argument("--stats", action="store_true", help="Print rough token estimates to stderr")

    init = sub.add_parser("memory-init", help="Create an empty rolling-memory JSON file")
    init.add_argument("path")
    init.add_argument("--project", default="")
    init.add_argument("--force", action="store_true", help="Replace an existing file (its backup is retained)")

    add = sub.add_parser("memory-add", help="Store a durable fact or decision")
    add.add_argument("path")
    add.add_argument("category", choices=CATEGORIES)
    add.add_argument("text")

    remove = sub.add_parser("memory-remove", help="Remove an exact stored fact")
    remove.add_argument("path")
    remove.add_argument("category", choices=CATEGORIES)
    remove.add_argument("text")

    clear = sub.add_parser("memory-clear", help="Clear all facts and recent turns")
    clear.add_argument("path")
    clear.add_argument("--yes", action="store_true", help="Confirm clearing memory")

    turn = sub.add_parser("memory-turn", help="Append one turn to the bounded recent window")
    turn.add_argument("path")
    turn.add_argument("role", choices=["user", "assistant", "tool", "system"])
    turn.add_argument("text")

    request = sub.add_parser("request", help="Build a prompt from compact memory and a new message")
    request.add_argument("path", help="Rolling-memory JSON file")
    request.add_argument("message", help="New user message")

    render = sub.add_parser("memory-render", help="Print compact memory to stdout")
    render.add_argument("path")
    render.add_argument("--no-recent", action="store_true")

    serve = sub.add_parser("serve", help="Start the local API compression gateway")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    report = sub.add_parser("report", help="Show measured provider usage and estimated context categories")
    report.add_argument("--url", default="http://127.0.0.1:8765/stats", help="Gateway stats endpoint")

    args = parser.parse_args()
    try:
        if args.command == "serve":
            from .proxy import serve as serve_proxy
            serve_proxy(args.host, args.port)
            return 0
        if args.command == "report":
            request = urllib.request.Request(args.url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as response:
                stats = json.loads(response.read().decode("utf-8"))
            print("TokenSaver usage report")
            print("Provider usage: MEASURED from upstream response usage objects")
            print("Context breakdown: ESTIMATED from request text; not provider tokenizer counts")
            print("")
            print("| Category | Characters | Rough tokens (estimated) |")
            print("| --- | ---: | ---: |")
            for label, key in (("System/developer", "system"), ("Old history", "old_history"),
                               ("Recent turns", "recent_turns"), ("Tool outputs", "tool_outputs")):
                print(f"| {label} | {int(stats.get(key + '_chars', 0)):,} | {int(stats.get('estimated_' + key + '_tokens', 0)):,} |")
            print("")
            print("| Provider metric (measured) | Tokens |")
            print("| --- | ---: |")
            print(f"| Input tokens | {int(stats.get('provider_input_tokens', 0)):,} |")
            print(f"| Output tokens | {int(stats.get('provider_output_tokens', 0)):,} |")
            print(f"| Cached input tokens | {int(stats.get('provider_cached_input_tokens', 0)):,} |")
            print("")
            print(f"Requests: {int(stats.get('requests', 0))}; compressed requests: {int(stats.get('compressed_requests', 0))}; transform errors: {int(stats.get('transform_errors', 0))}")
            print(f"Responses with parsed provider usage: {int(stats.get('provider_usage_responses', 0))}; parse/tee skips: {int(stats.get('provider_usage_parse_errors', 0))}")
            return 0
        if args.command == "compress":
            result = compress_text(_read_input(args.file), deduplicate_lines=not args.keep_duplicates)
            if args.stats:
                print(
                    f"chars: {result.original_chars} -> {result.compressed_chars}; "
                    f"rough token estimate: {result.original_estimated_tokens} -> {result.compressed_estimated_tokens}; "
                    f"rough estimated saved: {result.estimated_tokens_saved} ({result.reduction_percent:.1f}% chars)",
                    file=sys.stderr,
                )
            print(result.text)
            return 0
        if args.command == "memory-init":
            path = Path(args.path).expanduser()
            with memory_file_lock(path):
                if path.exists() and not args.force:
                    raise ValueError(f"{path} already exists. Use --force to replace it; a .bak copy will be retained.")
                RollingMemory(project=args.project).save(path)
            print(f"Created rolling memory: {path}")
            return 0
        if args.command == "memory-add":
            with memory_file_lock(args.path):
                memory = RollingMemory.load(args.path)
                added = memory.add(args.category, args.text)
                evicted = list(memory.last_evicted)
                memory.save(args.path)
            print("Added." if added else "Skipped empty or duplicate fact.")
            if evicted:
                print("Evicted to fit memory limits: " + ", ".join(evicted))
            return 0
        if args.command == "memory-remove":
            with memory_file_lock(args.path):
                memory = RollingMemory.load(args.path)
                removed = memory.remove(args.category, args.text)
                if removed:
                    memory.save(args.path)
            if removed:
                print("Removed.")
            else:
                print("No exact matching fact was found.")
            return 0
        if args.command == "memory-clear":
            if not args.yes:
                raise ValueError("Clearing memory requires --yes.")
            with memory_file_lock(args.path):
                memory = RollingMemory.load(args.path)
                memory.clear()
                memory.save(args.path)
            print("Memory cleared; project name and memory limits were preserved.")
            return 0
        if args.command == "memory-turn":
            with memory_file_lock(args.path):
                memory = RollingMemory.load(args.path)
                memory.add_turn(args.role, args.text)
                evicted = list(memory.last_evicted)
                memory.save(args.path)
            print("Recent turn stored.")
            if evicted:
                print("Evicted to fit memory limits: " + ", ".join(evicted))
            return 0
        if args.command == "request":
            with memory_file_lock(args.path):
                memory = RollingMemory.load(args.path)
                prompt = build_request(memory, args.message)
                # Always emit the prompt, even if persisting the new turn later fails.
                print(prompt)
                memory.add_turn("user", args.message)
                evicted = list(memory.last_evicted)
                memory.save(args.path)
            if evicted:
                print("Evicted to fit memory limits: " + ", ".join(evicted), file=sys.stderr)
            return 0
        if args.command == "memory-render":
            with memory_file_lock(args.path):
                memory = RollingMemory.load(args.path)
                rendered = memory.render(include_recent=not args.no_recent)
            print(rendered)
            return 0
        return 2
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"TokenSaver error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
