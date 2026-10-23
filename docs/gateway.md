# Toknix Gateway

Toknix Gateway is a local HTTP proxy. It receives an API request, replaces eligible older conversation turns with a compact extractive context note, and forwards the modified request to the configured provider. The latest messages and system/developer instructions are preserved. Recent tool-call cycles are kept together when detected.

It supports these request shapes:

- OpenAI-compatible `POST /v1/chat/completions`
- OpenAI-compatible `POST /v1/responses` when `input` is a list; recent function-call/output items are kept together
- Anthropic-compatible `POST /v1/messages`

Other routes are passed through without prompt compaction. Streaming responses are relayed as chunks. No external summarizer model is called, so compaction itself does not consume additional model tokens.

## Run locally

Python 3.10+ is required. From the repository root:

    python -m pip install -e .
    TOKNIX_UPSTREAM_BASE_URL=https://api.openai.com python -m toknix.cli serve

The gateway listens on `127.0.0.1:8765` by default. Check it with:

    curl http://127.0.0.1:8765/health
    curl http://127.0.0.1:8765/stats

Set the upstream origin for one gateway instance:

- OpenAI API: `TOKNIX_UPSTREAM_BASE_URL=https://api.openai.com`
- xAI/Grok API: `TOKNIX_UPSTREAM_BASE_URL=https://api.x.ai`
- Anthropic API: `TOKNIX_UPSTREAM_BASE_URL=https://api.anthropic.com`

If your client already sends a valid provider API key, the proxy forwards its authorization headers. Alternatively set `TOKNIX_UPSTREAM_API_KEY` in the server process environment to override the upstream key. Never commit real API keys.

### OpenAI-compatible Python client example

    from openai import OpenAI

    client = OpenAI(
        api_key="your-provider-api-key",
        base_url="http://127.0.0.1:8765/v1",
    )
    response = client.chat.completions.create(
        model="your-provider-model",
        messages=[
            {"role": "system", "content": "Keep the user's requirements intact."},
            {"role": "user", "content": "Continue the project."},
        ],
    )
    print(response.choices[0].message.content)

For Grok, the OpenAI-compatible client can use the local base URL while Toknix's upstream is set to `https://api.x.ai`. The upstream API key and API billing still apply.

### Claude Code / Anthropic API

Start Toknix with `TOKNIX_UPSTREAM_BASE_URL=https://api.anthropic.com`. In the Claude Code shell, configure the supported custom API base URL for your installed version to `http://127.0.0.1:8765`, then launch Claude Code. Use an Anthropic API credential for API-based routing. Claude Code authentication modes and custom-base-URL behavior vary by version, so verify the current Anthropic documentation before changing your normal setup.

**Subscription caveat:** routing through the Anthropic API uses API billing; it does not turn a Claude Pro/Max subscription into API credits. Likewise, this gateway does not intercept hidden traffic from the Claude, ChatGPT, or Grok websites/apps.

### Codex

The gateway can compact simple Responses API message lists. If your Codex version supports a custom OpenAI-compatible Responses API base URL, point it at `http://127.0.0.1:8765/v1` and configure Toknix's upstream to OpenAI. Test tool-heavy requests carefully before relying on it. The proxy intentionally avoids rewriting unsupported payload shapes.

Codex authentication and supported provider configuration depend on the product/version. API-key routing uses API billing and is distinct from subscription-managed traffic.

## Compaction behavior

- System/developer instructions are retained.
- The most recent messages are preserved, configurable with `TOKNIX_KEEP_RECENT_MESSAGES` (default 8). The cut is bucketed by `TOKNIX_CUT_STEP` (default 4) to keep the compacted prefix stable across consecutive turns within the same bucket.
- Compaction only runs when the full request exceeds `TOKNIX_MIN_TOTAL_CHARS` (default 5000) and the older portion exceeds `TOKNIX_MIN_OLD_CHARS` (default 1200).
- History compaction is skipped when cache-control markers appear in the static system/instructions or the candidate old-history prefix, avoiding edits that could invalidate cache reuse. Tool-output compaction is handled separately: output at or before a cache breakpoint is preserved, while eligible older tool output after that breakpoint may be compacted. The latest tool cycle remains protected.
- Old history is converted into a deterministic extractive note that favors errors, decisions, constraints, file paths, TODOs, and important lines.
- If the history is short or the compacted request would not be smaller, the original request is forwarded.
- Tool outputs in the retained recent window are not rewritten. Older tool history may be summarized.
- `/stats` distinguishes proxy-side transformation counters from provider-reported usage. Character-based token estimates are rough estimates, not provider tokenizer counts. Legacy `TOKENSAVER_*` environment variables remain accepted when the corresponding `TOKNIX_*` variable is unset.

This is intentionally safer and less aggressive than learned token-pruning methods such as LLMLingua. Extractive compaction can still lose nuance; test against your actual tasks and lower compression settings if quality suffers. The automated HTTP tests cover SSE body passthrough and upstream error responses, but they do not prove compatibility with every provider or every streaming event shape. If an upstream connection fails before response headers, Toknix returns a 502; if it fails after streaming has begun, the response has already started and the proxy can only close the connection. For a repeatable paired test of real tasks, cost, latency, and answer quality, see [the validation protocol](validation.md). For stronger semantic memory, use a dedicated summarizer and account for the additional model call/cost.

## Security

- The server binds to loopback by default.
- Remote binding is refused unless `TOKNIX_ALLOW_REMOTE=1`; do not enable it on a public interface without an authenticated TLS reverse proxy.
- Do not log request bodies or API keys. Access logging is off by default.
- Every environment setting is validated at startup with a clear error. Remote mode requires `TOKNIX_GATEWAY_TOKEN` and authenticates every endpoint, including `/health` and `/stats`, using the `X-Toknix-Token` request header. The old `X-TokenSaver-Token` header is still accepted for existing clients.
- Use only trusted local clients. Other processes running as your user may be able to access the local port.
- The proxy forwards requests to the configured upstream. Choose the upstream deliberately and avoid sending secrets you would not send directly to that provider.
