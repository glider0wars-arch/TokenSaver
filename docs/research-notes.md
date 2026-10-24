# Research notes: patterns used in Toknix

This note records the design patterns that informed the gateway. The projects below solve related but different problems; Toknix borrows ideas rather than claiming equivalent compression quality.

## 1. LLMLingua: learned prompt compression

Project: https://github.com/microsoft/LLMLingua

LLMLingua uses a small aligned language model to estimate which prompt tokens are less important, and supports different compression rates for instruction, context, and question segments. Its research reports high compression ratios, but the trade-off between compression and preserving meaning matters, and a learned compressor requires extra model weights/runtime.

**Toknix choice:** the default gateway uses deterministic extractive compaction. It has no model download and makes no extra model call, which keeps setup lightweight and avoids adding a second inference bill. The trade-off is that it is less aggressive and may lose nuance. A learned compressor can be an optional backend later, behind tests and an explicit opt-in.

## 2. MemGPT / Letta: hierarchical memory

Project: https://github.com/letta-ai/letta

MemGPT's design treats context as a limited working area and uses separate storage tiers for persistent facts and older recallable material. The useful pattern is not to resend everything forever: keep a small active context and retrieve or summarize older material when needed.

**Toknix choice:** the local memory utility separates goals, rules, decisions, current state, files, open tasks, and recent turns. The gateway keeps system/developer instructions and recent turns while replacing older conversational turns with a compact context note. The current gateway does not implement vector search or a full autonomous memory agent.

## 3. Claude Code hooks: lifecycle integration

Official guide: https://code.claude.com/docs/en/hooks

Hooks run at defined lifecycle points and are useful for loading project context, preserving information before compaction, or running deterministic checks. However, a hook is not the same as replacing the request payload that a provider receives.

**Toknix choice:** a local API gateway is used where the client supports a configurable API base URL, because it can transform the request body before forwarding it. A Claude Code skill alone changes instructions, not the provider's hidden history. Authentication and custom base-URL support vary by tool/version.

## 4. Stable prompts and provider caching

Provider prompt caching can reduce repeated processing for identical prefixes, depending on provider and request shape. Toknix preserves system/developer instructions and avoids rewriting a cache-marked prefix during history compaction. Tool-output compression is separate: output at/before the last cache breakpoint remains unchanged, while eligible older outputs after that boundary may be compacted. These transformations do not prove provider-side cache hits or lower billed cost.

## Evaluation principles

- Measure actual provider usage metadata when available; character-based estimates are not proof of token savings.
- Test response quality and tool-call correctness, not only compression ratio.
- Preserve recent tool-call/output sequences to avoid malformed requests.
- If a summary is not smaller than the original context, forward the original history.
- Do not expose the local gateway publicly without authentication and TLS.
- API proxying uses provider API credentials and billing; it does not convert subscription-based app usage into API credits.
