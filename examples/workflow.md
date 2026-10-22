# Rolling memory workflow

TokenSaver stores project facts locally and renders a compact handoff for the next request. This helps only if you send the compact prompt instead of the full old transcript.

## Example

    python -m pip install -e .
    tokensaver memory-init .tokensaver-memory.json --project "Achaemenid strategy game"
    tokensaver memory-add .tokensaver-memory.json GOALS "Build a 3D strategy game set in the Achaemenid era"
    tokensaver memory-add .tokensaver-memory.json RULES "Keep existing tests passing"
    tokensaver memory-add .tokensaver-memory.json DECISIONS "Keep economy, army, diplomacy, and trade as separate systems"

For each new user message, prepare the model input with:

    tokensaver request .tokensaver-memory.json "Design trade between states"

The command prints the compact memory plus the new message, then records the new user message in the bounded recent-turn window. Send that printed prompt to your model if your client lets you control the request. After the model responds, store only a concise summary or durable decisions, for example:

    tokensaver memory-add .tokensaver-memory.json DECISIONS "Trade routes need security and tariffs"
    tokensaver memory-turn .tokensaver-memory.json assistant "Outlined route security, tariffs, and trade agreements"

## Important limitations

- Compression is deterministic and conservative. It normalizes whitespace and removes exact duplicate lines; it does not understand meaning or autonomously summarize a conversation.
- Token counts are rough estimates and vary by model, language, and tokenizer.
- This library does not intercept provider traffic, rewrite a provider's hidden history, or bypass usage limits. Integration must be implemented separately for each tool.
- Keep crucial constraints and decisions in structured memory. Keep a separate full transcript if you need exact historical details.
