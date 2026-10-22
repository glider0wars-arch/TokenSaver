"""Conservative text compression helpers; token counts are rough heuristics."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_FENCE = re.compile(r"^\s*(\x60{3,}|~{3,})")
_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


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
        return max(0.0, self.chars_saved / self.original_chars * 100.0) if self.original_chars else 0.0


def estimate_tokens(text: str) -> int:
    """Return a rough script-aware estimate, not a provider tokenizer count.

    Latin-heavy text is approximated at ~4 characters/token. Persian/Arabic
    script uses a more conservative ~1.8 characters/token, and CJK characters
    count approximately one token each. Mixed text is estimated per character.
    """
    if not text:
        return 0
    weight = 0.0
    for char in text:
        cp = ord(char)
        if char.isspace():
            weight += 0.25
        elif (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
              0x3040 <= cp <= 0x30FF or 0xAC00 <= cp <= 0xD7AF):
            weight += 1.0
        elif (0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or
              0x08A0 <= cp <= 0x08FF or 0xFB50 <= cp <= 0xFDFF or
              0xFE70 <= cp <= 0xFEFF):
            weight += 0.56
        elif cp < 128:
            weight += 0.25
        else:
            weight += 0.5
    return max(1, math.ceil(weight))


def _looks_like_code(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(re.match(
        r"^(?:[#@]|//|/\*|\*|\}|\]|\)|(?:async\s+)?(?:def|class|return|yield|import|from|if|elif|else|for|while|try|except|finally|with|const|let|var|function|export|public|private|SELECT|INSERT|UPDATE|DELETE)\b)|"
        r"(?:=>|->|::|:=|==|!=|<=|>=|&&|\|\||[{}\[\]();=<>])",
        stripped,
        re.IGNORECASE,
    ))


def compress_text(text: str, *, deduplicate_lines: bool = True) -> CompressionResult:
    """Conservatively normalize prose while preserving code-like formatting.

    Only adjacent, identical, non-indented prose lines are deduplicated. Indented
    and code-looking lines are left byte-for-byte unchanged outside line endings.
    Fenced code and blank-line structure inside fences are preserved.
    """
    original = text
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in normalized.split("\n"):
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if not in_fence:
                in_fence, fence_char, fence_len = True, marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            continue
        if not line.strip():
            if output and output[-1] != "":
                output.append("")
            continue
        indented = line.startswith((" ", "\t"))
        code_like = _looks_like_code(line)
        if not indented and not code_like:
            line = _SPACES.sub(" ", line.rstrip())
        if deduplicate_lines and not indented and not code_like and output and output[-1] == line:
            continue
        output.append(line)
    if not in_fence:
        while output and output[-1] == "":
            output.pop()
    result_text = "\n".join(output)
    return CompressionResult(
        original_chars=len(original),
        compressed_chars=len(result_text),
        original_estimated_tokens=estimate_tokens(original),
        compressed_estimated_tokens=estimate_tokens(result_text),
        text=result_text,
    )
