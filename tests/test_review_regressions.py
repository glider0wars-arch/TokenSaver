import json
import unittest
from unittest.mock import patch

from tokensaver.compressor import compress_text
from tokensaver.memory import RollingMemory
from tokensaver.proxy import (
    _content_text,
    compact_anthropic_messages,
    compact_chat_messages,
    compact_response_input,
)


class ReviewRegressionTests(unittest.TestCase):
    def test_chat_cut_does_not_start_with_orphan_tool_message(self):
        messages = [
            {"role": "user", "content": "old context " * 100},
            {"role": "assistant", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "x", "arguments": "{}"}}], "content": None},
            {"role": "tool", "tool_call_id": "call-1", "content": "tool output " * 20},
            {"role": "user", "content": "recent question " * 100},
            {"role": "assistant", "content": "recent answer " * 100},
        ]
        for keep in (1, 2, 3):
            compacted, stats = compact_chat_messages(
                messages, keep_recent=keep, cut_step=1, min_total_chars=1,
                min_old_chars=1, max_summary_chars=500,
            )
            if stats["compressed"]:
                first_conversation = next((m for m in compacted if m.get("role") not in {"system", "developer"}), {})
                self.assertNotEqual(first_conversation.get("role"), "tool")

    def test_responses_cut_keeps_function_call_with_output(self):
        items = [
            {"type": "message", "role": "user", "content": "old context " * 100},
            {"type": "function_call", "call_id": "fc-1", "name": "read", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "fc-1", "output": "tool output " * 20},
            {"type": "message", "role": "user", "content": "recent question " * 100},
            {"type": "message", "role": "assistant", "content": "recent answer " * 100},
        ]
        for keep in (1, 2, 3):
            compacted, stats = compact_response_input(
                items, keep_recent=keep, cut_step=1, min_total_chars=1,
                min_old_chars=1, max_summary_chars=500,
            )
            if stats["compressed"]:
                first = next((m for m in compacted if m.get("type") not in {"reasoning"}), {})
                if first.get("type") == "function_call_output":
                    self.fail("retained context starts with an orphan function_call_output")

    def test_anthropic_cut_never_keeps_tool_result_without_its_use(self):
        messages = [
            {"role": "user", "content": "old context " * 100},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "read", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "result " * 20}]},
            {"role": "user", "content": "recent question " * 100},
            {"role": "assistant", "content": "recent answer " * 100},
        ]
        for keep in (1, 2, 3):
            compacted, stats = compact_anthropic_messages(
                messages, keep_recent=keep, cut_step=1, min_total_chars=1,
                min_old_chars=1, max_summary_chars=500,
            )
            if stats["compressed"]:
                retained = json.dumps(compacted, ensure_ascii=False)
                if '"tool_use_id": "tool-1"' in retained:
                    self.assertIn('"type": "tool_use"', retained)

    def test_content_text_omits_base64_and_image_payloads(self):
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 10000}},
            {"type": "text", "text": "keep this sentence"},
        ]
        result = _content_text(content)
        self.assertLess(len(result), 200)
        self.assertIn("[image omitted]", result)
        self.assertIn("keep this sentence", result)

    def test_compressor_only_deduplicates_adjacent_safe_prose_lines(self):
        prose = "A sufficiently long repeated prose line that is safe.\nA sufficiently long repeated prose line that is safe."
        self.assertEqual(compress_text(prose).text.count("A sufficiently long"), 1)
        nonadjacent = "A sufficiently long repeated prose line that is safe.\nOther content.\nA sufficiently long repeated prose line that is safe."
        self.assertEqual(compress_text(nonadjacent).text.count("A sufficiently long"), 2)
        code_like = "if value is not None:    return value\nif value is not None:    return value"
        self.assertEqual(compress_text(code_like).text.count("if value"), 2)

    def test_memory_add_failure_is_atomic_and_preserves_durable_categories(self):
        memory = RollingMemory(max_total_chars=70)
        memory.add("GOALS", "goal that must remain")
        memory.add("RULES", "rule that must remain")
        before = memory.to_dict()
        with self.assertRaises(ValueError):
            memory.add("DECISIONS", "x" * 500)
        self.assertEqual(memory.to_dict(), before)
        self.assertIn("goal that must remain", memory.sections["GOALS"])
        self.assertIn("rule that must remain", memory.sections["RULES"])

    def test_memory_turn_truncates_instead_of_failing(self):
        memory = RollingMemory(max_total_chars=10000)
        with patch.dict("os.environ", {"TOKENSAVER_MAX_TURN_CHARS": "2000"}):
            memory.add_turn("user", "x" * 5000)
        self.assertLessEqual(len(memory.recent_turns[-1]["content"]), 2000)


if __name__ == "__main__":
    unittest.main()
