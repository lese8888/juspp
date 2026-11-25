import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from translation.llm import ChatUsage
from translation.mixed_shadow import (
    Candidate,
    MixedShadowConfig,
    run_mixed_shadow,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def short_article():
    return {
        "url": "https://example.com/full",
        "source": "Example",
        "sourceKey": "example",
        "sourceLanguage": "en",
        "originalTitle": "Switch 2 reaches 5 million players",
        "originalSummary": "Nintendo announced the milestone in 2026.",
        "originalContent": (
            "Nintendo Switch 2 reached 5 million players in 2026. "
            "The platform team confirmed the milestone in a public update."
        ),
        "publishedAt": "2026-11-24T00:00:00Z",
        "timestamp": 1763942400000,
        "heatScore": 100,
    }


def long_article():
    article = short_article()
    article["url"] = "https://example.com/long"
    article["originalTitle"] = "Long feature reaches 5 million players"
    article["originalContent"] = " ".join(["word"] * 5001)
    return article


def document(articles):
    return {
        "generatedAt": "2026-11-24T00:00:00Z",
        "timezone": "Asia/Hong_Kong",
        "periodHours": 24,
        "articles": articles,
    }


def write_document(root: Path, articles) -> Path:
    path = root / "news.json"
    path.write_text(
        json.dumps(document(articles), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeLLM:
    model = "fake-model"

    def __init__(self, *, fail_language=None):
        self.fail_language = fail_language
        self.calls = []

    def complete_json(self, *, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if self.fail_language and f"目标语言：{self.fail_language}" in system_prompt:
            return {}, ChatUsage()
        return (
            {
                "title": "Nintendo Switch 2 在 2026 年达到 500 万玩家",
                "summary": "任天堂公布了 Switch 2 的新里程碑。",
                "content": (
                    "Nintendo Switch 2 在 2026 年达到 500 万玩家，"
                    "任天堂确认这一平台里程碑并公布了相关数据。"
                ),
            },
            ChatUsage(input_tokens=10, output_tokens=20),
        )


class NumberMetadataClient:
    model = "fake-model"

    def complete_json(self, *, system_prompt, user_prompt):
        return (
            {
                "title": "GTA 6 新消息",
                "summary": "2026 年报道提到 3900 万玩家。",
                "content": (
                    "GTA 6 的相关消息在 2026 年获得确认，报道同时提到 3900 万玩家，"
                    "内容保持完整并覆盖标题与来源摘要中的数字。"
                ),
            },
            ChatUsage(input_tokens=10, output_tokens=20),
        )


class MixedShadowTests(unittest.TestCase):
    def test_mock_mixed_route_generates_five_languages_and_keeps_input_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = write_document(root, [short_article(), long_article()])
            before = input_file.read_bytes()
            output_dir = root / "shadow"
            production_before = {
                name: sha256(PROJECT_ROOT / name)
                for name in ("news.json", "rss.xml")
            }

            report = run_mixed_shadow(
                MixedShadowConfig(
                    input_file=input_file,
                    output_dir=output_dir,
                    limit=2,
                    engine="mock",
                ),
                zh_tw_converter=lambda value: value,
            )

            self.assertEqual(report["plannedPolicyCounts"], {"full_l10n": 1, "digest": 1})
            self.assertEqual(report["actualPolicyCounts"], {"full_l10n": 1, "digest": 1})
            self.assertEqual(report["generatedArticles"], 2)
            self.assertEqual(report["policyMismatchCount"], 0)
            self.assertTrue(report["inputFileUnchanged"])
            self.assertFalse(report["productionFilesModified"])
            self.assertEqual(input_file.read_bytes(), before)
            self.assertEqual(
                production_before,
                {
                    name: sha256(PROJECT_ROOT / name)
                    for name in ("news.json", "rss.xml")
                },
            )

            shadow = json.loads(
                (output_dir / "news.shadow.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(shadow["articles"]), 2)
            for article in shadow["articles"]:
                self.assertEqual(
                    list(article["translations"]),
                    ["zh-CN", "zh-TW", "en", "ja", "ko"],
                )
                self.assertEqual(
                    article["contentPolicy"],
                    article["plannedRouting"]["contentPolicy"],
                )
                self.assertEqual(article["contentPolicy"], article["actualRouting"]["contentPolicy"])

    def test_partial_zh_cn_includes_opencc_style_zh_tw(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = write_document(root, [short_article()])
            report = run_mixed_shadow(
                MixedShadowConfig(
                    input_file=input_file,
                    output_dir=root / "shadow",
                    limit=1,
                    engine="mock",
                    language="zh-CN",
                ),
                zh_tw_converter=lambda value: f"TW:{value}",
            )
            shadow = json.loads(
                (root / "shadow" / "news.shadow.json").read_text(encoding="utf-8")
            )
            translations = shadow["articles"][0]["translations"]
            self.assertEqual(list(translations), ["zh-CN", "zh-TW"])
            self.assertTrue(translations["zh-TW"]["title"].startswith("TW:"))
            self.assertTrue(shadow["articles"][0]["partialShadow"])
            self.assertEqual(report["generatedArticles"], 1)

    def test_real_mode_failed_language_does_not_emit_article(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = write_document(root, [short_article()])
            client = FakeLLM(fail_language="ja")
            report = run_mixed_shadow(
                MixedShadowConfig(
                    input_file=input_file,
                    output_dir=root / "shadow",
                    limit=1,
                    engine="llm",
                    language="ja",
                ),
                client=client,
                zh_tw_converter=lambda value: value,
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["generatedArticles"], 0)
            self.assertEqual(len(report["failures"]), 1)
            self.assertEqual(report["failures"][0]["language"], "ja")

    def test_policy_mismatch_is_hard_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = write_document(root, [short_article()])
            wrong = Candidate(
                title="标题",
                summary="摘要",
                content="正文",
                usage=ChatUsage(),
                requests=0,
                content_policy="digest",
                target_language="zh-CN",
            )
            with mock.patch(
                "translation.mixed_shadow._generate_candidate",
                return_value=wrong,
            ):
                report = run_mixed_shadow(
                    MixedShadowConfig(
                        input_file=input_file,
                        output_dir=root / "shadow",
                        limit=1,
                        engine="mock",
                        language="zh-CN",
                    ),
                    zh_tw_converter=lambda value: value,
                )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["generatedArticles"], 0)
            self.assertEqual(report["policyMismatchCount"], 1)
            self.assertIn(
                "实际内容模式与计划不一致",
                report["failures"][0]["failures"][0],
            )

    def test_generator_policy_mismatch_is_counted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = write_document(root, [short_article()])
            wrong = Candidate(
                title="标题",
                summary="摘要",
                content="正文",
                usage=ChatUsage(),
                requests=0,
                content_policy="digest",
                target_language="zh-CN",
            )
            with mock.patch(
                "translation.mixed_shadow.generate_full_l10n",
                return_value=type(
                    "Result",
                    (),
                    {
                        "title": wrong.title,
                        "summary": wrong.summary,
                        "content": wrong.content,
                        "usage": wrong.usage,
                        "requests": 0,
                        "content_policy": "digest",
                        "target_language": "zh-CN",
                        "warnings": (),
                        "segments": (),
                    },
                )(),
            ):
                report = run_mixed_shadow(
                    MixedShadowConfig(
                        input_file=input_file,
                        output_dir=root / "shadow",
                        limit=1,
                        engine="llm",
                        language="zh-CN",
                    ),
                    client=FakeLLM(),
                    zh_tw_converter=lambda value: value,
                )
            self.assertEqual(report["policyMismatchCount"], 1)
            self.assertEqual(report["generatedArticles"], 0)

    def test_real_mode_cache_reuses_only_gate_passed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = write_document(root, [short_article()])
            client = FakeLLM()
            config = MixedShadowConfig(
                input_file=input_file,
                output_dir=root / "shadow",
                limit=1,
                engine="llm",
                language="zh-CN",
                cache_dir=root / "cache",
            )
            first = run_mixed_shadow(
                config,
                client=client,
                zh_tw_converter=lambda value: value,
            )
            second = run_mixed_shadow(
                config,
                client=client,
                zh_tw_converter=lambda value: value,
            )
            self.assertEqual(first["status"], "passed")
            self.assertEqual(second["status"], "passed")
            self.assertEqual(second["cache"]["hits"], 1)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(second["requests"], 0)
            self.assertEqual(second["estimatedCostCny"], 0)
            self.assertEqual(second["budget"]["maxCostCny"], 20.0)
            self.assertEqual(second["hardDeadlineMinutes"], 60.0)

    def test_real_mode_budget_hard_limit_stops_before_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = write_document(root, [short_article()])
            client = FakeLLM()
            report = run_mixed_shadow(
                MixedShadowConfig(
                    input_file=input_file,
                    output_dir=root / "shadow",
                    limit=1,
                    engine="llm",
                    language="zh-CN",
                    max_cost_cny=0.0001,
                    hard_deadline_minutes=60,
                ),
                client=client,
                zh_tw_converter=lambda value: value,
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(len(client.calls), 0)
            self.assertTrue(report["budget"]["budgetExceeded"])

    def test_gate_compares_numbers_from_title_summary_and_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            article = short_article()
            article["originalTitle"] = "GTA 6 reaches 39 million players"
            article["originalSummary"] = "The 2026 launch attracted 39 million players."
            article["originalContent"] = (
                "The launch attracted a large audience. "
                "The company confirmed the result in a public statement."
            )
            input_file = write_document(root, [article])
            report = run_mixed_shadow(
                MixedShadowConfig(
                    input_file=input_file,
                    output_dir=root / "shadow",
                    limit=1,
                    engine="llm",
                    language="zh-CN",
                ),
                client=NumberMetadataClient(),
                zh_tw_converter=lambda value: value,
            )
            self.assertEqual(report["status"], "passed", report)
            self.assertEqual(report["generatedArticles"], 1)

    def test_shadow_cli_parses_mixed_and_digest_modes(self):
        import shadow_translate

        for mode in ("mixed", "digest"):
            with self.subTest(mode=mode):
                with mock.patch.object(
                    sys,
                    "argv",
                    [
                        "shadow_translate.py",
                        "--engine",
                        "mock",
                        "--content-mode",
                        mode,
                        "--limit",
                        "1",
                    ],
                ):
                    args = shadow_translate.parse_args()
                self.assertEqual(args.content_mode, mode)


if __name__ == "__main__":
    unittest.main()
