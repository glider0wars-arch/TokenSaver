"""Command-line interface for TokenSaver."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .compressor import compress_text
from .memory import CATEGORIES, RollingMemory, build_request


def _read_input(path: str | None) -> str:
    if path and path != "-":
        return Path(path).expanduser().read_text(encoding="utf-8")
    return sys.stdin.read()


def main() -> int:
    parser = argparse.ArgumentParser(prog="tokensaver", description="Local prompt compression and rolling memory.")
    sub = parser.add_subparsers(dest="command", required=True)

    cp = sub.add_parser("compress", help="Normalize whitespace and remove exact duplicate lines")
    cp.add_argument("file", nargs="?", help="Input file; omit or use - for stdin")
    cp.add_argument("--keep-duplicates", action="store_true")
    cp.add_argument("--stats", action="store_true", help="Print rough token estimates to stderr")

    init = sub.add_parser("memory-init", help="Create an empty rolling-memory JSON file")
    init.add_argument("path")
    init.add_argument("--project", default="")

    add = sub.add_parser("memory-add", help="Store a durable fact or decision")
    add.add_argument("path")
    add.add_argument("category", choices=CATEGORIES)
    add.add_argument("text")

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

    args = parser.parse_args()
    if args.command == "serve":
        from .proxy import serve as serve_proxy
        serve_proxy(args.host, args.port)
        return 0
    if args.command == "compress":
        result = compress_text(_read_input(args.file), deduplicate_lines=not args.keep_duplicates)
        if args.stats:
            print(
                f"chars: {result.original_chars} -> {result.compressed_chars}; "
                f"rough tokens: {result.original_estimated_tokens} -> {result.compressed_estimated_tokens}; "
                f"rough saved: {result.estimated_tokens_saved} ({result.reduction_percent:.1f}% chars)",
                file=sys.stderr,
            )
        print(result.text)
        return 0
    if args.command == "memory-init":
        RollingMemory(project=args.project).save(args.path)
        print(f"Created rolling memory: {Path(args.path).expanduser()}")
        return 0
    if args.command == "memory-add":
        memory = RollingMemory.load(args.path)
        added = memory.add(args.category, args.text)
        memory.save(args.path)
        print("Added." if added else "Skipped empty or duplicate fact.")
        return 0
    if args.command == "memory-turn":
        memory = RollingMemory.load(args.path)
        memory.add_turn(args.role, args.text)
        memory.save(args.path)
        print("Recent turn stored.")
        return 0
    if args.command == "request":
        memory = RollingMemory.load(args.path)
        # Render first so the new message is not duplicated in this same prompt.
        prompt = build_request(memory, args.message)
        memory.add_turn("user", args.message)
        memory.save(args.path)
        print(prompt)
        return 0
    if args.command == "memory-render":
        print(RollingMemory.load(args.path).render(include_recent=not args.no_recent))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
