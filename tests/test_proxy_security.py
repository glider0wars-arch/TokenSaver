import os
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from tokensaver.proxy import TokenSaverProxyHandler, load_config


class GatewaySecurityTests(unittest.TestCase):
    def make_server(self, env):
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        config = load_config()
        server = ThreadingHTTPServer(("127.0.0.1", 0), TokenSaverProxyHandler)
        server.gateway_config = config
        server.stats_lock = threading.Lock()
        server.stats = {"requests": 0, "compressed_requests": 0, "chars_saved": 0, "upstream_errors": 0}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def request(self, server, request_text):
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=3) as sock:
            sock.sendall(request_text.encode("ascii"))
            return sock.recv(4096).decode("iso-8859-1")

    def test_remote_mode_requires_token_for_health_and_stats(self):
        server = self.make_server({
            "TOKENSAVER_ALLOW_REMOTE": "1",
            "TOKENSAVER_GATEWAY_TOKEN": "test-only-token",
            "TOKENSAVER_UPSTREAM_BASE_URL": "https://api.openai.com",
        })
        for path in ("/health", "/stats"):
            denied = self.request(server, f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            self.assertIn("401", denied)
            allowed = self.request(server, f"GET {path} HTTP/1.1\r\nHost: localhost\r\nX-TokenSaver-Token: test-only-token\r\nConnection: close\r\n\r\n")
            self.assertIn("200", allowed)

    def test_gateway_header_takes_precedence_over_upstream_bearer_key(self):
        server = self.make_server({
            "TOKENSAVER_ALLOW_REMOTE": "1",
            "TOKENSAVER_GATEWAY_TOKEN": "gateway-secret",
            "TOKENSAVER_UPSTREAM_BASE_URL": "https://api.openai.com",
        })
        response = self.request(
            server,
            "GET /health HTTP/1.1\r\nHost: localhost\r\n"
            "X-TokenSaver-Token: gateway-secret\r\n"
            "Authorization: Bearer upstream-api-key\r\n"
            "Connection: close\r\n\r\n",
        )
        self.assertIn("200", response)

    def test_non_ascii_gateway_header_returns_401_without_crashing(self):
        server = self.make_server({
            "TOKENSAVER_ALLOW_REMOTE": "1",
            "TOKENSAVER_GATEWAY_TOKEN": "gateway-secret",
            "TOKENSAVER_UPSTREAM_BASE_URL": "https://api.openai.com",
        })
        request = ("GET /health HTTP/1.1\r\nHost: localhost\r\n"
                   "X-TokenSaver-Token: café\r\nConnection: close\r\n\r\n")
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=3) as sock:
            sock.sendall(request.encode("latin-1"))
            response = sock.recv(4096).decode("iso-8859-1")
        self.assertIn("401", response)

    def test_invalid_and_negative_content_length_return_400(self):
        server = self.make_server({"TOKENSAVER_UPSTREAM_BASE_URL": "https://api.openai.com"})
        for value in ("invalid", "-1"):
            response = self.request(server, f"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nContent-Length: {value}\r\nConnection: close\r\n\r\n")
            self.assertIn("400", response)


    def test_chunked_request_body_is_rejected_instead_of_silently_dropped(self):
        server = self.make_server({
            "TOKENSAVER_UPSTREAM_BASE_URL": "http://127.0.0.1:1",
        })
        response = self.request(
            server,
            "POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
            "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n0\r\n\r\n",
        )
        self.assertIn("400", response)
        self.assertNotIn("502", response)



    def test_duplicate_content_length_headers_are_rejected(self):
        server = self.make_server({
            "TOKENSAVER_UPSTREAM_BASE_URL": "http://127.0.0.1:1",
        })
        response = self.request(
            server,
            "POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
            "Content-Length: 0\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        )
        self.assertIn("400", response)
        self.assertIn("Content-Length", response)


    def test_all_numeric_settings_are_validated_at_startup(self):
        with patch.dict(os.environ, {
            "TOKENSAVER_CUT_STEP": "many",
            "TOKENSAVER_UPSTREAM_BASE_URL": "https://api.openai.com",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "TOKENSAVER_CUT_STEP must be an integer"):
                load_config()


    def test_invalid_upstream_port_is_rejected_at_startup(self):
        with patch.dict(os.environ, {
            "TOKENSAVER_UPSTREAM_BASE_URL": "https://api.openai.com:99999",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "port"):
                load_config()


    def test_remote_mode_without_auth_token_is_rejected_at_startup(self):
        with patch.dict(os.environ, {
            "TOKENSAVER_ALLOW_REMOTE": "1",
            "TOKENSAVER_UPSTREAM_BASE_URL": "https://api.openai.com",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "TOKENSAVER_GATEWAY_TOKEN is required"):
                load_config()


if __name__ == "__main__":
    unittest.main()
