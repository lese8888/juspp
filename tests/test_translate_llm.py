import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import translate_news
from translation.engine import LLMEngineConfig, LLMProductionEngine, ProductionEngineResult
from translation.protected_articles import article_sha256
from translation.shadow import article_id


def sample_article(url: str, title: str) -> dict:
    """构造满足生产输入契约的英文文章。"""

    return {
        "originalTitle": title,
        "originalSummary": "PlayStation and Switch update for players.",
        "originalContent": (
            "PlayStation and Switch update for players.\n\n"
            "The update is available now."
        ),
        "url": url,
        "publishedAt": "2026-11-24T00:00:00.000Z",
        "timestamp": 1795478400000,
        "source": "Example",
        "sourceKey": "example",
        "sourceLanguage": "en",
        "sourceTypeKey": "media",
        "categoryKey": "industry",
        "heatScore": 1795478400000,
    }


def sample_report(url: str = "https://example.com/news", title: str = "Update") -> dict:
    return {
        "generatedAt": "2026-11-24T00:00:00.000Z",
        "timezone": "Asia/Hong_Kong",
        "periodHours": 24,
        "sourceCount": 1,
        "articles": [sample_article(url, title)],
    }


class SameTextService:
    def prepare(self):
        return None

    def translate(self, text, target_language):
        return text


class FakeLLMEngine:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.received_urls = []

    def translate(self, articles):
        if self.fail:
            raise RuntimeError("模拟 LLM 失败")
        self.received_urls = [article["url"] for article in articles]
        translated = translate_news.translate_normalized_articles(
            articles,
            SameTextService(),
        )
        for article in translated:
            article["contentPolicy"] = "full_l10n"
            article["contentPolicyReason"] = "normal_length"
            article["id"] = article_id(article)
        return ProductionEngineResult(
            articles=tuple(translated),
            metadata={
                "engine": "llm",
                "model": "test-model",
                "articlesGenerated": len(translated),
                "requests": 1,
                "tokens": {"input": 1, "output": 1, "estimated": False},
                "estimatedCostCny": 0.01,
            },
        )


def write_manifest(root: Path, frozen_article: dict) -> Path:
    key = f"url:{frozen_article['url']}"
    manifest = {
        "protectedArticleCount": 1,
        "orderedArticleKeys": [key],
        "articleHashes": {key: article_sha256(frozen_article)},
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def write_fixture(root: Path, existing_article: dict) -> tuple[Path, Path]:
    existing = {
        "schemaVersion": 2,
        "supportedLanguages": list(translate_news.SUPPORTED_LANGUAGES),
        "defaultLanguage": "zh-CN",
        "generatedAt": "2026-11-24T00:00:00.000Z",
        "timezone": "Asia/Hong_Kong",
        "periodHours": 24,
        "sourceCount": 1,
        "translationEngine": "Argos Translate + OpenCC",
        "articles": [existing_article],
    }
    existing_path = root / "existing.json"
    existing_path.write_text(
        json.dumps(existing, ensure_ascii=False),
        encoding="utf-8",
    )
    rss_path = root / "rss.xml"
    rss_path.write_text("旧 RSS", encoding="utf-8")
    return existing_path, rss_path


class LLMProductionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.frozen_final = translate_news.translate_normalized_articles(
            [translate_news.normalize_article(sample_article(
                "https://example.com/frozen",
                "Frozen story",
            ))],
            SameTextService(),
        )[0]

    def test_default_engine_remains_argos(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRANSLATION_ENGINE", None)
            self.assertEqual(
                translate_news.parse_args([]).translation_engine,
                "argos",
            )

    def test_llm_appends_only_new_article_and_preserves_frozen_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing_path, rss_path = write_fixture(
                root,
                self.frozen_final,
            )
            manifest = write_manifest(root, self.frozen_final)
            input_path = root / "input.json"
            report = sample_report("https://example.com/new", "New story")
            input_path.write_text(json.dumps(report), encoding="utf-8")
            engine = FakeLLMEngine()

            translate_news.main(
                news_file=input_path,
                rss_file=rss_path,
                translation_engine="llm",
                baseline_news_file=existing_path,
                manifest_path=manifest,
                llm_engine=engine,
            )

            output = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertEqual(len(output["articles"]), 2)
            self.assertEqual(output["articles"][0], self.frozen_final)
            self.assertEqual(
                output["articles"][1]["url"],
                "https://example.com/new",
            )
            self.assertEqual(engine.received_urls, ["https://example.com/new"])
            self.assertIn("LLM / test-model", output["translationEngine"])
            self.assertIn("translation", output)
            self.assertIn("New story", rss_path.read_text(encoding="utf-8"))

    def test_llm_failure_does_not_write_news_or_rss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing_path, rss_path = write_fixture(
                root,
                self.frozen_final,
            )
            manifest = write_manifest(root, self.frozen_final)
            input_path = root / "input.json"
            input_path.write_text(
                json.dumps(sample_report("https://example.com/new")),
                encoding="utf-8",
            )
            before_news = input_path.read_bytes()
            before_rss = rss_path.read_bytes()

            with self.assertRaisesRegex(RuntimeError, "模拟 LLM 失败"):
                translate_news.main(
                    news_file=input_path,
                    rss_file=rss_path,
                    translation_engine="llm",
                    baseline_news_file=existing_path,
                    manifest_path=manifest,
                    llm_engine=FakeLLMEngine(fail=True),
                )

            self.assertEqual(input_path.read_bytes(), before_news)
            self.assertEqual(rss_path.read_bytes(), before_rss)

    def test_llm_requires_a_baseline_to_protect_frozen_articles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            rss_path = root / "rss.xml"
            input_path.write_text(
                json.dumps(sample_report()),
                encoding="utf-8",
            )
            rss_path.write_text("旧 RSS", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "BASELINE_NEWS_FILE"):
                translate_news.main(
                    news_file=input_path,
                    rss_file=rss_path,
                    translation_engine="llm",
                    llm_engine=FakeLLMEngine(),
                )

    def test_frozen_change_fails_before_output_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tampered = copy.deepcopy(self.frozen_final)
            tampered["originalTitle"] = "Tampered"
            existing_path, rss_path = write_fixture(root, tampered)
            manifest = write_manifest(root, self.frozen_final)
            input_path = root / "input.json"
            input_path.write_text(
                json.dumps(sample_report("https://example.com/new")),
                encoding="utf-8",
            )
            before_news = input_path.read_bytes()

            with self.assertRaisesRegex(
                ValueError,
                "冻结文章内容发生变化",
            ):
                translate_news.main(
                    news_file=input_path,
                    rss_file=rss_path,
                    translation_engine="llm",
                    baseline_news_file=existing_path,
                    manifest_path=manifest,
                    llm_engine=FakeLLMEngine(),
                )

            self.assertEqual(input_path.read_bytes(), before_news)

    def test_argos_can_append_with_the_same_freeze_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing_path, rss_path = write_fixture(
                root,
                self.frozen_final,
            )
            manifest = write_manifest(root, self.frozen_final)
            input_path = root / "input.json"
            input_path.write_text(
                json.dumps(sample_report("https://example.com/new")),
                encoding="utf-8",
            )

            translate_news.main(
                news_file=input_path,
                rss_file=rss_path,
                service=SameTextService(),
                translation_engine="argos",
                baseline_news_file=existing_path,
                manifest_path=manifest,
            )

            output = json.loads(input_path.read_text(encoding="utf-8"))
            self.assertEqual(len(output["articles"]), 2)
            self.assertEqual(output["articles"][0], self.frozen_final)
            self.assertEqual(
                output["translationEngine"],
                "Argos Translate + OpenCC",
            )

    def test_llm_engine_adapter_uses_mixed_pipeline_and_restores_original_content(self):
        source = translate_news.normalize_article(
            sample_article("https://example.com/new", "New story")
        )

        def fake_mixed(config, *, client):
            generated = {
                "schemaVersion": 3,
                "articles": [
                    {
                        "id": article_id(source),
                        "url": source["url"],
                        "source": source["source"],
                        "sourceKey": source["sourceKey"],
                        "sourceTypeKey": source["sourceTypeKey"],
                        "categoryKey": source["categoryKey"],
                        "sourceLanguage": "en",
                        "publishedAt": source["publishedAt"],
                        "timestamp": source["timestamp"],
                        "heatScore": source["heatScore"],
                        "contentPolicy": "full_l10n",
                        "contentPolicyReason": "normal_length",
                        "originalTitle": source["originalTitle"],
                        "originalSummary": source["originalSummary"],
                        "translations": {
                            "zh-CN": {
                                "title": "新消息",
                                "summary": "摘要",
                                "content": "正文",
                            }
                        },
                        "translationInfo": {},
                        "partialShadow": False,
                        "plannedRouting": {},
                        "actualRouting": {},
                    }
                ],
            }
            config.output_dir.mkdir(parents=True, exist_ok=True)
            (config.output_dir / "news.shadow.json").write_text(
                json.dumps(generated, ensure_ascii=False),
                encoding="utf-8",
            )
            return {
                "status": "passed",
                "model": "test-model",
                "generatedArticles": 1,
                "requests": 1,
                "tokens": {"input": 1, "output": 1, "estimated": False},
                "estimatedCostCny": 0.01,
                "durationSeconds": 0.1,
                "contentRoutingVersion": "mixed-language-5000-2000-v1",
                "plannedPolicyCounts": {"full_l10n": 1, "digest": 0},
                "actualPolicyCounts": {"full_l10n": 1, "digest": 0},
                "cache": {"hits": 0, "misses": 1},
                "budget": {},
                "warnings": [],
            }

        engine = LLMProductionEngine(
            client=SimpleNamespace(model="test-model", max_tokens=1000),
            config=LLMEngineConfig(),
            source_document={"generatedAt": "2026-11-24T00:00:00.000Z"},
        )
        with patch("translation.engine.run_mixed_shadow", fake_mixed):
            result = engine.translate([source])

        self.assertEqual(len(result.articles), 1)
        self.assertEqual(
            result.articles[0]["originalContent"],
            source["originalContent"],
        )
        self.assertNotIn("partialShadow", result.articles[0])
        self.assertEqual(result.metadata["model"], "test-model")


if __name__ == "__main__":
    unittest.main()
