# Prompt compression rules

## Preserve
- Desired outcome and deliverable format
- Hard constraints and exclusions
- Acceptance criteria
- Relevant names, versions, numbers, paths, and identifiers
- Exact error text and minimal reproduction steps
- Decisions already made and approaches already rejected

## Remove or shorten
- Greetings and filler
- Repeated goals or constraints
- History that does not affect the current decision
- Examples that add no new requirement
- Raw tool output once its relevant facts are extracted

## Rewrite pattern
Instead of a long narrative, use:

**Goal:** [one sentence]  
**Context:** [only facts that affect the task]  
**Constraints:** [must/must not]  
**Done when:** [observable acceptance criteria]  
**Current blocker:** [if any]  
**Relevant files/data:** [paths or compact excerpts]

## Rules
- Do not change the meaning while shortening.
- Keep ambiguity visible; do not silently guess.
- Keep exact wording for errors, legal text, user-provided quotes, and API identifiers when precision matters.
- Prefer one strong example over many repetitive examples.
- When comparing versions, preserve the meaningful differences.
