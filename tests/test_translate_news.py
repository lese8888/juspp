import copy
import json
import tempfile
import unittest
from pathlib import Path

import translate_news


def sample_report():
    return {
        "generatedAt": "2026-11-24T00:00:00.000Z",
        "timezone": "Asia/Hong_Kong",
        "periodHours": 24,
        "sourceCount": 1,
        "articles": [
            {
                "originalTitle": "PlayStation and Switch update",
                "originalSummary": "Xbox and Steam add a complete summary for players.",
                "originalContent": "Xbox and Steam add a complete summary for players.\n\nThe update is available now.",
                "url": "https://example.com/news",
                "publishedAt": "2026-11-24T00:00:00.000Z",
                "timestamp": 1795478400000,
                "source": "Example",
                "sourceKey": "example",
                "sourceLanguage": "en",
                "sourceTypeKey": "media",
                "categoryKey": "industry",
                "heatScore": 1795478400000,
            }
        ],
    }


class SameTextService:
    def __init__(self):
        self.prepared = False

    def prepare(self):
        self.prepared = True

    def translate(self, text, target_language):
        return text


class EmptySummaryService(SameTextService):
    def translate(self, text, target_language):
        return "" if "summary" in text.lower() else text


class MissingModelService:
    def prepare(self):
        raise RuntimeError("翻译模型缺失")

    def translate(self, text, target_language):
        raise AssertionError("准备阶段失败后不应继续翻译")


class TranslationTests(unittest.TestCase):
    def test_glossary_round_trip_preserves_brand_names(self):
        protected, terms = translate_news.protect_glossary(
            "PlayStation 5 and Nintendo Switch 2 use Steam and AI."
        )
        self.assertNotIn("PlayStation 5", protected)
        restored = translate_news.restore_glossary(protected, terms)
        self.assertIn("PlayStation 5", restored)
        self.assertIn("Nintendo Switch 2", restored)
        self.assertIn("Steam", restored)
        self.assertIn("AI", restored)

    def test_source_cleanup_removes_markup_entities_and_rss_footer(self):
        cleaned = translate_news.clean_source_text(
            "<p>Tom &amp; Jerry&hellip;</p> Read the full article on example.com"
        )
        self.assertEqual(cleaned, "Tom & Jerry…")

    def test_unmatched_marker_brackets_are_removed(self):
        self.assertEqual(
            translate_news.remove_unmatched_square_brackets("性能提高]"),
            "性能提高",
        )

    def test_marker_adjacent_to_chinese_is_detected(self):
        self.assertTrue(
            translate_news.has_unrestored_marker("新闻QZABCDEF消息")
        )

    def test_hyphenated_marker_is_restored(self):
        protected, terms = translate_news.protect_glossary("PlayStation and Xbox")
        token, canonical = terms[0]
        corrupted = protected.replace(token, f"{token[:4]}-{token[4:]}")
        restored = translate_news.restore_glossary(corrupted, terms)
        self.assertIn(canonical, restored)
        self.assertNotIn(token[:4], restored)
        self.assertFalse(translate_news.has_unrestored_marker(restored))

    def test_normal_numeric_brackets_are_allowed(self):
        self.assertFalse(
            translate_news.has_unrestored_marker("引用编号 [[123456]]")
        )

    def test_corrupted_marker_falls_back_to_segmented_translation(self):
        service = translate_news.OfflineTranslationService(
            model_translator=lambda text, target: text.replace("QZ", "XX"),
            zh_tw_converter=lambda text: text,
        )
        translated = service.translate("Xbox and Steam work together.", "zh-CN")
        self.assertTrue(translated.startswith("Xbox and Steam work together"))
        self.assertNotIn("QZ", translated)
        self.assertNotIn("XX", translated)

    def test_translate_articles_builds_all_five_languages(self):
        translated = translate_news.translate_articles(
            sample_report(), SameTextService()
        )
        translations = translated[0]["translations"]
        self.assertEqual(
            list(translations.keys()), ["zh-CN", "zh-TW", "en", "ja", "ko"]
        )
        self.assertEqual(
            set(translations["zh-CN"].keys()), {"title", "summary", "content"}
        )
        self.assertIn("The update is available now", translated[0]["originalContent"])
        self.assertIn("The update is available now", translations["en"]["content"])
        self.assertNotIn("videoScript", translated[0])
        self.assertNotIn("translationStatus", translated[0])

    def test_missing_translation_model_keeps_existing_outputs(self):
        report = sample_report()
        with tempfile.TemporaryDirectory() as directory:
            news_path = Path(directory) / "news.json"
            rss_path = Path(directory) / "rss.xml"
            news_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            rss_path.write_text("有效旧 RSS", encoding="utf-8")
            before_news = news_path.read_bytes()
            before_rss = rss_path.read_bytes()

            with self.assertRaises(RuntimeError):
                translate_news.main(
                    news_file=news_path,
                    rss_file=rss_path,
                    service=MissingModelService(),
                )

            self.assertEqual(news_path.read_bytes(), before_news)
            self.assertEqual(rss_path.read_bytes(), before_rss)

    def test_empty_summary_fails_without_partial_output(self):
        report = sample_report()
        report["articles"][0]["originalSummary"] = ""
        with tempfile.TemporaryDirectory() as directory:
            news_path = Path(directory) / "news.json"
            rss_path = Path(directory) / "rss.xml"
            news_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            rss_path.write_text("有效旧 RSS", encoding="utf-8")
            before_news = news_path.read_bytes()
            before_rss = rss_path.read_bytes()

            with self.assertRaises(ValueError):
                translate_news.main(
                    news_file=news_path,
                    rss_file=rss_path,
                    service=SameTextService(),
                )

            self.assertEqual(news_path.read_bytes(), before_news)
            self.assertEqual(rss_path.read_bytes(), before_rss)

    def test_article_objects_are_not_mutated(self):
        report = sample_report()
        original = copy.deepcopy(report)
        translate_news.translate_articles(report, SameTextService())
        self.assertEqual(report, original)

    def test_legacy_title_and_summary_are_migrated(self):
        legacy = sample_report()
        article = legacy["articles"][0]
        article["title"] = article.pop("originalTitle")
        article["summary"] = article.pop("originalSummary")
        for field in (
            "sourceKey",
            "sourceLanguage",
            "sourceTypeKey",
            "categoryKey",
            "heatScore",
        ):
            article.pop(field)
        translated = translate_news.translate_articles(
            legacy, SameTextService()
        )
        self.assertEqual(
            translated[0]["originalTitle"], "PlayStation and Switch update"
        )
        self.assertEqual(translated[0]["sourceKey"], "example")
        self.assertEqual(translated[0]["sourceLanguage"], "en")


if __name__ == "__main__":
    unittest.main()
