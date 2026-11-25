import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from translation.llm import LLMRequestError, OpenAICompatibleClient


def completion_response(content: str = '{"title":"标题"}') -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
        ensure_ascii=False,
    ).encode("utf-8")


class ServerFixture:
    """启动本地线程 HTTP 服务，测试不访问外部网络。"""

    def __init__(self, handler_type):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.server

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()


class LLMFaultTests(unittest.TestCase):
    def test_429_retries_then_succeeds(self):
        attempts = 0

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                nonlocal attempts
                attempts += 1
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if attempts < 3:
                    payload = b'{"error":"rate limited"}'
                    self.send_response(429)
                else:
                    payload = completion_response()
                    self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        with mock.patch("translation.llm.time.sleep"):
            with ServerFixture(Handler) as server:
                client = OpenAICompatibleClient(
                    api_key="test",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="test",
                    max_retries=3,
                )
                result, _ = client.complete_json(
                    system_prompt="system",
                    user_prompt="user",
                )
        self.assertEqual(result["title"], "标题")
        self.assertEqual(attempts, 3)

    def test_5xx_exhausts_retry_budget(self):
        attempts = 0

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                nonlocal attempts
                attempts += 1
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = b'{"error":"upstream"}'
                self.send_response(503)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        with mock.patch("translation.llm.time.sleep"):
            with ServerFixture(Handler) as server:
                client = OpenAICompatibleClient(
                    api_key="test",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="test",
                    max_retries=2,
                )
                with self.assertRaises(LLMRequestError) as context:
                    client.complete_json(system_prompt="system", user_prompt="user")
        self.assertTrue(context.exception.retryable)
        self.assertEqual(attempts, 2)

    def test_empty_model_content_is_not_success(self):
        attempts = 0

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                nonlocal attempts
                attempts += 1
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "",
                                    "reasoning_content": "仅有推理",
                                },
                                "finish_reason": "length",
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        with mock.patch("translation.llm.time.sleep"):
            with ServerFixture(Handler) as server:
                client = OpenAICompatibleClient(
                    api_key="test",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="test",
                    max_retries=2,
                )
                with self.assertRaisesRegex(LLMRequestError, "内容为空"):
                    client.complete_json(system_prompt="system", user_prompt="user")
        self.assertEqual(attempts, 2)

    def test_request_timeout_is_retryable(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                time.sleep(0.2)
                payload = completion_response()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionAbortedError):
                    return

            def log_message(self, format, *args):
                return

        with ServerFixture(Handler) as server:
            client = OpenAICompatibleClient(
                api_key="test",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="test",
                timeout_seconds=0.05,
                max_retries=1,
            )
            with self.assertRaises(LLMRequestError) as context:
                client.complete_json(system_prompt="system", user_prompt="user")
        self.assertTrue(context.exception.retryable)

    def test_invalid_json_retries_once_then_fails(self):
        attempts = 0

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                nonlocal attempts
                attempts += 1
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = completion_response("not-json")
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        with mock.patch("translation.llm.time.sleep"):
            with ServerFixture(Handler) as server:
                client = OpenAICompatibleClient(
                    api_key="test",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="test",
                    max_retries=1,
                )
                with self.assertRaises(LLMRequestError) as context:
                    client.complete_json(system_prompt="system", user_prompt="user")
        self.assertTrue(context.exception.retryable)
        self.assertEqual(attempts, 2)


if __name__ == "__main__":
    unittest.main()
