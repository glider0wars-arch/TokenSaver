import unittest

from tokensaver.proxy import compact_chat_messages, compact_response_input, transform_payload


class ProxyTests(unittest.TestCase):
    def test_compacts_old_chat_but_preserves_system_and_recent(self):
        messages = [{"role": "system", "content": "Never remove tests."}]
        messages += [{"role": "user", "content": f"Old detail {i}. " + ("extra context " * 70)} for i in range(5)]
        messages += [{"role": "user", "content": "Current request: keep this exact."}]
        result, stats = compact_chat_messages(messages, keep_recent=2, min_old_chars=200, max_summary_chars=700)
        self.assertTrue(stats["compressed"])
        self.assertEqual(result[0], messages[0])
        self.assertIn("Current request: keep this exact.", str(result[-1]))
        self.assertTrue(any("compacted earlier conversation" in str(m.get("content", "")) for m in result if isinstance(m, dict)))
        self.assertLess(stats["after_chars"], stats["before_chars"])

    def test_does_not_compress_short_history(self):
        messages = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]
        result, stats = compact_chat_messages(messages)
        self.assertIs(result, messages)
        self.assertFalse(stats["compressed"])

    def test_openai_chat_payload_compacted(self):
        body = {"model": "test", "messages": [{"role": "user", "content": f"Message {i}. " + ("x " * 100)} for i in range(8)]}
        transformed, stats = transform_payload("/v1/chat/completions", body, keep_recent=2, max_summary_chars=500, min_old_chars=100)
        self.assertTrue(stats["compressed"])
        self.assertEqual(transformed["model"], "test")
        self.assertIn("Message 7", str(transformed["messages"][-1]))

    def test_anthropic_payload_keeps_system_and_newest(self):
        body = {"model": "test", "system": "Follow the system rules.", "messages": [{"role": "user", "content": f"Message {i}. " + ("extra " * 100)} for i in range(8)]}
        transformed, stats = transform_payload("/v1/messages", body, keep_recent=2, max_summary_chars=500, min_old_chars=100)
        self.assertTrue(stats["compressed"])
        self.assertIn("Follow the system rules.", transformed["system"])
        self.assertIn("compacted earlier conversation", transformed["system"])
        self.assertIn("Message 7", str(transformed["messages"][-1]))

    def test_responses_input_compacts_old_messages_and_keeps_recent_tool_cycle(self):
        items = [{"type": "message", "role": "user", "content": f"Old {i}. " + ("detail " * 80)} for i in range(5)]
        items += [
            {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "found relevant result"},
            {"type": "message", "role": "assistant", "content": "Final current turn"},
        ]
        compacted, stats = compact_response_input(items, keep_recent=2, min_old_chars=100, max_summary_chars=600)
        self.assertTrue(stats["compressed"])
        self.assertTrue(any(item.get("type") == "function_call" for item in compacted))
        self.assertTrue(any(item.get("type") == "function_call_output" for item in compacted))
        self.assertIn("Final current turn", str(compacted[-1]))

    def test_responses_payload_uses_response_compactor(self):
        body = {"model": "test", "input": [{"type": "message", "role": "user", "content": f"Message {i}. " + ("x " * 100)} for i in range(8)]}
        transformed, stats = transform_payload("/v1/responses", body, keep_recent=2, max_summary_chars=500, min_old_chars=100)
        self.assertTrue(stats["compressed"])
        self.assertIn("Message 7", str(transformed["input"][-1]))


if __name__ == "__main__":
    unittest.main()
