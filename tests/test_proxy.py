import unittest

from tokensaver.proxy import (
    _IMPORTANT, compact_anthropic_messages, compact_chat_messages,
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

    def test_important_regex_does_not_promote_generic_modal_words_or_slashes(self):
        for line in ("We must update this later.", "We should look at this.", "باید این کار را انجام بدهیم", "The path is /tmp/cache"):
            self.assertIsNone(_IMPORTANT.search(line), line)
        for line in ("Error: request failed", "Decision: preserve cached prefix", "فایل app.py خراب است", "tool_use id=abc"):
            self.assertIsNotNone(_IMPORTANT.search(line), line)


if __name__ == "__main__":
    unittest.main()
