"""Bounded, persistent rolling memory for long AI-assisted workflows."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
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
    max_items_per_category: int = 20
    max_total_chars: int = 12000

    def _total_chars(self) -> int:
        return len(self.project) + sum(len(item) for values in self.sections.values() for item in values) + sum(
            len(turn.get("role", "")) + len(turn.get("content", "")) for turn in self.recent_turns
        )

    def _enforce_limits(self) -> None:
        if self.max_items_per_category < 1 or self.max_total_chars < 1:
            raise ValueError("Memory limits must be positive integers.")
        for category in CATEGORIES:
            bucket = self.sections.setdefault(category, [])
            if len(bucket) > self.max_items_per_category:
                self.sections[category] = bucket[-self.max_items_per_category:]
        if self._total_chars() > self.max_total_chars:
            # Evict the oldest recent turns first, then oldest stored facts.
            while self.recent_turns and self._total_chars() > self.max_total_chars:
                self.recent_turns.pop(0)
            for category in CATEGORIES:
                bucket = self.sections.get(category, [])
                while bucket and self._total_chars() > self.max_total_chars:
                    bucket.pop(0)
            if self._total_chars() > self.max_total_chars:
                raise ValueError(
                    f"Memory exceeds max_total_chars={self.max_total_chars}; shorten the project name or latest item."
                )

    def add(self, category: str, item: str) -> bool:
        category = category.upper().strip()
        item = item.strip()
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category {category!r}. Choose from: {', '.join(CATEGORIES)}")
        if not item:
            return False
        bucket = self.sections.setdefault(category, [])
        if item in bucket:
            return False
        bucket.append(item)
        try:
            self._enforce_limits()
        except ValueError:
            bucket.remove(item)
            raise
        return item in bucket

    def remove(self, category: str, item: str) -> bool:
        category = category.upper().strip()
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category {category!r}. Choose from: {', '.join(CATEGORIES)}")
        bucket = self.sections.setdefault(category, [])
        try:
            bucket.remove(item.strip())
        except ValueError:
            return False
        return True

    def clear(self) -> None:
        self.sections = {name: [] for name in CATEGORIES}
        self.recent_turns = []

    def add_turn(self, role: str, content: str) -> None:
        role = role.strip().lower()
        if role not in {"user", "assistant", "tool", "system"}:
            role = "other"
        content = content.strip()
        if not content:
            return
        self.recent_turns.append({"role": role, "content": content})
        self.recent_turns = self.recent_turns[-max(1, self.max_recent_turns):]
        self._enforce_limits()

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
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        backup = target.with_suffix(target.suffix + ".bak")
        temp_name: str | None = None
        try:
            if target.exists():
                shutil.copy2(target, backup)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=target.parent,
                                             prefix=target.name + ".", suffix=".tmp", delete=False) as temp:
                temp_name = temp.name
                temp.write(payload)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_name, target)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    @classmethod
    def load(cls, path: str | Path) -> "RollingMemory":
        target = Path(path).expanduser()
        if not target.exists():
            raise FileNotFoundError(f"Memory file not found: {target}. Create it with 'tokensaver memory-init {target}'.")
        try:
            raw: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            backup = target.with_suffix(target.suffix + ".bak")
            try:
                shutil.copy2(target, backup)
            except OSError as backup_exc:
                raise ValueError(f"Memory JSON is corrupt at {target}; backup failed: {backup_exc}") from exc
            raise ValueError(f"Memory JSON is corrupt at {target}. A copy was saved to {backup}.") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Memory file {target} must contain a JSON object.")
        memory = cls(
            project=str(raw.get("project", "")),
            max_recent_turns=max(1, int(raw.get("max_recent_turns", 6))),
            max_items_per_category=max(1, int(raw.get("max_items_per_category", 20))),
            max_total_chars=max(1, int(raw.get("max_total_chars", 12000))),
        )
        sections = raw.get("sections", {})
        if isinstance(sections, dict):
            for category in CATEGORIES:
                items = sections.get(category, [])
                if isinstance(items, list):
                    memory.sections[category] = [str(x) for x in items if isinstance(x, str) and x.strip()]
        turns = raw.get("recent_turns", [])
        memory.recent_turns = [
            {"role": str(t.get("role", "other")), "content": str(t.get("content", ""))}
            for t in turns if isinstance(t, dict) and str(t.get("content", "")).strip()
        ][-memory.max_recent_turns:]
        memory._enforce_limits()
        return memory

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "project": self.project,
            "sections": {name: self.sections.get(name, []) for name in CATEGORIES},
            "recent_turns": self.recent_turns[-max(1, self.max_recent_turns):],
            "max_recent_turns": self.max_recent_turns,
            "max_items_per_category": self.max_items_per_category,
            "max_total_chars": self.max_total_chars,
        }


def build_request(memory: RollingMemory, new_message: str) -> str:
    """Build a prompt from compact memory and one new message without flattening it."""
    sections = memory.render(include_recent=True)
    message = compress_text(new_message, deduplicate_lines=False).text
    parts = []
    if sections:
        parts.append("[COMPRESSED MEMORY]\n" + sections)
    parts.append("[NEW MESSAGE]\n" + message)
    return "\n\n".join(parts)
