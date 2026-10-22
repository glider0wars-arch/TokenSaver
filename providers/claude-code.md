# Claude Code

## Suggested setup
Use the skill at `.claude/skills/token-saver/SKILL.md`. Claude Code skill discovery and project settings can vary by version, so verify the path against the installed version's documentation.

## Compact instruction
"Use TokenSaver rules: inspect only relevant files, preserve requirements and exact errors, avoid repeated reads and large dumps, make the smallest correct change, run focused checks, and summarize changed files/tests/blockers. Never claim token savings unless measured."

## Notes
- A skill can improve prompt/context efficiency but cannot change Anthropic's quota policy.
- Keep project-specific instructions in the repository; keep task-specific context short.
- Do not include API keys, private logs, or credentials in the skill.
