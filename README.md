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

## Benchmarks, tool output, and usage reporting

Run the offline benchmark with Python 3.10+ and no extra dependencies:

    python -m benchmarks.run

The benchmark replays fixtures for a coding agent with long tool logs, a long conversation, and mixed Persian/English messages. It reports characters, rough token estimates, estimated input-cost scenarios, and retention of exact “needle” facts. The command exits non-zero if retention falls below 95%. These figures are estimates, not provider billing measurements.

The cache-aware table shows a baseline estimate, a compaction estimate with a stable cacheable prefix, and a worst-case scenario where compaction breaks the cache. Configure the cached-input price ratio (default 0.1), assumed cacheable fraction, input price, and retention threshold with CLI options. Actual cache behavior depends on provider-specific rules and exact prefix stability.

Optional live usage checks can send fixture requests to an OpenAI-compatible endpoint. They are opt-in and may incur charges:

    TOKENSAVER_LIVE_API_URL=https://api.openai.com/v1/chat/completions
    TOKENSAVER_LIVE_API_KEY=... python -m benchmarks.run --live

Keys are read only from the environment and are never written by the benchmark. Do not use a production key for tests.

The gateway compresses oversized historical tool outputs conservatively. It strips ANSI control codes, summarizes repeated lines, retains head/tail context and important error/constraint lines, and can refer to identical earlier outputs. The newest tool cycle is protected by default. Set `TOKENSAVER_TOOL_OUTPUT_MAX_CHARS` to change the threshold; set `TOKENSAVER_COMPRESS_LATEST_TOOL_CYCLE=1` only if you explicitly want the newest cycle included.

Provider-reported usage is accumulated in `/stats` when upstream JSON or SSE responses include usage metadata and the bounded tee buffer can parse the response. Set `TOKENSAVER_USAGE_TEE_MAX_BYTES` to adjust the default 1 MiB measurement cap. If the body exceeds the cap, usage is skipped rather than buffering the response or delaying passthrough.

Print a readable measured-vs-estimated report with:

    tokensaver report

Provider token totals are measured values from upstream usage objects. Context-category breakdowns are estimated character counts, and tool output is a subset of history rather than a separate additive bucket.

## When this does NOT help

- It does not intercept traffic from the ChatGPT, Claude, or Grok subscription apps. Only requests explicitly routed through the gateway are affected.
- It adds little value when a client or coding agent already compacts the same context well.
- Extractive compaction can lose nuance. A high needle-retention score is not proof that answer quality is unchanged.
- Short requests or histories that do not cross the configured thresholds may remain unchanged.
- Rewriting an old prompt prefix can reduce cache hits. Fewer estimated tokens can still cost more if the cache benefit lost is larger than the input reduction.
- The token estimator is heuristic. Provider-reported usage is preferred whenever available.

## Results

Run `python -m benchmarks.run` to generate the Markdown table from the recorded fixtures. Only add numeric results here after the benchmark has completed successfully; never substitute unmeasured savings claims.

## License

No license has been added yet. Until one is added, normal copyright restrictions apply.
