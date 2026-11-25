import unittest

from translation.full_l10n import (
    FullL10nConfig,
    FullL10nGenerationError,
    generate_full_l10n,
)
from translation.glossary import load_glossary
from translation.llm import ChatUsage, LLMRequestError


def article(body):
    return {
        "originalTitle": "PlayStation 5 reaches 39 million players",
        "originalSummary": "Sony announced a new milestone in 2026.",
        "originalContent": body,
        "sourceLanguage": "en",
        "url": "https://example.com/full",
    }


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


class FullL10nTests(unittest.TestCase):
    def setUp(self):
        self.glossary = load_glossary()

    def test_short_article_uses_one_complete_translation_request(self):
        body = "PlayStation 5 reached 39 million players in 2026. " * 8
        client = QueueClient(
            [
                (
                    {
                        "title": "PlayStation 5 达到 3900 万玩家",
                        "summary": "索尼在 2026 年宣布新的里程碑。",
                        "content": body,
                    },
                    ChatUsage(input_tokens=10, output_tokens=20),
                )
            ]
        )
        result = generate_full_l10n(
            client=client,
            article=article(body),
            language="zh-CN",
            glossary=self.glossary,
            config=FullL10nConfig(
                min_segment_chars=100,
                max_segment_chars=1000,
            ),
        )
        self.assertEqual(result.requests, 1)
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.content, body.strip())
        self.assertEqual(result.content_policy, "full_l10n")
        self.assertEqual(result.usage.input_tokens, 10)
        self.assertIn("不得概括", client.calls[0][0])

    def test_long_article_is_translated_by_segments_in_order(self):
        first = "第一段完整事实。" * 20
        second = "第二段完整事实。" * 20
        third = "第三段完整事实。" * 20
        body = "\n\n".join((first, second, third))
        client = QueueClient(
            [
                (
                    {
                        "title": "完整标题",
                        "summary": "完整来源摘要",
                    },
                    ChatUsage(input_tokens=1, output_tokens=2),
                ),
                ({"content": "译一"}, ChatUsage(input_tokens=3, output_tokens=4)),
                ({"content": "译二"}, ChatUsage(input_tokens=5, output_tokens=6)),
                ({"content": "译三"}, ChatUsage(input_tokens=7, output_tokens=8)),
            ]
        )
        result = generate_full_l10n(
            client=client,
            article=article(body),
            language="zh-CN",
            glossary=self.glossary,
            config=FullL10nConfig(
                min_segment_chars=200,
                max_segment_chars=300,
            ),
        )
        self.assertEqual(result.requests, 4)
        self.assertEqual(result.title, "完整标题")
        self.assertEqual(result.summary, "完整来源摘要")
        self.assertEqual(result.content, "译一\n\n译二\n\n译三")
        self.assertEqual(
            [segment.index for segment in result.segments],
            [0, 1, 2],
        )
        self.assertEqual(result.usage.input_tokens, 16)
        self.assertEqual(result.usage.output_tokens, 20)
        self.assertIn('"片段序号": 1', client.calls[1][1])
        self.assertIn('"片段序号": 3', client.calls[3][1])

    def test_middle_segment_failure_aborts_whole_article(self):
        body = "\n\n".join(["甲" * 90, "乙" * 90, "丙" * 90])
        client = QueueClient(
            [
                (
                    {"title": "标题", "summary": "摘要"},
                    ChatUsage(),
                ),
                ({"content": "第一段"}, ChatUsage()),
                LLMRequestError("服务失败", retryable=True),
            ]
        )
        with self.assertRaisesRegex(
            FullL10nGenerationError,
            "segment\\[2/3\\]",
        ):
            generate_full_l10n(
                client=client,
                article=article(body),
                language="zh-CN",
                glossary=self.glossary,
                config=FullL10nConfig(
                    min_segment_chars=80,
                    max_segment_chars=100,
                ),
            )

    def test_empty_direct_field_is_rejected(self):
        client = QueueClient(
            [
                (
                    {"title": "标题", "summary": "", "content": "正文"},
                    ChatUsage(),
                )
            ]
        )
        with self.assertRaisesRegex(FullL10nGenerationError, "summary"):
            generate_full_l10n(
                client=client,
                article=article("短正文"),
                language="ja",
                glossary=self.glossary,
            )

    def test_empty_segment_is_rejected_instead_of_returning_partial_text(self):
        body = "\n\n".join(["甲" * 90, "乙" * 90])
        client = QueueClient(
            [
                (
                    {"title": "标题", "summary": "摘要"},
                    ChatUsage(),
                ),
                ({"content": "第一段"}, ChatUsage()),
                ({"content": ""}, ChatUsage()),
            ]
        )
        with self.assertRaisesRegex(FullL10nGenerationError, "content"):
            generate_full_l10n(
                client=client,
                article=article(body),
                language="ko",
                glossary=self.glossary,
                config=FullL10nConfig(
                    min_segment_chars=80,
                    max_segment_chars=100,
                ),
            )

    def test_metadata_and_segments_cover_the_entire_source(self):
        body = "正文。" * 180
        segments = []
        segmented = __import__(
            "translation.segmentation",
            fromlist=["segment_text"],
        ).segment_text(
            body,
            min_chars=100,
            max_chars=200,
        )
        for item in segmented.segments:
            segments.append(({"content": f"译文{item.index}"}, ChatUsage()))
        client = QueueClient(
            [
                ({"title": "标题", "summary": "摘要"}, ChatUsage()),
                *segments,
            ]
        )
        result = generate_full_l10n(
            client=client,
            article=article(body),
            language="en",
            glossary=self.glossary,
            config=FullL10nConfig(
                min_segment_chars=100,
                max_segment_chars=200,
            ),
        )
        self.assertEqual(result.source_char_count, len(body))
        self.assertEqual(result.segments[0].start, 0)
        self.assertEqual(result.segments[-1].end, len(body))
        for previous, current in zip(result.segments, result.segments[1:]):
            self.assertEqual(previous.end, current.start)

    def test_unsupported_target_language_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支持"):
            generate_full_l10n(
                client=QueueClient([]),
                article=article("正文"),
                language="zh-TW",
                glossary=self.glossary,
            )

    def test_empty_article_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能为空"):
            generate_full_l10n(
                client=QueueClient([]),
                article=article(""),
                language="zh-CN",
                glossary=self.glossary,
            )


if __name__ == "__main__":
    unittest.main()
