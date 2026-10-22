"""Persistent rolling memory for long AI-assisted workflows."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compressor import compress_text

CATEGORIES = ("GOALS", "RULES", "DECISIONS", "CURRENT_STATE", "FILES", "OPEN_TASKS", "RECENT_CONTEXT")


@dataclass
class RollingMemory:
    project: str = ""
    sections: dict[str, list[str]] = field(default_factory=lambda: {name: [] for name in CATEGORIES})
    recent_turns: list[dict[str, str]] = field(default_factory=list)
    max_recent_turns: int = 6

    def add(self, category: str, item: str) -> bool:
        category = category.upper().strip()
        item = " ".join(item.split()).strip()
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category {category!r}. Choose from: {', '.join(CATEGORIES)}")
        if not item:
            return False
        bucket = self.sections.setdefault(category, [])
        if item in bucket:
            return False
        bucket.append(item)
        return True

    def add_turn(self, role: str, content: str) -> None:
        role = role.strip().lower()
        if role not in {"user", "assistant", "tool", "system"}:
            role = "other"
        content = " ".join(content.split()).strip()
        if not content:
            return
        self.recent_turns.append({"role": role, "content": content})
        self.recent_turns = self.recent_turns[-max(1, self.max_recent_turns):]

    def render(self, *, include_recent: bool = True) -> str:
        lines = ["# TOKENSAVER MEMORY"]
        if self.project.strip():
            lines.append(f"PROJECT: {self.project.strip()}")
        for category in CATEGORIES:
            items = self.sections.get(category, [])
            if items:
                lines.append(f"\n## {category}")
                lines.extend(f"- {item}" for item in items)
        if include_recent and self.recent_turns:
            lines.append("\n## RECENT_TURNS")
            lines.extend(f"- {turn['role']}: {turn['content']}" for turn in self.recent_turns)
        return "\n".join(lines).strip()

    def save(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "RollingMemory":
        target = Path(path).expanduser()
        if not target.exists():
            return cls()
        raw: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
        memory = cls(project=str(raw.get("project", "")))
        memory.max_recent_turns = max(1, int(raw.get("max_recent_turns", 6)))
        sections = raw.get("sections", {})
        for category in CATEGORIES:
            items = sections.get(category, []) if isinstance(sections, dict) else []
            memory.sections[category] = [str(x) for x in items if str(x).strip()]
        turns = raw.get("recent_turns", [])
        memory.recent_turns = [
            {"role": str(t.get("role", "other")), "content": str(t.get("content", ""))}
            for t in turns if isinstance(t, dict) and str(t.get("content", "")).strip()
        ][-memory.max_recent_turns:]
        return memory

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "project": self.project,
            "sections": {name: self.sections.get(name, []) for name in CATEGORIES},
            "recent_turns": self.recent_turns[-max(1, self.max_recent_turns):],
            "max_recent_turns": self.max_recent_turns,
        }



def build_request(memory: RollingMemory, new_message: str) -> str:
    """Build the next prompt from compact memory and one new user message.

    The current message is deliberately not added to memory until after this
    prompt is rendered, avoiding duplicate inclusion in the same request.
    """
    sections = memory.render(include_recent=True)
    message = compress_text(new_message, deduplicate_lines=False).text
    parts = []
    if sections:
        parts.append("[COMPRESSED MEMORY]\\n" + sections)
    parts.append("[NEW MESSAGE]\\n" + message)
    return "\\n\\n".join(parts)
