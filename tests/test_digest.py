import unittest

from translation.digest import (
    DigestConfig,
    DigestGenerationError,
    generate_digest,
    trim_digest_content,
)
from translation.glossary import load_glossary
from translation.llm import ChatUsage, LLMRequestError


def sample_article(body="PlayStation 5 reached 39 million players in 2026."):
    return {
        "originalTitle": "PlayStation 5 tops 39 million players",
        "originalSummary": "Sony reported a new PlayStation 5 milestone in 2026.",
        "originalContent": body,
        "sourceLanguage": "en",
        "url": "https://example.com/digest",
    }


def zh_content(sentences=12):
    sentence = "PlayStation 5 在 2026 年达到 3900 万玩家，平台团队确认了这一里程碑。"
    return "".join(sentence for _ in range(sentences))


class QueueClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, *, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if not self.responses:
            raise AssertionError("测试客户端没有更多响应")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class LongArticleClient:
    def __init__(self):
        self.calls = []
        self.map_count = 0

    def complete_json(self, *, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if "阅读以下新闻片段" in system_prompt:
            self.map_count += 1
            return (
                {"facts": f"事实{self.map_count}"},
                ChatUsage(input_tokens=1, output_tokens=1),
            )
        return (
            {
                "title": "PlayStation 5 达成 3900 万玩家",
                "summary": "平台迎来里程碑。",
                "content": zh_content(6),
            },
            ChatUsage(input_tokens=2, output_tokens=2),
        )


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.glossary = load_glossary()

    def test_short_article_uses_one_request_and_sums_usage(self):
        client = QueueClient(
            [
                (
                    {
                        "title": "PlayStation 5 玩家数达到 3900 万",
                        "summary": "索尼公布了 2026 年的新里程碑。",
                        "content": zh_content(6),
                    },
                    ChatUsage(input_tokens=10, output_tokens=20),
                )
            ]
        )

        result = generate_digest(
            client=client,
            article=sample_article(),
            language="zh-CN",
            glossary=self.glossary,
        )

        self.assertEqual(result.requests, 1)
        self.assertEqual(result.usage.input_tokens, 10)
        self.assertEqual(result.usage.output_tokens, 20)
        self.assertEqual(result.content_policy, "digest")
        self.assertFalse(result.content_truncated)
        self.assertIn("3900", result.content)

    def test_long_article_uses_map_then_reduce_in_order(self):
        body = "\n\n".join(
            f"第 {index} 段包含 PlayStation 5 和 3900 万玩家的事实。"
            for index in range(8)
        )
        client = LongArticleClient()
        config = DigestConfig(
            direct_body_chars=100,
            chunk_chars=80,
            max_body_chars=1000,
        )

        result = generate_digest(
            client=client,
            article=sample_article(body),
            language="zh-CN",
            glossary=self.glossary,
            config=config,
        )

        self.assertGreaterEqual(result.requests, 3)
        self.assertEqual(result.requests, client.map_count + 1)
        self.assertEqual(result.usage.input_tokens, client.map_count * 1 + 2)
        self.assertEqual(result.usage.output_tokens, client.map_count * 1 + 2)
        self.assertIn("片段 1/", client.calls[0][1])
        self.assertIn("片段 2/", client.calls[1][1])
        self.assertIn("事实清单", client.calls[-1][1])
        first = client.calls[-1][1].find("事实1")
        second = client.calls[-1][1].find("事实2")
        self.assertGreaterEqual(first, 0)
        self.assertGreater(second, first)

    def test_cjk_content_is_trimmed_only_at_complete_sentence(self):
        content = ("第一段内容完整。" * 100) + "最后一句不应该保留。"
        trimmed, changed, warnings = trim_digest_content(
            content,
            language="zh-CN",
            cjk_max_chars=30,
        )
        self.assertTrue(changed)
        self.assertTrue(trimmed.endswith("。"))
        self.assertEqual(
            warnings,
            ["content_trimmed_at_sentence_boundary"],
        )

    def test_cjk_without_sentence_boundary_is_not_cut_mid_sentence(self):
        content = "没" * 60
        trimmed, changed, warnings = trim_digest_content(
            content,
            language="zh-CN",
            cjk_max_chars=30,
        )
        self.assertEqual(trimmed, content)
        self.assertFalse(changed)
        self.assertEqual(
            warnings,
            ["content_over_limit_without_sentence_boundary"],
        )

    def test_english_content_is_trimmed_at_sentence_boundary(self):
        content = " ".join(["This sentence has six words." for _ in range(60)])
        trimmed, changed, warnings = trim_digest_content(
            content,
            language="en",
            english_max_words=18,
        )
        self.assertTrue(changed)
        self.assertTrue(trimmed.endswith("."))
        self.assertEqual(
            warnings,
            ["content_trimmed_at_sentence_boundary"],
        )

    def test_oversized_source_body_is_bounded_and_warned(self):
        body = "甲" * 500
        client = QueueClient(
            [
                (
                    {
                        "title": "标题",
                        "summary": "摘要",
                        "content": zh_content(6),
                    },
                    ChatUsage(input_tokens=1, output_tokens=1),
                )
            ]
        )
        config = DigestConfig(
            direct_body_chars=300,
            chunk_chars=100,
            max_body_chars=200,
        )
        result = generate_digest(
            client=client,
            article=sample_article(body),
            language="zh-CN",
            glossary=self.glossary,
            config=config,
        )
        self.assertTrue(result.body_truncated)
        self.assertIn("source_body_truncated", result.warnings)

    def test_empty_response_field_fails_instead_of_returning_partial_result(self):
        client = QueueClient(
            [
                (
                    {
                        "title": "标题",
                        "summary": "",
                        "content": zh_content(6),
                    },
                    ChatUsage(),
                )
            ]
        )
        with self.assertRaisesRegex(DigestGenerationError, "summary"):
            generate_digest(
                client=client,
                article=sample_article(),
                language="zh-CN",
                glossary=self.glossary,
            )

    def test_model_exception_is_wrapped_and_not_swallowed(self):
        client = QueueClient(
            [LLMRequestError("服务不可用", retryable=True)]
        )
        with self.assertRaisesRegex(
            DigestGenerationError,
            "stage=direct",
        ):
            generate_digest(
                client=client,
                article=sample_article(),
                language="zh-CN",
                glossary=self.glossary,
            )

    def test_map_stage_cannot_return_empty_facts(self):
        body = "\n\n".join(["长文片段" * 60] * 3)
        client = QueueClient(
            [({"facts": ""}, ChatUsage(input_tokens=1, output_tokens=1))]
        )
        config = DigestConfig(
            direct_body_chars=100,
            chunk_chars=80,
            max_body_chars=1000,
        )
        with self.assertRaisesRegex(DigestGenerationError, "空事实清单"):
            generate_digest(
                client=client,
                article=sample_article(body),
                language="zh-CN",
                glossary=self.glossary,
                config=config,
            )

    def test_result_rejects_non_digest_policy_impersonation_by_contract(self):
        client = QueueClient(
            [
                (
                    {
                        "title": "标题",
                        "summary": "摘要",
                        "content": zh_content(6),
                    },
                    ChatUsage(),
                )
            ]
        )
        result = generate_digest(
            client=client,
            article=sample_article(),
            language="zh-CN",
            glossary=self.glossary,
        )
        self.assertEqual(result.content_policy, "digest")
        self.assertNotEqual(result.content_policy, "full_l10n")


if __name__ == "__main__":
    unittest.main()
