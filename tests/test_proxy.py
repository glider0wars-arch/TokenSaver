import unittest
from unittest.mock import patch

from tokensaver.proxy import (
    _IMPORTANT, _compress_tool_output_text, _compress_payload_tool_outputs,
    _parse_provider_usage, _payload_breakdown, compact_anthropic_messages, compact_chat_messages,
    compact_response_input, transform_payload,
)


def json_text(value):
    import json
    return json.dumps(value, ensure_ascii=False)


class ProxyTests(unittest.TestCase):
    def test_compacts_old_chat_but_preserves_system_and_recent(self):
        messages = [{"role": "system", "content": "Never remove tests."}]
        messages += [{"role": "user", "content": f"Old detail {i}. " + ("extra context " * 70)} for i in range(9)]
        messages += [{"role": "user", "content": "Current request: keep this exact."}]
        result, stats = compact_chat_messages(messages, keep_recent=2, min_old_chars=200, min_total_chars=100, max_summary_chars=700)
        self.assertTrue(stats["compressed"])
        self.assertEqual(result[0], messages[0])
        self.assertIn("Current request: keep this exact.", str(result[-1]))
        self.assertTrue(any("compacted earlier conversation" in str(m.get("content", "")) for m in result if isinstance(m, dict)))
        self.assertLess(stats["after_chars"], stats["before_chars"])

    def test_does_not_compress_short_history_or_below_total_threshold(self):
        messages = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]
        result, stats = compact_chat_messages(messages)
        self.assertIs(result, messages)
        self.assertFalse(stats["compressed"])
        large_history = [{"role": "user", "content": "small"} for _ in range(12)]
        unchanged, stats = compact_chat_messages(large_history, keep_recent=2, min_total_chars=1000)
        self.assertIs(unchanged, large_history)
        self.assertFalse(stats["compressed"])

    def test_openai_chat_payload_compacted(self):
        body = {"model": "test", "messages": [{"role": "user", "content": f"Message {i}. " + ("x " * 100)} for i in range(12)]}
        transformed, stats = transform_payload("/v1/chat/completions", body, keep_recent=2, max_summary_chars=500, min_old_chars=100, min_total_chars=100)
        self.assertTrue(stats["compressed"])
        self.assertEqual(transformed["model"], "test")
        self.assertIn("Message 11", str(transformed["messages"][-1]))

    def test_anthropic_payload_keeps_system_and_newest(self):
        body = {"model": "test", "system": "Follow the system rules.", "messages": [{"role": "user", "content": f"Message {i}. " + ("extra " * 100)} for i in range(12)]}
        transformed, stats = transform_payload("/v1/messages", body, keep_recent=2, max_summary_chars=500, min_old_chars=100, min_total_chars=100)
        self.assertTrue(stats["compressed"])
        self.assertIn("Follow the system rules.", transformed["system"])
        self.assertIn("compacted earlier conversation", transformed["system"])
        self.assertIn("Message 11", str(transformed["messages"][-1]))
        self.assertEqual(transformed["messages"][0]["role"], "user")

    def test_anthropic_tool_use_and_result_never_split_and_are_summarized(self):
        messages = [
            {"role": "user", "content": "Old background " + ("context " * 50)},
            {"role": "assistant", "content": "Prior decision: keep the tool interaction."},
            {"role": "user", "content": "Another old turn " + ("details " * 50)},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-7", "name": "lookup", "input": {"query": "important lookup"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-7", "content": "tool result payload preserved"}]},
            {"role": "assistant", "content": "Tool result processed."},
            {"role": "user", "content": "Newest user turn " + ("new " * 40)},
            {"role": "assistant", "content": "Newest answer " + ("answer " * 40)},
            {"role": "user", "content": "Current request " + ("current " * 40)},
            {"role": "assistant", "content": "Latest assistant " + ("latest " * 40)},
        ]
        result, stats = compact_anthropic_messages(messages, keep_recent=4, min_old_chars=100, min_total_chars=100, max_summary_chars=2000)
        self.assertTrue(stats["compressed"])
        self.assertEqual(result[0]["role"], "user")
        self.assertNotIn("tool-7", json_text(result))
        self.assertIn("tool_use", stats["summary_text"])
        self.assertIn("tool_result", stats["summary_text"])
        self.assertIn("tool result payload preserved", stats["summary_text"])

    def test_cache_control_in_old_context_or_system_skips_compaction(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "cached", "cache_control": {"type": "ephemeral"}}]}]
        messages += [{"role": "user", "content": "lots of old context " * 100} for _ in range(12)]
        result, stats = compact_chat_messages(messages, keep_recent=2, min_total_chars=100, min_old_chars=100)
        self.assertIs(result, messages)
        self.assertFalse(stats["compressed"])
        body = {"system": [{"type": "text", "text": "cached system", "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": "old " * 100} for _ in range(12)]}
        transformed, stats = transform_payload("/v1/messages", body, keep_recent=2, min_total_chars=100, min_old_chars=100)
        self.assertFalse(stats["compressed"])
        self.assertEqual(transformed, body)

    def test_bucketed_summary_stays_identical_inside_same_bucket(self):
        messages = [{"role": "user", "content": f"Old fact {i}: " + ("context " * 35)} for i in range(10)]
        first, stats1 = compact_chat_messages(messages, keep_recent=2, cut_step=4, min_total_chars=100, min_old_chars=100, max_summary_chars=1200)
        second_messages = messages + [{"role": "user", "content": "New turn within the same bucket " + ("new " * 10)}]
        second, stats2 = compact_chat_messages(second_messages, keep_recent=2, cut_step=4, min_total_chars=100, min_old_chars=100, max_summary_chars=1200)
        self.assertTrue(stats1["compressed"])
        self.assertTrue(stats2["compressed"])
        note1 = next(m["content"] for m in first if m.get("role") == "system" and "compacted earlier" in m.get("content", ""))
        note2 = next(m["content"] for m in second if m.get("role") == "system" and "compacted earlier" in m.get("content", ""))
        self.assertEqual(note1, note2)

    def test_responses_input_compacts_old_messages_and_keeps_recent_tool_cycle(self):
        items = [{"type": "message", "role": "user", "content": f"Old {i}. " + ("detail " * 80)} for i in range(12)]
        items += [
            {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "found relevant result"},
            {"type": "message", "role": "assistant", "content": "Final current turn"},
        ]
        compacted, stats = compact_response_input(items, keep_recent=2, min_old_chars=100, min_total_chars=100, max_summary_chars=600)
        self.assertTrue(stats["compressed"])
        self.assertTrue(any(item.get("type") == "function_call" for item in compacted))
        self.assertTrue(any(item.get("type") == "function_call_output" for item in compacted))
        self.assertIn("Final current turn", str(compacted[-1]))

    def test_responses_cut_never_splits_reasoning_from_following_item(self):
        items = [{"type": "message", "role": "user", "content": f"Old {i}. " + ("detail " * 80)} for i in range(7)]
        items += [
            {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "reasoning kept with next item"}]},
            {"type": "message", "role": "assistant", "content": "The item following reasoning"},
            {"type": "message", "role": "user", "content": "Newest request " + ("latest " * 40)},
            {"type": "message", "role": "assistant", "content": "Newest answer " + ("answer " * 40)},
        ]
        compacted, stats = compact_response_input(items, keep_recent=2, min_old_chars=100, min_total_chars=100, max_summary_chars=1000)
        self.assertTrue(stats["compressed"])
        reasoning_index = next(i for i, item in enumerate(compacted) if item.get("type") == "reasoning")
        self.assertEqual(compacted[reasoning_index + 1].get("content"), "The item following reasoning")

    def test_tool_output_compression_keeps_errors_persian_and_windows_lines(self):
        raw = ("\x1b[32mINFO repeated line\x1b[0m\r\n" * 80
               + "Traceback (most recent call last):\r\n"
               + "ValueError: exact failure\r\n"
               + "خطا: اطلاعات مهم را حفظ کن\r\n"
               + "tail marker\r\n")
        compressed = _compress_tool_output_text(raw, max_chars=300, head_lines=3, tail_lines=3)
        self.assertNotIn("\x1b[32m", compressed)
        self.assertIn("ValueError: exact failure", compressed)
        self.assertIn("خطا: اطلاعات مهم را حفظ کن", compressed)
        self.assertIn("tail marker", compressed)
        self.assertIn("repeated x80", compressed)

    def test_tool_output_compression_protects_latest_tool_cycle(self):
        old_output = "INFO repetitive log line\n" * 100
        latest_output = "LATEST TOOL OUTPUT MUST REMAIN EXACT\n" * 100
        body = {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "old-call", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "old-call", "content": old_output},
            {"role": "assistant", "content": "processing"},
            {"role": "assistant", "tool_calls": [{"id": "new-call", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "new-call", "content": latest_output},
        ]}
        with patch.dict("os.environ", {"TOKENSAVER_TOOL_OUTPUT_MAX_CHARS": "256"}):
            _compress_payload_tool_outputs(body["messages"], "chat", 256)
        self.assertLess(len(body["messages"][1]["content"]), len(old_output))
        self.assertEqual(body["messages"][4]["content"], latest_output)

    def test_tool_output_compression_respects_cap_when_important_lines_fit(self):
        lines = [f"ordinary entry {i:03d} with padding" for i in range(30)]
        lines[10] = "ERROR: important one"
        lines[20] = "Traceback: important two"
        compressed = _compress_tool_output_text("\n".join(lines), max_chars=100, head_lines=2, tail_lines=2)
        self.assertLessEqual(len(compressed), 100)
        self.assertIn("ERROR: important one", compressed)
        self.assertIn("Traceback: important two", compressed)

    def test_duplicate_tool_output_reference_does_not_use_pre_compaction_index(self):
        repeated = "same deterministic output\\n" * 50
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "old", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "old", "content": repeated},
            {"role": "assistant", "content": "other history"},
            {"role": "assistant", "tool_calls": [{"id": "new", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "new", "content": repeated},
        ]
        _compress_payload_tool_outputs(messages, "chat", 80)
        earlier = messages[1]["content"]
        self.assertIn("identical tool output repeated later", earlier)
        self.assertNotIn("see message", earlier)
        self.assertEqual(messages[4]["content"], repeated)

    def test_anthropic_cache_creation_breakdown_is_measured(self):
        raw = (b'{"type":"message_start","message":{"usage":{"input_tokens":10,'
               b'"cache_read_input_tokens":3,"cache_creation":{"ephemeral_5m_input_tokens":4,'
               b'"ephemeral_1h_input_tokens":5}}}}')
        usage = _parse_provider_usage(raw, "application/json")
        self.assertEqual(usage, {
            "input": 22,
            "output": 0,
            "cached_input": 3,
            "cache_creation_input": 9,
        })

    def test_response_breakdown_does_not_hoist_late_developer_message(self):
        payload = {"input": [
            {"type": "message", "role": "user", "content": "old conversation"},
            {"type": "message", "role": "developer", "content": "late developer instruction"},
            {"type": "message", "role": "user", "content": "latest question"},
        ]}
        breakdown = _payload_breakdown("/v1/responses", payload, keep_recent=1)
        self.assertNotIn("late developer instruction", breakdown["system"])
        self.assertIn("late developer instruction", breakdown["old_history"])

    def test_usage_parser_handles_json_and_sse_responses(self):
        usage = _parse_provider_usage(
            b'{"usage":{"prompt_tokens":100,"completion_tokens":20,"prompt_tokens_details":{"cached_tokens":30}}}',
            "application/json",
        )
        self.assertEqual(usage, {
            "input": 100,
            "output": 20,
            "cached_input": 30,
            "cache_creation_input": 0,
        })
        sse = (b'data: {"type":"response.completed","response":{"usage":{"input_tokens":11,"output_tokens":4,'
               b'"input_tokens_details":{"cached_tokens":3}}}}\n\n')
        usage = _parse_provider_usage(sse, "text/event-stream")
        self.assertEqual(usage, {
            "input": 11,
            "output": 4,
            "cached_input": 3,
            "cache_creation_input": 0,
        })

    def test_important_regex_does_not_promote_generic_modal_words_or_slashes(self):
        for line in ("We must update this later.", "We should look at this.", "باید این کار را انجام بدهیم", "The path is /tmp/cache"):
            self.assertIsNone(_IMPORTANT.search(line), line)
        for line in ("Error: request failed", "Decision: preserve cached prefix", "فایل app.py خراب است", "tool_use id=abc"):
            self.assertIsNotNone(_IMPORTANT.search(line), line)


if __name__ == "__main__":
    unittest.main()
