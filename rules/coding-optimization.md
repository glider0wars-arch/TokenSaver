# Token-aware coding workflow

1. **Orient:** inspect the repository tree and the relevant entry points only.
2. **Locate:** search for the exact function, symbol, error, or configuration key.
3. **Narrow:** read the smallest useful code range and immediate dependencies.
4. **Plan:** state the intended change in one or two sentences when the change is nontrivial.
5. **Edit:** make the smallest coherent patch; preserve public interfaces unless change is required.
6. **Verify:** run focused tests first, then broader checks if useful.
7. **Summarize:** list files changed, checks run, results, and known limitations.

## Avoid
- Printing entire files by default
- Repeatedly dumping the same logs
- Broad refactors unrelated to the requested outcome
- Adding packages for a task that can use existing tools
- Claiming tests passed when they were not run
- Rewriting code solely to make it shorter if clarity or correctness suffers
