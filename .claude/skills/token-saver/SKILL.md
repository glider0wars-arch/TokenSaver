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

## Rolling memory (when the TokenSaver CLI is installed)
- Keep durable goals, constraints, decisions, current state, relevant files, and open tasks in the local TokenSaver memory file.
- Before a new model request, use the `tokensaver request` command to render compact memory plus the new message; use that output instead of resending the entire old transcript when the client permits it.
- After a response, store only a short, useful summary or durable decision. Do not copy a long assistant response into memory.
- Keep exact error messages and essential constraints when they are needed for debugging.
- This workflow is not automatic provider interception. Never claim that hidden provider history has been replaced unless a real integration is configured.
