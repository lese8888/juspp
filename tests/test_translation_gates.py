import unittest

from translation.gates import (
    detect_dominant_language,
    extract_required_numbers,
    validate_translation,
)


def validate(
    *,
    source="PlayStation 5 reached 39 million players in 2026.",
    title="PlayStation 5 在 2026 年达到 3900 万玩家",
    summary="索尼确认了 2026 年的新里程碑。",
    content="PlayStation 5 在 2026 年达到 3900 万玩家。索尼确认这是重要的平台里程碑。",
    language="zh-CN",
    policy="digest",
    source_language="en",
    segments=None,
):
    return validate_translation(
        source_text=source,
        candidate_title=title,
        candidate_summary=summary,
        candidate_content=content,
        target_language=language,
        content_policy=policy,
        source_language=source_language,
        segments=segments,
    )


class TranslationGateTests(unittest.TestCase):
    def test_valid_digest_passes(self):
        report = validate()
        self.assertTrue(report.passed, report.as_dict())
        self.assertIn("39000000", report.metrics["candidateNumbers"])

    def test_number_change_and_invention_fail(self):
        missing = validate(
            title="PlayStation 5 玩家数增长",
            summary="索尼公布新消息。",
            content="PlayStation 5 玩家数大幅增长。",
        )
        self.assertFalse(missing.passed)
        self.assertIn("missing_required_numbers", [x.code for x in missing.hard_failures])

        invented = validate(content="PlayStation 5 在 2026 年达到 4900 万玩家。")
        self.assertFalse(invented.passed)
        self.assertIn("invented_numbers", [x.code for x in invented.hard_failures])

    def test_language_mismatch_fails(self):
        report = validate(content="PlayStation 5 reached 39 million players in 2026.")
        self.assertFalse(report.passed)
        self.assertIn("target_language_mismatch", [x.code for x in report.hard_failures])

    def test_empty_fields_and_placeholder_fail(self):
        empty = validate(title="")
        self.assertFalse(empty.passed)
        self.assertIn("empty_title", [x.code for x in empty.hard_failures])

        placeholder = validate(content="PlayStation 5 的 Q Z A B C D E 平台达到 3900 万。")
        self.assertFalse(placeholder.passed)
        self.assertIn("internal_placeholder", [x.code for x in placeholder.hard_failures])

    def test_summary_impersonating_full_translation_fails(self):
        source = ("PlayStation 5 reached 39 million players in 2026. " * 30)
        report = validate(
            source=source,
            content="PlayStation 5 在 2026 年达到 3900 万玩家。",
            policy="full_l10n",
        )
        self.assertFalse(report.passed)
        self.assertIn("full_content_probably_summarized", [x.code for x in report.hard_failures])

    def test_dense_chinese_full_translation_is_not_treated_as_summary(self):
        source = (
            "PlayStation 5 reached 39 million players in 2026. "
            "The platform team confirmed the milestone and continued to update live data. "
        ) * 18
        content = (
            "PlayStation 5 在 2026 年达到 3900 万玩家。"
            "平台团队确认了里程碑，并持续更新实时数据。"
        ) * 12
        report = validate(
            source=source,
            content=content,
            policy="full_l10n",
        )
        self.assertNotIn(
            "full_content_probably_summarized",
            [x.code for x in report.hard_failures],
        )

    def test_source_ending_at_colon_does_not_fail_matching_candidate(self):
        source = (
            "The presentation will include the following announcements:\n\n"
            + ("PlayStation 5 reached 39 million players in 2026. " * 20)
            + "The final segment includes:"
        )
        content = (
            "发布会将包含以下公告：\n\n"
            + ("PlayStation 5 在 2026 年达到 3900 万玩家。" * 25)
            + "最后环节包括："
        )
        report = validate(
            source=source,
            content=content,
            policy="full_l10n",
        )
        self.assertNotIn(
            "content_ends_mid_sentence",
            [x.code for x in report.hard_failures],
        )

    def test_short_chinese_with_brand_names_is_accepted(self):
        report = validate(
            source="Attack on Titan 3 gameplay video released.",
            title="Attack on Titan 3《城墙内探索》实机演示 - Gematsu",
            summary="发行商 Koei Tecmo 与 Omega Force 发布了一段实机演示。",
            content=(
                "发行商 Koei Tecmo 与开发商 Omega Force 发布了"
                "《Attack on Titan 3》的一段八分钟实机演示视频，聚焦墙内探索。"
            ),
            policy="full_l10n",
        )
        self.assertNotIn(
            "target_language_mismatch",
            [x.code for x in report.hard_failures],
        )

    def test_extra_small_decimal_is_warning_not_hard(self):
        report = validate(
            source="Lucia remained below deck for.1 mi ute e.",
            title="Lucia below deck",
            summary="A short bug report.",
            content="Lucia remained below deck for 0.1 minutes in one report.",
            language="en",
            policy="digest",
        )
        self.assertNotIn(
            "invented_numbers",
            [x.code for x in report.hard_failures],
        )

    def test_missing_or_reordered_segments_fail(self):
        source = "甲" * 90 + "\n\n" + "乙" * 90
        segments = [
            {"index": 1, "sourceCharCount": 50, "content": "乙译文"},
            {"index": 0, "sourceCharCount": 50, "content": "甲译文"},
        ]
        report = validate(
            source=source,
            content="甲译文\n\n乙译文" * 20,
            policy="full_l10n",
            segments=segments,
        )
        self.assertFalse(report.passed)
        codes = [x.code for x in report.hard_failures]
        self.assertIn("segment_order_invalid", codes)
        self.assertIn("segment_coverage_invalid", codes)

    def test_digest_copy_from_source_fails(self):
        source = "PlayStation 5 reached 39 million players in 2026. " * 20
        report = validate(
            source=source,
            content=source[:300],
            policy="digest",
            language="en",
        )
        self.assertFalse(report.passed)
        self.assertIn("digest_copied_from_source", [x.code for x in report.hard_failures])

    def test_safe_oversized_digest_is_warning(self):
        content = (
            "2026 年，PlayStation 5 的玩家规模达到 3900 万。"
            + ("平台团队确认该里程碑并持续更新运营数据。" * 40)
            + "完。"
        )
        report = validate(content=content, policy="digest")
        self.assertEqual(report.hard_failures, ())
        self.assertIn("digest_over_limit_at_sentence", [x.code for x in report.warnings])
        self.assertTrue(report.cache_eligibility().content_truncated)

    def test_policy_and_target_language_are_validated(self):
        bad_policy = validate(policy="summary")
        self.assertFalse(bad_policy.passed)
        self.assertIn("unsupported_content_policy", [x.code for x in bad_policy.hard_failures])

        bad_language = validate(language="fr")
        self.assertFalse(bad_language.passed)
        self.assertIn("unsupported_target_language", [x.code for x in bad_language.hard_failures])

    def test_number_normalization_handles_units(self):
        numbers = extract_required_numbers("39 million, 3.9万, 12%, 2026。")
        self.assertEqual(numbers, {"39000000", "39000", "12", "2026"})

    def test_language_detection(self):
        self.assertEqual(detect_dominant_language("这是中文新闻。"), "zh-CN")
        self.assertEqual(detect_dominant_language("これはゲームニュースです。"), "ja")
        self.assertEqual(detect_dominant_language("이것은 게임 뉴스입니다."), "ko")
        self.assertEqual(detect_dominant_language("This is game news."), "en")

    def test_untranslated_source_backfill_fails(self):
        source = "PlayStation 5 reached 39 million players in 2026. " * 10
        report = validate(
            source=source,
            content=source,
            policy="full_l10n",
        )
        self.assertFalse(report.passed)
        self.assertIn("untranslated_source_backfill", [x.code for x in report.hard_failures])


if __name__ == "__main__":
    unittest.main()
