"""Conservative, deterministic text compression helpers. No LLM is called."""
from __future__ import annotations

import re
from dataclasses import dataclass

_BLANKS = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[\t ]+")


@dataclass(frozen=True)
class CompressionResult:
    original_chars: int
    compressed_chars: int
    original_estimated_tokens: int
    compressed_estimated_tokens: int
    text: str

    @property
    def chars_saved(self) -> int:
        return self.original_chars - self.compressed_chars

    @property
    def estimated_tokens_saved(self) -> int:
        return self.original_estimated_tokens - self.compressed_estimated_tokens

    @property
    def reduction_percent(self) -> float:
        if not self.original_chars:
            return 0.0
        return max(0.0, self.chars_saved / self.original_chars * 100.0)


def estimate_tokens(text: str) -> int:
    """Rough estimate only, not a provider-specific tokenizer."""
    return 0 if not text else max(1, (len(text) + 3) // 4)


def compress_text(text: str, *, deduplicate_lines: bool = True) -> CompressionResult:
    """Normalize whitespace and optionally remove exact duplicate nonblank lines.

    First-occurrence order is preserved. Unique lines are not paraphrased or
    deleted, making this safer than semantic compression but less aggressive.
    """
    original = text
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(_SPACES.sub(" ", line).strip() for line in normalized.split("\n"))
    normalized = _BLANKS.sub("\n\n", normalized).strip()

    if deduplicate_lines:
        seen: set[str] = set()
        output: list[str] = []
        for line in normalized.split("\n"):
            key = line.strip()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            output.append(line)
        normalized = "\n".join(output).strip()

    return CompressionResult(
        original_chars=len(original),
        compressed_chars=len(normalized),
        original_estimated_tokens=estimate_tokens(original),
        compressed_estimated_tokens=estimate_tokens(normalized),
        text=normalized,
    )
