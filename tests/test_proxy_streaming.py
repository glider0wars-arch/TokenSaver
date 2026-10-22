import io
import threading
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch

from tokensaver.proxy import TokenSaverProxyHandler


class _StreamingResponse:
    status = 200
    reason = "OK"

    def __init__(self):
        self.chunks = [b"data: first event\n\n", b"data: [DONE]\n\n"]
        self.read_calls = 0

    def getheaders(self):
        return [("Content-Type", "text/event-stream")]

    def getheader(self, name, default=None):
        return "text/event-stream" if name.lower() == "content-type" else default

    def read1(self, size):
        return self.chunks.pop(0) if self.chunks else b""

    def read(self, size):
        self.read_calls += 1
        raise AssertionError("blocking read() must not be used for streamed responses")


class _StreamingConnection:
    def __init__(self, response):
        self.response = response

    def request(self, *args, **kwargs):
        self.request_args = args
        self.request_kwargs = kwargs

    def getresponse(self):
        return self.response

    def close(self):
        return None


class StreamingProxyTests(unittest.TestCase):
    def test_streaming_forward_uses_nonblocking_read1(self):
        response = _StreamingResponse()
        connection = _StreamingConnection(response)
        config = SimpleNamespace(
            max_body_bytes=1024,
            upstream_base_url="http://127.0.0.1:9999",
            upstream_timeout=1,
            upstream_api_key="",
            allow_remote=False,
            gateway_token="",
        )
        server = SimpleNamespace(
            gateway_config=config,
            stats_lock=threading.Lock(),
            stats={
                "requests": 0, "compressed_requests": 0, "chars_saved": 0,
                "upstream_errors": 0, "transform_errors": 0,
            },
        )
        handler = object.__new__(TokenSaverProxyHandler)
        handler._request_config = config
        handler._request_content_length = 0
        handler.command = "GET"
        handler.path = "/v1/models"
        handler.headers = Message()
        handler.server = server
        handler.wfile = io.BytesIO()
        handler.close_connection = False
        handler.send_response = lambda *args, **kwargs: None
        handler.send_header = lambda *args, **kwargs: None
        handler.end_headers = lambda *args, **kwargs: None

        with patch("tokensaver.proxy.http.client.HTTPConnection", return_value=connection):
            handler._forward()

        output = handler.wfile.getvalue()
        self.assertIn(b"data: first event", output)
        self.assertIn(b"data: [DONE]", output)
        self.assertEqual(response.read_calls, 0)
        self.assertTrue(handler.close_connection)

    def test_final_usage_event_is_parsed_after_stream_exceeds_tee_limit(self):
        response = _StreamingResponse()
        response.chunks = [b"data: " + (b"x" * 1_100_000) + b"\\n\\n",
                           b'data: {"type":"message_delta","usage":{"output_tokens":37}}\\n\\n']
        connection = _StreamingConnection(response)
        config = SimpleNamespace(
            max_body_bytes=1024,
            upstream_base_url="http://127.0.0.1:9999",
            upstream_timeout=1,
            upstream_api_key="",
            allow_remote=False,
            gateway_token="",
            usage_tee_max_bytes=1024,
        )
        server = SimpleNamespace(
            gateway_config=config,
            stats_lock=threading.Lock(),
            stats={"requests": 0, "compressed_requests": 0, "chars_saved": 0,
                   "upstream_errors": 0, "transform_errors": 0},
        )
        handler = object.__new__(TokenSaverProxyHandler)
        handler._request_config = config
        handler._request_content_length = 0
        handler.command = "GET"
        handler.path = "/v1/models"
        handler.headers = Message()
        handler.server = server
        handler.wfile = io.BytesIO()
        handler.close_connection = False
        handler.send_response = lambda *args, **kwargs: None
        handler.send_header = lambda *args, **kwargs: None
        handler.end_headers = lambda *args, **kwargs: None

        with patch("tokensaver.proxy.http.client.HTTPConnection", return_value=connection):
            handler._forward()

        self.assertGreater(len(handler.wfile.getvalue()), 1_000_000)
        self.assertEqual(server.stats["provider_output_tokens"], 37)
        self.assertEqual(server.stats["provider_usage_responses"], 1)

    def test_options_request_body_is_forwarded_upstream(self):
        response = _StreamingResponse()
        connection = _StreamingConnection(response)
        config = SimpleNamespace(
            max_body_bytes=1024,
            upstream_base_url="http://127.0.0.1:9999",
            upstream_timeout=1,
            upstream_api_key="",
            allow_remote=False,
            gateway_token="",
        )
        server = SimpleNamespace(
            gateway_config=config,
            stats_lock=threading.Lock(),
            stats={
                "requests": 0, "compressed_requests": 0, "chars_saved": 0,
                "upstream_errors": 0, "transform_errors": 0,
            },
        )
        handler = object.__new__(TokenSaverProxyHandler)
        handler._request_config = config
        handler._request_content_length = 3
        handler.command = "OPTIONS"
        handler.path = "/v1/options"
        handler.headers = Message()
        handler.server = server
        handler.rfile = io.BytesIO(b"abc")
        handler.wfile = io.BytesIO()
        handler.close_connection = False
        handler.send_response = lambda *args, **kwargs: None
        handler.send_header = lambda *args, **kwargs: None
        handler.end_headers = lambda *args, **kwargs: None

        with patch("tokensaver.proxy.http.client.HTTPConnection", return_value=connection):
            handler._forward()

        self.assertEqual(connection.request_kwargs["body"], b"abc")


if __name__ == "__main__":
    unittest.main()
