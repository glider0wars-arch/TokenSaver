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

## Measuring results

Token counts differ by provider and tokenizer. Compare actual usage metadata when available. Otherwise, report that savings are estimated rather than measured. Input, output, cached, and reasoning tokens may be counted differently by each provider.

## License

No license has been added yet. Until one is added, normal copyright restrictions apply.
