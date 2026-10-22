---
name: token-saver
description: Reduce unnecessary prompt, context, tool-output, and response tokens while preserving task requirements and correctness.
---

# TokenSaver

- Identify the goal, constraints, and acceptance criteria before acting.
- Remove repetition and irrelevant history, but preserve exact names, values, paths, errors, decisions, and negative constraints.
- For coding, inspect targeted files and symbols; avoid dumping whole files or rereading unchanged content.
- Make the smallest correct change and run focused tests when possible.
- Summarize tool output while retaining actionable errors, line numbers, and status codes.
- Keep responses concise and report changes, tests, and blockers.
- Measure token savings only when a tokenizer or provider usage metadata is available; otherwise call them unmeasured.
- Never invent test results or token counts, expose secrets, or bypass provider quotas and safeguards.
- For handoffs, preserve the goal, constraints, state, decisions, attempts, exact blocker, next action, and relevant files.
