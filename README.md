# TokenSaver AI

**Spend context carefully. Keep the important bits.**

TokenSaver is a small, provider-neutral prompt and context optimization kit for Claude Code, OpenAI Codex, and Grok. It helps reduce unnecessary input and output tokens by keeping requests focused, summarizing old context, inspecting only relevant files, and asking for concise results.

> TokenSaver does not bypass provider quotas, rate limits, or usage windows. Compression can help, but it cannot guarantee that a limit will not be reached.

## What is included

- `SKILL.md`: reusable instructions for an AI coding agent.
- `rules/context-management.md`: compact, reliable context handoffs.
- `rules/prompt-compression.md`: remove repetition without losing requirements.
- `rules/coding-optimization.md`: token-aware coding workflow.
- `providers/`: setup notes and prompt snippets for Claude Code, Codex, and Grok.
- `tests/compression-tests.md`: checks to ensure compression preserves meaning.

## Quick start

1. Open `SKILL.md` and copy its instructions into the AI tool you use, or install the Claude Code version from `.claude/skills/token-saver/SKILL.md`.
2. For Codex, use `AGENTS.md` as project guidance or paste the core rules into your task instructions.
3. For Grok, use `providers/grok.md` to copy the compact system prompt.
4. Give the agent a task and ask it to return only the required output, with assumptions and blockers called out.

## Core rules

1. Preserve the user's goal, constraints, exact error messages, relevant paths, and decisions.
2. Remove repetition and background that does not affect the task.
3. Inspect targeted files before requesting or producing large dumps.
4. Summarize tool output; retain exact details when needed for debugging.
5. Prefer a small, verifiable change over a broad rewrite.
6. Never claim a token-saving percentage unless it was measured.
7. Do not sacrifice correctness or omit safety-critical information to save tokens.

## Rolling compressed memory (new in v0.2)

The Python package now includes a local rolling-memory store and a request builder. It keeps durable facts in sections such as goals, rules, decisions, current state, files, and open tasks, plus a bounded window of recent turns. Before a new request, TokenSaver renders that compact memory together with the new message. It then records the user message for future context.

Install locally (Python 3.10+):

    python -m pip install -e .

Create a memory file and store important project facts:

    tokensaver memory-init .tokensaver-memory.json --project "My project"
    tokensaver memory-add .tokensaver-memory.json GOALS "Build the requested app"
    tokensaver memory-add .tokensaver-memory.json RULES "Keep existing tests passing"

Prepare a compact prompt for the next model request:

    tokensaver request .tokensaver-memory.json "Add a settings page"

The command prints the compact memory plus the new message, and saves the new user turn to the rolling window. Send the printed prompt to the model instead of resending the full old transcript when your workflow lets you control request construction. After the model responds, save only a short, useful summary or durable decision with memory-add or memory-turn, not a huge raw answer.

For a full walkthrough, see `examples/workflow.md`.

**Important:** this is a working local memory utility, not yet an automatic plugin or network proxy for Claude Code, Codex, or Grok. It cannot replace hidden provider-managed history unless integrated with that provider's request pipeline. Compression is conservative and deterministic; it does not autonomously understand or summarize meaning. Keep critical constraints and decisions explicitly stored, and keep a separate transcript if you need a full audit trail.

## Local API Gateway (v0.3)

TokenSaver now includes a local proxy that compacts older conversation history **before** forwarding supported API requests. It supports OpenAI-compatible `/v1/chat/completions`, simple/list-based `/v1/responses`, and Anthropic `/v1/messages`. It preserves system/developer instructions, keeps recent turns, favors important lines when building the old-context note, and passes through unrelated request shapes.

Run it with Python 3.10+:

    python -m pip install -e .
    TOKENSAVER_UPSTREAM_BASE_URL=https://api.openai.com python -m tokensaver.cli serve

By default, it listens only on `127.0.0.1:8765`. Check `http://127.0.0.1:8765/health` and `/stats`. Set the upstream origin to `https://api.x.ai` for Grok API or `https://api.anthropic.com` for Anthropic API. Compaction is controlled by `TOKENSAVER_MIN_TOTAL_CHARS` (default 5000) and `TOKENSAVER_CUT_STEP` (default 4); cache-control prefixes are passed through unchanged. Configuration and integration examples are in `docs/gateway.md` and `.env.example`. Design trade-offs from related projects are documented in `docs/research-notes.md`.

The gateway does not call a second model to summarize old messages, so it adds no extra model call for compression. It recomputes the extractive note from the transcript supplied by the client on each request; the separate CLI memory file is available for durable project facts. Its extractive compaction is conservative, but can still lose nuance; check the results with your own tasks. Token counts are rough estimates, not provider-reported measurements.

**Billing and access:** the gateway only affects requests routed through it. API-key traffic is billed by the upstream API provider. It does not intercept hidden traffic from the Claude, ChatGPT, Grok websites/apps, and it does not convert subscription usage into API credits or bypass quotas. The gateway binds locally by default; remote mode requires `TOKENSAVER_GATEWAY_TOKEN` on every endpoint and should only be used behind authenticated TLS. Configuration is validated at startup.

## Measuring results

Token counts differ by provider and tokenizer. Compare actual usage metadata when available. Otherwise, report that savings are estimated rather than measured. Input, output, cached, and reasoning tokens may be counted differently by each provider.

## License

No license has been added yet. Until one is added, normal copyright restrictions apply.


## Results

The following table is the output of `python -m benchmarks.run` on code revision `fc24df5f7b1587ac96b760764bd845480392b358`. It replays the three checked-in fixtures. Counts are estimates, and cost values are illustrative input-cost units, not provider quotes.

| Fixture | Chars before/after | Rough tokens before/after | Token reduction | Cost before/after (cache-aware)* | After if cache breaks* | Break-even risk | Needle retention |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| coding-agent-long-tool-logs | 6,005 / 1,673 | 1,502 / 419 | 72.1% | 0.000556 / 0.000392 | 0.000419 | False | 100.0% |
| long-chat | 18,913 / 4,152 | 4,729 / 1,038 | 78.1% | 0.001750 / 0.001010 | 0.001038 | False | 100.0% |
| mixed-persian-english | 10,252 / 3,801 | 2,896 / 1,118 | 61.4% | 0.001072 / 0.001080 | 0.001118 | True | 100.0% |

* Benchmark assumptions: input price 1 illustrative unit per million estimated tokens, cached-input price ratio 0.1, and 70% assumed cacheability before compaction. The "After if cache breaks" column is a worst-case estimate that bills the compacted input at the uncached rate. Real provider cache behavior and pricing can differ. The benchmark fails if needle retention drops below 95%.

Run `python -m benchmarks.run` to regenerate the Markdown table for the current revision. Token counts are estimates unless optional live mode reports provider-returned usage. The retention check is a useful regression signal, not proof that answer quality is unchanged.

## When this does NOT help

- **Subscription apps:** TokenSaver's local API gateway only sees requests routed through it. It cannot compact hidden conversation history inside the official ChatGPT or Claude apps.
- **Tools with built-in compaction:** Claude Code and other agent tools may already summarize old context. An extra compaction layer can duplicate work or remove useful detail.
- **Cache trade-offs:** Changing an old prompt prefix may reduce cache hits. Fewer input tokens do not guarantee a lower bill; compare the benchmark's cache-aware estimate with provider-reported usage and actual pricing.
- **Quality-sensitive work:** Extractive compaction can omit facts that are not recognized as important. Keep a transcript for auditability and validate answer quality on representative tasks.
- **Already-small requests:** The gateway may leave short or cache-marked requests unchanged, and compression can increase size in some cases.
