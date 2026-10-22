import http.client
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from tokensaver.proxy import TokenSaverProxyHandler


class EchoHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        raw = json.dumps({"received": json.loads(body)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class GatewaySmokeTests(unittest.TestCase):
    def test_gateway_compacts_before_forwarding(self):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        gateway = ThreadingHTTPServer(("127.0.0.1", 0), TokenSaverProxyHandler)
        gateway.stats_lock = threading.Lock()
        gateway.stats = {"requests": 0, "compressed_requests": 0, "chars_saved": 0, "upstream_errors": 0}
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        try:
            env = {
                "TOKENSAVER_UPSTREAM_BASE_URL": f"http://127.0.0.1:{upstream.server_port}",
                "TOKENSAVER_ENABLED": "1",
                "TOKENSAVER_KEEP_RECENT_MESSAGES": "2",
                "TOKENSAVER_SUMMARY_MAX_CHARS": "500",
                "TOKENSAVER_MIN_OLD_CHARS": "100",
            }
            with patch.dict(os.environ, env, clear=False):
                body = {"model": "test", "messages": [
                    {"role": "user", "content": f"Old turn {i}. " + ("background " * 80)} for i in range(7)
                ]}
                conn = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
                encoded = json.dumps(body).encode()
                conn.request("POST", "/v1/chat/completions", body=encoded, headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                result = json.loads(response.read())
                conn.close()
            sent = result["received"]["messages"]
            self.assertTrue(any("compacted earlier conversation" in str(m.get("content", "")) for m in sent))
            self.assertIn("Old turn 6", str(sent[-1]))
        finally:
            gateway.shutdown()
            upstream.shutdown()
            gateway.server_close()
            upstream.server_close()


if __name__ == "__main__":
    unittest.main()
