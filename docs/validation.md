# Evaluating Toknix on real tasks

Offline character/token estimates and exact-needle retention are regression signals, not evidence of real cost savings or unchanged answer quality. Use a paired evaluation before making performance claims.

## Minimal paired test

1. Select representative tasks from your own workload: debugging, code edits, long tool logs, multilingual context, and tool-heavy API calls.
2. Freeze each task's starting conversation, tool outputs, model, provider settings, and requested result. Do not compare different tasks or model settings.
3. Run each task once directly against the provider and once through Toknix. Alternate run order where practical to reduce timing or service-load bias.
4. Record the provider-reported usage for each run: input tokens, output tokens, cached input tokens, cache creation/write tokens, and any reasoning-token fields the provider reports. Record the actual model and price schedule used.
5. Calculate costs from the provider's current pricing rules. Do not count cached tokens as ordinary input tokens when the provider prices them differently. Keep estimates separate from invoice-confirmed costs.
6. Compare task outcomes using a rubric written before the runs: correctness, required details preserved, tests passing, constraints followed, and unsupported claims. Review failures manually.
7. Report the number of tasks, median and range of cost/usage changes, quality regressions, latency, and errors. Include cases where Toknix increased request size or cost.

## Cache-prefix regression and real-provider measurement

Before claiming that cache-aware traffic is cheaper, include a multi-turn session that has a cache breakpoint on the final request item (the Claude Code-style case that previously left little output eligible for compaction).

1. Run the same representative task directly against the provider and through Toknix, using the same model, settings, and starting transcript.
2. For the Toknix run, record `/stats` before and after the session. Inspect `cache_control_requests`, `cache_compacted_tool_outputs`, `cache_protected_tool_outputs`, `compressed_requests`, and the provider usage fields.
3. Confirm that the same tool output is byte-identical after transformation across consecutive turns. The automated regression test checks this property offline; the real-provider run checks actual cache-read/cache-creation usage and billing behavior.
4. Calculate the bill from the provider-reported usage categories and the current price schedule. Compare cache-read, cache-creation/write, and uncached input separately. A lower character count or lower estimated token count is not a cost result.
5. Compare final answers with a predefined quality rubric and record any omitted file path, constraint, decision, or required detail as a regression.

Use a provider API key only in your local environment or secret manager. Do not paste it into chat, fixtures, terminal recordings, issue reports, or committed files. A real-provider measurement has not been completed merely because the offline test suite passes.

## Safety and privacy

- Use synthetic or appropriately redacted requests when possible. Do not upload secrets, credentials, or private source code to a third-party evaluation service.
- Keep provider API keys out of fixtures, logs, screenshots, and commits.
- Never remove safety-critical instructions or required task constraints just to improve a token metric.
- Preserve an uncompressed transcript when an audit trail matters.
- If Toknix changes a result or removes a required detail, count that as a quality regression even if the request used fewer tokens.

## What a valid claim looks like

A defensible claim states the workload, sample size, provider/model, date or pricing basis, measurement source, and quality outcome. For example, report the measured change across a named set of test cases rather than claiming that Toknix always saves a fixed percentage.

Do not treat character counts, approximate tokenizer estimates, benchmark fixtures, or exact-needle retention as provider billing data. Results from one provider or workload should not be generalized to all models and tasks.
