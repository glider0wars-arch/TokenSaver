"""Offline, cache-aware benchmark for TokenSaver gateway compaction.

Run with: python -m benchmarks.run
All token counts are estimates unless the optional live API mode returns provider usage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from tokensaver.compressor import estimate_tokens
from tokensaver.proxy import transform_payload

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _cache_aware_cost(total_tokens: int, stable_prefix_tokens: int, *, price: float,
                      cached_ratio: float, cacheable_fraction: float) -> float:
    cached = min(total_tokens, max(0, int(total_tokens * cacheable_fraction)))
    # Keep the stable prefix as an upper bound on cacheable input.
    cached = min(cached, stable_prefix_tokens)
    return (cached * price * cached_ratio) + ((total_tokens - cached) * price)


def _live_usage(payload: dict[str, Any], *, url: str, key: str) -> dict[str, int] | None:
    request_payload = dict(payload)
    model = os.getenv("TOKENSAVER_LIVE_MODEL")
    if model:
        request_payload["model"] = model
    data = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"live request failed ({type(exc).__name__}); no key was printed", file=sys.stderr)
        return None
    usage = result.get("usage", {}) if isinstance(result, dict) else {}
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", usage.get("input_tokens_details", {}).get("total") if isinstance(usage.get("input_tokens_details"), dict) else None))
    output = usage.get("completion_tokens", usage.get("output_tokens"))
    details = usage.get("prompt_tokens_details", usage.get("input_tokens_details", {}))
    cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    return {
        "input": int(prompt or 0),
        "output": int(output or 0),
        "cached_input": int(cached or 0),
    }


def run(*, cached_ratio: float = 0.1, input_price: float = 1.0,
        cacheable_fraction: float = 0.7, live: bool = False,
        minimum_retention: float = 95.0, keep_recent: int = 4,
        summary_max_chars: int = 2400) -> tuple[str, bool]:
    rows: list[dict[str, Any]] = []
    retention_failed = False
    live_url = os.getenv("TOKENSAVER_LIVE_API_URL", "").strip()
    live_key = os.getenv("TOKENSAVER_LIVE_API_KEY", "")
    if live and (not live_url or not live_key):
        print("Live mode skipped: set TOKENSAVER_LIVE_API_URL and TOKENSAVER_LIVE_API_KEY.", file=sys.stderr)
        live = False

    for fixture_path in sorted(FIXTURES.glob("*.json")):
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        payload = fixture["payload"]
        path = fixture.get("path", "/v1/chat/completions")
        before_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        compacted, stats = transform_payload(
            path, payload, keep_recent=keep_recent, max_summary_chars=summary_max_chars,
            min_old_chars=200, min_total_chars=500, cut_step=1,
        )
        after_text = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
        before_chars, after_chars = len(before_text), len(after_text)
        before_tokens, after_tokens = estimate_tokens(before_text), estimate_tokens(after_text)
        static_messages = [
            m for m in payload.get("messages", [])
            if isinstance(m, dict) and m.get("role") in {"system", "developer"}
        ]
        stable_prefix_tokens = estimate_tokens(json.dumps(static_messages, ensure_ascii=False))
        before_cost = _cache_aware_cost(
            before_tokens, stable_prefix_tokens, price=input_price,
            cached_ratio=cached_ratio, cacheable_fraction=cacheable_fraction,
        )
        # Compaction usually changes old-history prefixes, so conservatively assume
        # only explicit static instructions remain stable/cacheable afterward.
        after_cost = _cache_aware_cost(
            after_tokens, stable_prefix_tokens, price=input_price,
            cached_ratio=cached_ratio, cacheable_fraction=cacheable_fraction,
        )
        # Worst-case cache miss: every compacted input token is billed at the
        # uncached input rate because the rewritten prefix no longer matches.
        after_cost_cache_broken = after_tokens * input_price
        cache_break_risk = after_cost_cache_broken > before_cost
        needles = fixture.get("needles", [])
        retained = sum(1 for needle in needles if needle in after_text)
        retention = 100.0 if not needles else retained * 100.0 / len(needles)
        if retention < minimum_retention:
            retention_failed = True
        row: dict[str, Any] = {
            "fixture": fixture.get("name", fixture_path.stem),
            "before_chars": before_chars,
            "after_chars": after_chars,
            "before_tokens_est": before_tokens,
            "after_tokens_est": after_tokens,
            "saved_percent": ((before_tokens - after_tokens) / before_tokens * 100.0) if before_tokens else 0.0,
            "estimated_cost_before": before_cost,
            "estimated_cost_after": after_cost,
            "estimated_cost_after_cache_broken": after_cost_cache_broken,
            "cache_break_risk": cache_break_risk,
            "retention": f"{retention:.1f}%",
            "compressed": bool(stats.get("compressed")),
        }
        if live:
            before_usage = _live_usage(payload, url=live_url, key=live_key)
            after_usage = _live_usage(compacted, url=live_url, key=live_key)
            row["live_usage"] = f"before={before_usage}; after={after_usage}"
        rows.append(row)

    headers = ["Fixture", "Chars before/after", "Rough tokens before/after", "Token reduction", "Cost before/after (cache-aware)*", "After if cache breaks*", "Break-even risk", "Needle retention", "Compacted"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| {fixture} | {before_chars:,} / {after_chars:,} | {before_tokens_est:,} / {after_tokens_est:,} | {saved_percent:.1f}% | {estimated_cost_before:.6f} / {estimated_cost_after:.6f} | {estimated_cost_after_cache_broken:.6f} | {cache_break_risk} | {retention} | {compressed} |".format(**row))
    lines.extend([
        "",
        "* Cost values are illustrative input-cost units, not provider quotes. The estimate uses the configured input price and cached-input ratio; real caching depends on provider rules and exact prefix stability.",
        f"Assumptions: input price={input_price:g} units per million estimated tokens; cached-input price ratio={cached_ratio:g}; assumed cacheable fraction={cacheable_fraction:.0%}.",
        "",
    ])
    if live:
        lines.append("Live provider usage (provider-returned numbers):")
        for row in rows:
            lines.append(f"- {row['fixture']}: {row.get('live_usage', 'not available')}")
    return "\n".join(lines), retention_failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark TokenSaver compaction and retention.")
    parser.add_argument("--cached-input-price-ratio", type=float, default=float(os.getenv("TOKENSAVER_CACHED_INPUT_PRICE_RATIO", "0.1")))
    parser.add_argument("--input-price-per-million", type=float, default=float(os.getenv("TOKENSAVER_INPUT_PRICE_PER_MILLION", "1.0")))
    parser.add_argument("--cacheable-fraction", type=float, default=float(os.getenv("TOKENSAVER_ASSUMED_CACHEABLE_FRACTION", "0.7")))
    parser.add_argument("--minimum-retention", type=float, default=95.0)
    parser.add_argument("--keep-recent", type=int, default=4)
    parser.add_argument("--summary-max-chars", type=int, default=2400)
    parser.add_argument("--live", action="store_true", help="Use API credentials from environment variables; never saves keys")
    args = parser.parse_args()
    if not 0.0 <= args.cached_input_price_ratio <= 1.0:
        parser.error("--cached-input-price-ratio must be between 0 and 1")
    if args.input_price_per_million < 0:
        parser.error("--input-price-per-million must not be negative")
    if not 0.0 <= args.minimum_retention <= 100.0:
        parser.error("--minimum-retention must be between 0 and 100")
    if args.keep_recent < 1 or args.summary_max_chars < 100:
        parser.error("--keep-recent must be positive and --summary-max-chars must be at least 100")
    if not 0.0 <= args.cacheable_fraction <= 1.0:
        parser.error("--cacheable-fraction must be between 0 and 1")
    report, failed = run(
        cached_ratio=args.cached_input_price_ratio,
        input_price=args.input_price_per_million / 1_000_000,
        cacheable_fraction=args.cacheable_fraction,
        live=args.live,
        minimum_retention=args.minimum_retention,
        keep_recent=args.keep_recent,
        summary_max_chars=args.summary_max_chars,
    )
    print(report)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
