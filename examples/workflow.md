# Rolling memory workflow

TokenSaver stores project facts locally and renders a compact handoff for the next request. It does not automatically intercept a model provider's private chat history. Copy the rendered memory into the next prompt or integrate the library into a client that controls request construction.

## Example

    python -m pip install -e .
    tokensaver memory-init .tokensaver-memory.json --project "Achaemenid strategy game"
    tokensaver memory-add .tokensaver-memory.json GOALS "Build a 3D strategy game set in the Achaemenid era"
    tokensaver memory-add .tokensaver-memory.json DECISIONS "Keep economy, army, diplomacy, and trade as separate systems"
    tokensaver memory-turn .tokensaver-memory.json user "Add international trade"
    tokensaver memory-render .tokensaver-memory.json

After each turn, add durable decisions, current status, constraints, and open tasks. Use memory-turn for a short rolling window, not as a replacement for important structured facts.
