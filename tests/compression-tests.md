# Compression validation tests

Use these as manual checks whenever prompt-compression rules change.

## Test 1: Preserve constraints
**Input:** "Update the login page. Do not change the API. Keep the existing styling. Fix the bug where submitting twice creates duplicate requests. Add a test."

**Expected compact brief:** Fix duplicate login submissions; preserve API and styling; add a regression test.

**Pass condition:** No constraint or required test is lost.

## Test 2: Preserve exact error
**Input:** "Build fails with TypeError: Cannot read properties of undefined (reading 'map') at src/list.tsx:42."

**Expected compact brief:** Build error: exact TypeError above, at `src/list.tsx:42`; inspect data initialization and guard/map usage.

**Pass condition:** Error text and location remain exact.

## Test 3: Do not invent measurement
**Input:** Compress a 900-token prompt, but no tokenizer or provider usage data is available.

**Expected result:** Provide a shorter prompt and say token savings were not measured.

**Pass condition:** No invented token count or percentage.

## Test 4: Context handoff
**Input:** Long debugging chat with two attempted fixes, one rejected approach, and one unresolved error.

**Expected result:** Goal, relevant state, attempts/results, rejected approach, exact error, next step, relevant paths.

**Pass condition:** A new agent can continue without repeating completed work.

## Test 5: Provider quota
**Input:** User asks to prevent all usage-limit errors.

**Expected result:** Explain that reducing unnecessary tokens may help but cannot guarantee quota avoidance or bypass provider limits.

**Pass condition:** No false guarantee or evasion advice.
