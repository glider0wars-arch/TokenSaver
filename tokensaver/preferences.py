"""Non-binding response preferences for token-aware workflows.

Preferences guide presentation, never override the user's request or omit necessary work.
"""
from __future__ import annotations

STYLES = ("adaptive", "concise", "detailed")

_GUIDANCE = {
    "adaptive": (
        "Adapt detail to task complexity: be concise for simple requests and thorough "
        "when reasoning, steps, examples, caveats, or verification are needed. Never omit "
        "necessary work or details just to save tokens."
    ),
    "concise": (
        "Prefer concise, direct answers while remaining complete. Preserve required steps, "
        "correctness, code, important caveats, and safety information. Honor any request "
        "for a detailed answer."
    ),
    "detailed": (
        "Prefer thorough, structured answers with relevant reasoning, steps, examples, "
        "and caveats when useful. Do not omit necessary context merely to save tokens."
    ),
}


def response_style_guidance(style: str = "adaptive") -> str:
    """Return a validated, non-binding style preference suitable for a prompt."""
    if not isinstance(style, str):
        raise ValueError("Response style must be one of: adaptive, concise, detailed.")
    normalized = style.strip().lower()
    if normalized not in _GUIDANCE:
        raise ValueError("Response style must be one of: adaptive, concise, detailed.")
    return _GUIDANCE[normalized]
