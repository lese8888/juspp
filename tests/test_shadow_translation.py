import copy
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from translation.glossary import load_glossary
from translation.shadow import (
    GenerationResult,
    ShadowConfig,
    extract_numbers,
    run_shadow,
    select_articles,
    validate_result,
)
from translation.llm import ChatUsage, LLMRequestError, OpenAICompatibleClient


def sample_article(source_key="example", title="PlayStation 5 tops 39 million players"):
    return {
        "originalTitle": title,
        "originalSummary": "PlayStation 5 reached 39 million players in 2026.",
        "originalContent": (
            "PlayStation 5 reached 39 million players in 2026. "
            "The platform team said the milestone came after a major release."
        ),
        "source": source_key.title(),
        "sourceKey": source_key,
        "sourceLanguage": "en",
        "sourceTypeKey": "media",
        "categoryKey": "playstation",
        "url": f"https://example.com/{source_key}",
        "publishedAt": "2026-11-24T00:00:00Z",
        "timestamp": 1795478400000,
        "heatScore": 1795478400000,
    }


def sample_report(count=4):
    return {
        "generatedAt": "2026-11-24T00:00:00Z",
        "timezone": "Asia/Hong_Kong",
        "periodHours": 24,
        "sourceCount": 2,
        "articles": [
            sample_article("alpha" if index % 2 == 0 else "beta", f"Story {index}")
            for index in range(count)
        ],
    }


class ShadowTranslationTests(unittest.TestCase):
    def test_openai_client_parses_json_and_usage(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "```json\n{\"title\":\"标题\",\"summary\":\"摘要\",\"content\":\"正文\"}\n```"
                                }
                            }
                        ],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OpenAICompatibleClient(
                api_key="test-key",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="test-model",
            )
            result, usage = client.complete_json(
                system_prompt="system",
                user_prompt="user",
            )
            self.assertEqual(result["title"], "标题")
            self.assertEqual(usage.input_tokens, 12)
            self.assertEqual(usage.output_tokens, 8)
            self.assertFalse(usage.estimated)
        finally:
            server.shutdown()
            server.server_close()

    def test_openai_client_does_not_retry_authentication_failure(self):
        attempts = 0

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                nonlocal attempts
                attempts += 1
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = b'{"error":"unauthorized"}'
                self.send_response(401)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OpenAICompatibleClient(
                api_key="bad-key",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="test-model",
                max_retries=3,
            )
            with self.assertRaises(LLMRequestError) as context:
                client.complete_json(system_prompt="system", user_prompt="user")
            self.assertFalse(context.exception.retryable)
            self.assertEqual(attempts, 1)
        finally:
            server.shutdown()
            server.server_close()

    def test_round_robin_sample_covers_sources(self):
        selected = select_articles(sample_report(6)["articles"], 4)
        self.assertEqual(len(selected), 4)
        self.assertEqual(
            [item["sourceKey"] for item in selected],
            ["alpha", "beta", "alpha", "beta"],
        )

    def test_number_normalization_handles_million_and_wan(self):
        self.assertIn("39000000", extract_numbers("39 million"))
        self.assertIn("39000000", extract_numbers("3900 万"))
        self.assertIn("11", extract_numbers("November 22, 2026"))

    def test_missing_key_number_fails_gate(self):
        article = sample_article()
        result = GenerationResult(
            title="PlayStation 5 新闻",
            summary="这是用于测试的摘要，但是故意省略了关键数字。",
            content=(
                "这是使用中文撰写的测试内容，用来检查质量闸门能否发现数字缺失问题。"
                "测试文本包含足够的汉字，以便通过语言比例检查，但不会包含来源中的数量。"
                "其余句子继续描述影子任务的结构检查、文件隔离和报告输出，确保长度达到测试要求。"
                "这段内容不用于正式新闻，也不会覆盖生产数据或触发页面发布，仅用于单元测试。"
                "通过这种方式可以直接确认关键数字缺失会被明确标记，而不是静默通过。"
                "测试还需要覆盖术语、格式、语言和数字等多个维度，便于后续扩展。"
                "当前用例只关注数字缺失，其他检查应保持正常并返回可读结果。"
                "如果闸门实现正确，这个样本应当包含失败原因并阻止候选数据被视为合格。"
                "系统不应自动修改来源数字，也不应猜测缺失的数量，而应要求重新生成。"
                "最终的测试结果只存在于临时目录，不会影响仓库中的正式文件。"
            ),
            usage=ChatUsage(),
            requests=1,
        )
        failures, _ = validate_result(
            article=article,
            language="zh-CN",
            result=result,
            glossary=load_glossary(),
        )
        self.assertTrue(any("关键数字缺失" in failure for failure in failures))

    def test_mock_shadow_never_modifies_input(self):
        report = sample_report(3)
        original = copy.deepcopy(report)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = root / "news.json"
            input_file.write_text(
                json.dumps(report, ensure_ascii=False),
                encoding="utf-8",
            )
            before = input_file.read_bytes()
            config = ShadowConfig(
                input_file=input_file,
                output_dir=root / "shadow",
                limit=3,
                engine="mock",
            )
            result = run_shadow(config, zh_tw_converter=lambda value: value)
            self.assertEqual(input_file.read_bytes(), before)
            self.assertTrue((root / "shadow" / "news.shadow.json").is_file())
            self.assertTrue((root / "shadow" / "translation-report.json").is_file())
            self.assertFalse(result["productionFilesModified"])
            self.assertEqual(report, original)


if __name__ == "__main__":
    unittest.main()
