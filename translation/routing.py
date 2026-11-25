"""按原文语言和长度选择五语内容生成模式。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


CONTENT_ROUTING_VERSION = "mixed-language-5000-2000-v1"
ENGLISH_WORD_LIMIT = 5000
CHINESE_CHARACTER_LIMIT = 2000

ContentPolicy = Literal["full_l10n", "digest"]

ENGLISH_WORD_PATTERN = re.compile(
    r"[A-Za-z]+(?:['’\-][A-Za-z]+)*",
)
CHINESE_CHARACTER_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]",
)


@dataclass(frozen=True)
class ContentRoute:
    """一次内容路由结果，可直接写入影子或生产报告。"""

    source_language: str
    content_policy: ContentPolicy
    content_policy_reason: str
    english_word_count: int = 0
    chinese_character_count: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    routing_version: str = CONTENT_ROUTING_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceLanguage": self.source_language,
            "contentPolicy": self.content_policy,
            "contentPolicyReason": self.content_policy_reason,
            "englishWordCount": self.english_word_count,
            "chineseCharacterCount": self.chinese_character_count,
            "contentRoutingVersion": self.routing_version,
            "warnings": list(self.warnings),
        }


def english_word_count(text: str) -> int:
    """统计英文单词；撇号和连字符连接的复合词按一个词处理。"""

    return len(ENGLISH_WORD_PATTERN.findall(str(text or "")))


def chinese_character_count(text: str) -> int:
    """只统计汉字，不把标点、数字、字母或空白算作中文字符。"""

    return len(CHINESE_CHARACTER_PATTERN.findall(str(text or "")))


def normalize_source_language(value: object) -> str:
    """归一化采集器提供的语言代码。"""

    language = str(value or "").strip().lower().replace("_", "-")
    if not language:
        return "unknown"
    if language in {"en", "eng", "english"} or language.startswith("en-"):
        return "en"
    if language in {"zh", "zho", "chi", "chinese", "中文"} or language.startswith(
        "zh-"
    ):
        return "zh"
    return language


def source_language_from_article(article: dict[str, Any]) -> str:
    """读取正文语言，不从译文或页面目标语言反推。"""

    return normalize_source_language(
        article.get("sourceLanguage")
        or article.get("originalLanguage")
        or article.get("source_language")
        or article.get("original_language")
    )


def route_content(
    article: dict[str, Any],
    *,
    source_language: str | None = None,
    body: str | None = None,
) -> ContentRoute:
    """根据原文语言执行确定性路由。

    英文：严格超过 5000 词使用 digest。
    中文：严格超过 2000 个汉字使用 digest。
    其他或未知语言：安全回退到 full_l10n，并产生警告。
    """

    language = normalize_source_language(
        source_language
        if source_language is not None
        else source_language_from_article(article)
    )
    text = str(
        body
        if body is not None
        else article.get("originalContent")
        or article.get("content")
        or ""
    )

    warnings: list[str] = []
    if not text.strip():
        warnings.append("empty_original_content")

    if language == "en":
        words = english_word_count(text)
        policy: ContentPolicy = (
            "digest" if words > ENGLISH_WORD_LIMIT else "full_l10n"
        )
        reason = (
            "english_long_article"
            if policy == "digest"
            else "normal_length"
        )
        return ContentRoute(
            source_language=language,
            content_policy=policy,
            content_policy_reason=reason,
            english_word_count=words,
            warnings=tuple(warnings),
        )

    if language == "zh":
        characters = chinese_character_count(text)
        policy = (
            "digest"
            if characters > CHINESE_CHARACTER_LIMIT
            else "full_l10n"
        )
        reason = (
            "chinese_long_article"
            if policy == "digest"
            else "normal_length"
        )
        return ContentRoute(
            source_language=language,
            content_policy=policy,
            content_policy_reason=reason,
            chinese_character_count=characters,
            warnings=tuple(warnings),
        )

    warning = (
        "unknown_source_language_default_full_l10n"
        if language == "unknown"
        else "unsupported_source_language_default_full_l10n"
    )
    return ContentRoute(
        source_language=language,
        content_policy="full_l10n",
        content_policy_reason=warning,
        english_word_count=english_word_count(text),
        chinese_character_count=chinese_character_count(text),
        warnings=tuple([*warnings, warning]),
    )
