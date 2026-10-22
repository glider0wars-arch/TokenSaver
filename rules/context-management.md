# Context management

## Long conversation summary
Keep only information needed to continue the task:
1. Goal and expected output
2. Stable user requirements
3. Decisions and rejected options
4. Current implementation state
5. Errors already encountered
6. Tests already run and their results
7. Exact next step and relevant file paths

## Before a new tool call
- Check whether the answer is already present in the current context.
- Avoid repeating searches or reading unchanged files.
- Prefer targeted queries and small file ranges.
- Ask tools for structured summaries when supported.
- Do not request full logs when the error and surrounding lines are enough.

## When context is near the limit
Create a handoff note using the template in `../SKILL.md`. Keep facts separate from guesses. Include unresolved questions instead of inventing answers.

## Never discard
- Security requirements or secret-handling constraints
- Breaking API/interface details
- Reproduction steps and exact error messages
- Acceptance criteria
- User decisions that materially affect the solution
