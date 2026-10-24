"""HTTP-level regression tests for streaming and upstream failures."""
import http.client
import json
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from tokensaver.proxy import ToknixProxyHandler, load_config


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    mode = "stream"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if type(self).mode == "error":
            body = b'{"error":{"message":"upstream unavailable"}}'
            self.send_response(503, "Service Unavailable")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for event in (
            b'data: {"type":"message_start"}\n\n',
            b'data: {"type":"message_delta"}\n\n',
            b'data: [DONE]\n\n',
        ):
            self.wfile.write(event)
            self.wfile.flush()


class GatewayHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.upstream_thread.join(timeout=2)

    def setUp(self):
        self.proxy = None
        self.proxy_thread = None
        self._start_proxy()

    def tearDown(self):
        if self.proxy is not None:
            self.proxy.shutdown()
            self.proxy.server_close()
        if self.proxy_thread is not None:
            self.proxy_thread.join(timeout=2)

    def _start_proxy(self, upstream_url=None):
        if self.proxy is not None:
            self.proxy.shutdown()
            self.proxy.server_close()
            self.proxy_thread.join(timeout=2)
        if upstream_url is None:
            upstream_url = f"http://127.0.0.1:{self.upstream.server_port}"
        with patch.dict("os.environ", {
            "TOKNIX_UPSTREAM_BASE_URL": upstream_url,
            "TOKNIX_ENABLED": "0",
            "TOKNIX_HOST": "127.0.0.1",
            "TOKNIX_ALLOW_REMOTE": "0",
        }, clear=False):
            config = replace(load_config(), upstream_base_url=upstream_url, enabled=False)
        self.proxy = ThreadingHTTPServer(("127.0.0.1", 0), ToknixProxyHandler)
        self.proxy.gateway_config = config
        self.proxy.stats_lock = threading.Lock()
        self.proxy.stats = {
            "requests": 0, "compressed_requests": 0, "chars_saved": 0,
            "cache_control_requests": 0, "cache_protected_tool_outputs": 0,
            "upstream_errors": 0, "transform_errors": 0,
            "system_chars": 0, "old_history_chars": 0, "recent_turns_chars": 0, "tool_outputs_chars": 0,
            "estimated_system_tokens": 0, "estimated_old_history_tokens": 0,
            "estimated_recent_turns_tokens": 0, "estimated_tool_outputs_tokens": 0,
            "provider_input_tokens": 0, "provider_output_tokens": 0,
            "provider_cached_input_tokens": 0, "provider_cache_creation_input_tokens": 0,
            "provider_usage_responses": 0, "provider_usage_parse_errors": 0,
        }
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.proxy_thread.start()

    def _post(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=3)
        body = json.dumps({"model": "test", "messages": [{"role": "user", "content": "hello"}]})
        connection.request("POST", "/v1/chat/completions", body=body,
                           headers={"Content-Type": "application/json"})
        return connection, connection.getresponse()

    def test_streaming_sse_is_forwarded_without_buffering_or_body_corruption(self):
        _UpstreamHandler.mode = "stream"
        connection, response = self._post()
        try:
            self.assertEqual(response.status, 200)
            self.assertIn("text/event-stream", response.getheader("Content-Type", ""))
            self.assertEqual(response.getheader("Transfer-Encoding"), "chunked")
            self.assertEqual(
                response.read(),
                b'data: {"type":"message_start"}\n\n'
                b'data: {"type":"message_delta"}\n\n'
                b'data: [DONE]\n\n',
            )
        finally:
            connection.close()

    def test_upstream_5xx_status_and_body_are_forwarded_and_counted(self):
        _UpstreamHandler.mode = "error"
        connection, response = self._post()
        try:
            self.assertEqual(response.status, 503)
            payload = json.loads(response.read())
            self.assertEqual(payload["error"]["message"], "upstream unavailable")
            self.assertEqual(self.proxy.stats["upstream_errors"], 1)
        finally:
            connection.close()

    def test_unreachable_upstream_returns_gateway_error_instead_of_hanging(self):
        self._start_proxy("http://127.0.0.1:1")
        connection, response = self._post()
        try:
            self.assertEqual(response.status, 502)
            payload = json.loads(response.read())
            self.assertEqual(payload["error"], "Upstream request failed")
            self.assertEqual(self.proxy.stats["upstream_errors"], 1)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
