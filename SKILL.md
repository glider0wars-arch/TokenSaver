---
name: token-saver
description: Reduce unnecessary prompt, context, tool-output, and response tokens while preserving task requirements and correctness. Use for coding tasks, long conversations, debugging, and multi-step agent workflows.
---

# TokenSaver AI Skill

## Goal
Complete the user's task with the smallest useful amount of context and output, without losing requirements, correctness, or important uncertainty.

## Before acting
1. Identify the requested outcome, hard constraints, acceptance criteria, and current blocker.
2. Reuse known decisions instead of asking for them again.
3. Separate facts from assumptions. Ask a question only when a missing detail materially changes the result.
4. Do not restate the entire conversation. Build a compact task brief.

## Prompt and context compression
- Remove greetings, repeated explanations, filler, and redundant examples.
- Preserve exact names, values, file paths, API signatures, error messages, constraints, and user decisions.
- Replace long history with: goal; decisions; constraints; relevant state; attempted steps; current error; next action.
- Keep important negative constraints (what must not change).
- Never compress away safety requirements or details needed to reproduce a bug.

## Coding workflow
1. Inspect only files and symbols relevant to the task first.
2. Search for definitions and call sites before opening large files.
3. Request or print focused ranges instead of entire files.
4. Make the smallest coherent change that satisfies the acceptance criteria.
5. Reuse existing patterns and dependencies; do not add dependencies without a reason.
6. Run focused tests, lint, or type checks where available.
7. Report changed files, tests run, outcomes, and unresolved issues briefly.
8. Do not repeatedly re-read unchanged files or repeat failed approaches without new evidence.

## Tool output
- Extract the findings needed for the next decision.
- Preserve exact error text, relevant line numbers, status codes, and identifiers.
- For large logs, report the first actionable error and the smallest surrounding context.
- Do not paste raw tool output when a short factual summary is sufficient.
- Do not omit warnings that change the recommended action.

## Response style
- Put the answer or result first.
- Use concise bullets for changes, evidence, and next steps.
- Avoid repeating the prompt or explaining obvious steps.
- Expand only when the user asks, the task is complex, or correctness requires detail.
- If output format is specified, follow it exactly.

## Token awareness
- If provider usage metadata or a tokenizer is available, measure before/after counts.
- Otherwise, describe savings as qualitative or estimated, never as a measured percentage.
- Distinguish input, output, cached, and reasoning tokens when the provider exposes them.
- Provider quotas may depend on time windows, model, plan, workload, and service policy. TokenSaver cannot guarantee quota avoidance.

## Safety and integrity
- Do not bypass quotas, rate limits, authentication, or provider safeguards.
- Do not delete important project context just to make a prompt shorter.
- Do not invent test results, token counts, or successful tool actions.
- Keep credentials, tokens, and secrets out of prompts, logs, and repository files.

## Compact handoff format
When a task must continue in a new chat or agent session, use:

**Goal:** …
**Constraints:** …
**Current state:** …
**Decisions:** …
**Tried:** …
**Blocker/error:** …
**Next action:** …
**Relevant files:** …
