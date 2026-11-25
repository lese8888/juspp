import unittest

import translate_news


def article(url: str, content_policy: str | None = None) -> dict:
    item = {
        "url": url,
        "timestamp": 1795478400000,
        "categoryKey": "industry",
        "translations": {
            "zh-CN": {
                "title": "测试新闻",
                "summary": "测试摘要",
            }
        },
    }
    if content_policy:
        item["contentPolicy"] = content_policy
    return item


class RssContentPolicyTests(unittest.TestCase):
    def test_rss_marks_full_translation_and_digest(self):
        rss = translate_news.make_rss(
            [
                article("https://example.com/full", "full_l10n"),
                article("https://example.com/digest", "digest"),
            ],
            period_hours=24,
            site_url="https://example.com/",
        )

        self.assertIn("【完整译文】测试摘要", rss)
        self.assertIn("【内容摘要】测试摘要", rss)

    def test_rss_legacy_articles_default_to_full_translation(self):
        rss = translate_news.make_rss(
            [article("https://example.com/legacy")],
            period_hours=24,
            site_url="https://example.com/",
        )

        self.assertIn("【完整译文】测试摘要", rss)


if __name__ == "__main__":
    unittest.main()
