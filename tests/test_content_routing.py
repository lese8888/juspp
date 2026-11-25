import unittest

from translation.routing import (
    CHINESE_CHARACTER_LIMIT,
    CONTENT_ROUTING_VERSION,
    ENGLISH_WORD_LIMIT,
    chinese_character_count,
    english_word_count,
    normalize_source_language,
    route_content,
)


def article(
    *,
    source_language: str | None = "en",
    body: str,
) -> dict[str, str]:
    value = {"originalContent": body}
    if source_language is not None:
        value["sourceLanguage"] = source_language
    return value


class ContentRoutingTests(unittest.TestCase):
    def test_english_boundaries(self):
        cases = (
            (ENGLISH_WORD_LIMIT - 1, "full_l10n", "normal_length"),
            (ENGLISH_WORD_LIMIT, "full_l10n", "normal_length"),
            (ENGLISH_WORD_LIMIT + 1, "digest", "english_long_article"),
        )
        for count, expected_policy, expected_reason in cases:
            with self.subTest(count=count):
                result = route_content(
                    article(body="word " * count),
                    source_language="en-US",
                )
                self.assertEqual(result.content_policy, expected_policy)
                self.assertEqual(result.content_policy_reason, expected_reason)
                self.assertEqual(result.english_word_count, count)

    def test_chinese_boundaries(self):
        cases = (
            (CHINESE_CHARACTER_LIMIT - 1, "full_l10n", "normal_length"),
            (CHINESE_CHARACTER_LIMIT, "full_l10n", "normal_length"),
            (CHINESE_CHARACTER_LIMIT + 1, "digest", "chinese_long_article"),
        )
        for count, expected_policy, expected_reason in cases:
            with self.subTest(count=count):
                result = route_content(
                    article(body="字" * count),
                    source_language="zh-CN",
                )
                self.assertEqual(result.content_policy, expected_policy)
                self.assertEqual(result.content_policy_reason, expected_reason)
                self.assertEqual(result.chinese_character_count, count)

    def test_chinese_punctuation_is_not_counted_as_characters(self):
        body = "游" * 1999 + "，。！？ 123 ABC\n"
        result = route_content(article(body=body), source_language="zh-TW")
        self.assertEqual(result.chinese_character_count, 1999)
        self.assertEqual(result.content_policy, "full_l10n")

    def test_language_aliases(self):
        aliases = {
            "en": "en",
            "en-GB": "en",
            "eng": "en",
            "zh": "zh",
            "zh-Hant": "zh",
            "zho": "zh",
            "ja-JP": "ja-jp",
            "": "unknown",
        }
        for value, expected in aliases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_source_language(value), expected)

    def test_unknown_language_defaults_to_full_with_warning(self):
        result = route_content(
            article(source_language=None, body="word " * 9000),
        )
        self.assertEqual(result.content_policy, "full_l10n")
        self.assertEqual(
            result.content_policy_reason,
            "unknown_source_language_default_full_l10n",
        )
        self.assertIn(
            "unknown_source_language_default_full_l10n",
            result.warnings,
        )

    def test_other_language_defaults_to_full_with_warning(self):
        result = route_content(
            article(source_language="ja-JP", body="ゲーム" * 3000),
        )
        self.assertEqual(result.content_policy, "full_l10n")
        self.assertEqual(
            result.content_policy_reason,
            "unsupported_source_language_default_full_l10n",
        )
        self.assertIn(
            "unsupported_source_language_default_full_l10n",
            result.warnings,
        )

    def test_empty_body_keeps_safe_route_and_warning(self):
        result = route_content(article(body=""))
        self.assertEqual(result.content_policy, "full_l10n")
        self.assertIn("empty_original_content", result.warnings)

    def test_word_counter_treats_compounds_as_single_tokens(self):
        self.assertEqual(english_word_count("state-of-the-art game's patch"), 3)
        self.assertEqual(chinese_character_count("游戏，News 123"), 2)

    def test_route_serialization_contract(self):
        result = route_content(article(body="word " * 5001), source_language="en")
        payload = result.as_dict()
        self.assertEqual(payload["sourceLanguage"], "en")
        self.assertEqual(payload["contentPolicy"], "digest")
        self.assertEqual(payload["englishWordCount"], 5001)
        self.assertEqual(
            payload["contentRoutingVersion"],
            CONTENT_ROUTING_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
