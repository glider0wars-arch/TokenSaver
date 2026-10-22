import json
import unittest
from unittest.mock import patch

from tokensaver.compressor import compress_text
from tokensaver.memory import RollingMemory
from tokensaver.proxy import (
    _content_text,
    _compress_tool_output_text,
    _compress_payload_tool_outputs,
    _parse_provider_usage,
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


    def test_anthropic_cut_landing_on_tool_use_moves_past_complete_cycle(self):
        messages = [
            {"role": "user", "content": "old context " * 100},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "read", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "result " * 20}]},
            {"role": "user", "content": "recent question " * 100},
            {"role": "assistant", "content": "recent answer " * 100},
        ]
        compacted, stats = compact_anthropic_messages(
            messages, keep_recent=4, cut_step=1, min_total_chars=1,
            min_old_chars=1, max_summary_chars=500,
        )
        self.assertTrue(stats["compressed"])
        retained = json.dumps(compacted, ensure_ascii=False)
        self.assertNotIn('"tool_use_id": "tool-1"', retained)
        self.assertNotIn('"id": "tool-1"', retained)
        self.assertEqual(compacted[0]["role"], "user")

    def test_chat_cut_with_consecutive_tool_replies_moves_to_owner(self):
        messages = [
            {"role": "user", "content": "old context " * 100},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "one", "arguments": "{}"}},
                {"id": "call-2", "type": "function", "function": {"name": "two", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "content": "first output " * 20},
            {"role": "tool", "tool_call_id": "call-2", "content": "second output " * 20},
            {"role": "user", "content": "recent question " * 100},
            {"role": "assistant", "content": "recent answer " * 100},
        ]
        for keep in (2, 3, 4, 5):
            compacted, stats = compact_chat_messages(
                messages, keep_recent=keep, cut_step=1, min_total_chars=1,
                min_old_chars=1, max_summary_chars=500,
            )
            if stats["compressed"]:
                first = next((m for m in compacted if m.get("role") not in {"system", "developer"}), {})
                self.assertNotEqual(first.get("role"), "tool")

    def test_memory_eviction_prefers_turns_before_open_tasks(self):
        memory = RollingMemory(max_total_chars=100, max_recent_turns=5)
        memory.add("OPEN_TASKS", "task that should remain if turns can be evicted")
        memory.add_turn("user", "x" * 30)
        memory.add_turn("assistant", "y" * 30)
        memory.add_turn("user", "z" * 30)
        self.assertTrue(memory.recent_turns)
        self.assertIn("task that should remain if turns can be evicted", memory.sections["OPEN_TASKS"])


    def test_tool_output_compression_keeps_errors_and_handles_windows_newlines(self):
        repeated = "ordinary repeated log line" + "\r\n"
        noise = "".join("ordinary filler text " + chr(65 + (i % 26)) + chr(65 + (i // 26)) + "\r\n" for i in range(40))
        source = "\x1b[31mSTART\x1b[0m\r\n" + repeated * 25 + noise + "ERROR: exact failure detail\r\nخطا: جزئیات مهم\r\nTAIL"
        result = _compress_tool_output_text(source, max_chars=180, head_lines=2, tail_lines=2)
        self.assertNotIn("\x1b[", result)
        self.assertIn("repeated x25", result)
        self.assertIn("ERROR: exact failure detail", result)
        self.assertIn("خطا: جزئیات مهم", result)
        self.assertIn("omitted]", result)

    def test_tool_output_compression_protects_latest_tool_cycle(self):
        old_output = "old noisy output\n" * 200
        latest_output = "latest tool output must remain unchanged\n" * 150
        messages = [
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "old-call", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "old-call", "content": old_output},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "new-call", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "new-call", "content": latest_output},
        ]
        saved = _compress_payload_tool_outputs(messages, "chat", 100)
        self.assertGreater(saved, 0)
        self.assertLess(len(messages[2]["content"]), len(old_output))
        self.assertEqual(messages[4]["content"], latest_output)

    def test_duplicate_earlier_tool_output_becomes_reference(self):
        same = "line one\nline two"
        messages = [
            {"role": "tool", "tool_call_id": "first", "content": same},
            {"role": "assistant", "content": "between"},
            {"role": "tool", "tool_call_id": "second", "content": same},
        ]
        _compress_payload_tool_outputs(messages, "chat", 100)
        self.assertIn("identical tool output repeated later", messages[0]["content"])
        self.assertEqual(messages[2]["content"], same)

    def test_provider_usage_parses_json_and_sse(self):
        json_usage = _parse_provider_usage(
            b'{"usage":{"prompt_tokens":20,"completion_tokens":7,"prompt_tokens_details":{"cached_tokens":5}}}',
            "application/json",
        )
        self.assertEqual(json_usage, {"input": 20, "output": 7, "cached_input": 5})
        sse = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":12,"output_tokens":3,"input_tokens_details":{"cached_tokens":4}}}}\n\ndata: [DONE]\n\n'
        self.assertEqual(
            _parse_provider_usage(sse, "text/event-stream"),
            {"input": 12, "output": 3, "cached_input": 4},
        )


if __name__ == "__main__":
    unittest.main()
