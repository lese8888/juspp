"""分模式翻译质量闸门，只判断候选是否能进入缓存或发布。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal, Sequence

from .cache import CacheEligibility
from .routing import CONTENT_ROUTING_VERSION


VALID_LANGUAGES = frozenset({"zh-CN", "en", "ja", "ko"})
VALID_POLICIES = frozenset({"full_l10n", "digest"})
PLACEHOLDER_PATTERN = re.compile(
    r"Q[\s-]*Z(?:[\s-]*[A-Z]){5,8}|\[{1,4}\s*[A-Z]{5,10}\s*\]{1,4}"
)
NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.])(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<unit>thousand|million|billion|万|萬|亿|億|千|천|만|억|%|％)?",
    re.IGNORECASE,
)
SENTENCE_ENDINGS = tuple(".!?。！？")
BROKEN_ENDINGS = tuple(",，:：、-–—/")
ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")
CHINESE_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
JAPANESE_KANA_PATTERN = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
KOREAN_PATTERN = re.compile(r"[\uac00-\ud7a3]")
LATIN_PATTERN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class GateIssue:
    """一个可定位的闸门问题。"""

    code: str
    message: str


@dataclass(frozen=True)
class GateReport:
    """一次闸门运行结果；hard failures 非空时不得发布或缓存。"""

    content_policy: str
    target_language: str
    hard_failures: tuple[GateIssue, ...] = field(default_factory=tuple)
    warnings: tuple[GateIssue, ...] = field(default_factory=tuple)
    metrics: dict[str, Any] = field(default_factory=dict)
    routing_version: str = CONTENT_ROUTING_VERSION

    @property
    def passed(self) -> bool:
        return not self.hard_failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "contentPolicy": self.content_policy,
            "targetLanguage": self.target_language,
            "hardFailures": [
                {"code": issue.code, "message": issue.message}
                for issue in self.hard_failures
            ],
            "warnings": [
                {"code": issue.code, "message": issue.message}
                for issue in self.warnings
            ],
            "metrics": self.metrics,
            "contentRoutingVersion": self.routing_version,
        }

    def cache_eligibility(self) -> CacheEligibility:
        """转换为缓存写入资格；任何未解决警告均不允许长期复用。"""

        return CacheEligibility(
            gate_passed=self.passed,
            hard_failures=tuple(issue.code for issue in self.hard_failures),
            unresolved_warnings=tuple(issue.code for issue in self.warnings),
            content_truncated=bool(
                self.metrics.get("contentTruncated")
                or self.metrics.get("safelyTruncated")
            ),
        )


def validate_translation(
    *,
    source_text: str,
    source_body_text: str | None = None,
    candidate_title: str,
    candidate_summary: str,
    candidate_content: str,
    target_language: str,
    content_policy: str,
    source_language: str | None = None,
    segments: Sequence[dict[str, Any]] | None = None,
) -> GateReport:
    """执行通用、full_l10n 与 digest 对应的质量检查。"""

    failures: list[GateIssue] = []
    warnings: list[GateIssue] = []
    metrics: dict[str, Any] = {}

    if target_language not in VALID_LANGUAGES:
        _hard(failures, "unsupported_target_language", f"不支持的目标语言：{target_language}")
    if content_policy not in VALID_POLICIES:
        _hard(failures, "unsupported_content_policy", f"不支持的内容策略：{content_policy}")

    fields = {
        "title": str(candidate_title or "").strip(),
        "summary": str(candidate_summary or "").strip(),
        "content": str(candidate_content or "").strip(),
    }
    for name, value in fields.items():
        if not value:
            _hard(failures, f"empty_{name}", f"候选字段 {name} 为空")

    if any(PLACEHOLDER_PATTERN.search(value) for value in fields.values()):
        _hard(failures, "internal_placeholder", "候选包含内部术语占位符")

    source = str(source_text or "")
    source_body = (
        str(source_body_text)
        if source_body_text is not None
        else source
    )
    if target_language in VALID_LANGUAGES and fields["content"]:
        language = detect_dominant_language(fields["content"])
        metrics["detectedLanguage"] = language
        if not _language_matches(target_language, language):
            _hard(
                failures,
                "target_language_mismatch",
                f"目标语言为 {target_language}，实际主导语言为 {language}",
            )

    source_numbers = extract_required_numbers(source)
    candidate_numbers = extract_required_numbers(
        "\n".join(fields.values())
    )
    missing_numbers = sorted(source_numbers - candidate_numbers)
    extra_numbers = sorted(candidate_numbers - source_numbers)
    metrics["sourceNumbers"] = sorted(source_numbers)
    metrics["candidateNumbers"] = sorted(candidate_numbers)
    metrics["missingNumbers"] = missing_numbers
    metrics["extraNumbers"] = extra_numbers
    if missing_numbers:
        _hard(
            failures,
            "missing_required_numbers",
            f"候选缺少原文数字：{', '.join(missing_numbers)}",
        )
    if extra_numbers:
        _hard(
            failures,
            "invented_numbers",
            f"候选新增原文不存在的数字：{', '.join(extra_numbers)}",
        )

    normalized_source = _normalize_text(source)
    normalized_candidate = _normalize_text(fields["content"])
    if (
        source_language
        and source_language != target_language
        and len(normalized_source) >= 200
        and normalized_source and normalized_source in normalized_candidate
    ):
        _hard(
            failures,
            "untranslated_source_backfill",
            "跨语言候选直接回填了完整原文",
        )

    if content_policy == "full_l10n":
        _validate_full_l10n(
            source=source_body,
            candidate=fields["content"],
            segments=segments,
            failures=failures,
            warnings=warnings,
            metrics=metrics,
        )
    elif content_policy == "digest":
        _validate_digest(
            source=source,
            candidate=fields["content"],
            target_language=target_language,
            failures=failures,
            warnings=warnings,
            metrics=metrics,
        )

    return GateReport(
        content_policy=content_policy,
        target_language=target_language,
        hard_failures=tuple(failures),
        warnings=tuple(warnings),
        metrics=metrics,
    )


def extract_required_numbers(text: str) -> set[str]:
    """提取显式数字并归一化千、万、百万、十亿和百分比单位。"""

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    multipliers = {
        "thousand": 1_000,
        "million": 1_000_000,
        "billion": 1_000_000_000,
        "千": 1_000,
        "천": 1_000,
        "万": 10_000,
        "萬": 10_000,
        "만": 10_000,
        "亿": 100_000_000,
        "億": 100_000_000,
        "억": 100_000_000,
        "%": 1,
        "％": 1,
    }
    values: set[str] = set()
    for match in NUMBER_PATTERN.finditer(normalized):
        number = float(match.group("number").replace(",", ""))
        multiplier = multipliers.get((match.group("unit") or "").lower(), 1)
        value = number * multiplier
        if value.is_integer():
            values.add(str(int(value)))
        else:
            values.add(f"{value:.6f}".rstrip("0").rstrip("."))
    return values


def detect_dominant_language(text: str) -> str:
    """按文字脚本占比判定主要内容语言。"""

    value = str(text or "")
    ko = len(KOREAN_PATTERN.findall(value))
    kana = len(JAPANESE_KANA_PATTERN.findall(value))
    zh = len(CHINESE_PATTERN.findall(value))
    en = len(LATIN_PATTERN.findall(value))
    # 单个 CJK 字符通常承载的词义多于单个拉丁字母，避免短中文新闻因
    # “PlayStation”“Xbox”等品牌名被误判为英文。
    weighted = {
        "ko": ko * 3,
        "ja": kana * 3,
        "zh-CN": zh * 2,
        "en": en,
    }
    language, score = max(weighted.items(), key=lambda item: item[1])
    return language if score else "unknown"


def _validate_full_l10n(
    *,
    source: str,
    candidate: str,
    segments: Sequence[dict[str, Any]] | None,
    failures: list[GateIssue],
    warnings: list[GateIssue],
    metrics: dict[str, Any],
) -> None:
    source_length = len(source.strip())
    candidate_length = len(candidate.strip())
    ratio = candidate_length / source_length if source_length else 0.0
    metrics["sourceCharCount"] = source_length
    metrics["candidateCharCount"] = candidate_length
    metrics["lengthRatio"] = round(ratio, 4)

    if source_length >= 800 and ratio < 0.35:
        _hard(
            failures,
            "full_content_probably_summarized",
            f"全文长度仅为原文的 {ratio:.1%}，疑似摘要冒充全文",
        )
    elif source_length >= 800 and ratio < 0.55:
        _warning(warnings, "short_full_translation", "全文长度明显短于原文")

    if candidate.rstrip().endswith(BROKEN_ENDINGS):
        _hard(failures, "content_ends_mid_sentence", "正文以未完成连接符或标点结束")
    elif candidate and candidate.rstrip()[-1:] not in SENTENCE_ENDINGS + tuple("\"'”’）)]】"):
        _warning(warnings, "content_without_terminal_punctuation", "正文结尾没有明确句末标点")

    source_paragraphs = [item for item in re.split(r"\n\s*\n", source) if item.strip()]
    candidate_paragraphs = [item for item in re.split(r"\n\s*\n", candidate) if item.strip()]
    metrics["sourceParagraphCount"] = len(source_paragraphs)
    metrics["candidateParagraphCount"] = len(candidate_paragraphs)
    if len(source_paragraphs) >= 3 and len(candidate_paragraphs) < max(2, len(source_paragraphs) // 3):
        _hard(failures, "paragraphs_missing", "候选段落数量严重少于原文")
    if any(not paragraph.strip() for paragraph in candidate_paragraphs):
        _hard(failures, "empty_paragraph", "候选包含空段落")

    if segments is not None:
        _validate_segments(segments, source_length, failures, metrics)


def _validate_segments(
    segments: Sequence[dict[str, Any]],
    source_length: int,
    failures: list[GateIssue],
    metrics: dict[str, Any],
) -> None:
    if not segments:
        _hard(failures, "segments_missing", "全文候选缺少分段元数据")
        return
    indexes = [segment.get("index") for segment in segments]
    if indexes != list(range(len(segments))):
        _hard(failures, "segment_order_invalid", "分段索引缺失、重复或顺序错误")
    covered = sum(
        int(segment.get("sourceCharCount") or 0) for segment in segments
    )
    metrics["segmentCount"] = len(segments)
    metrics["sourceCharCountCovered"] = covered
    if source_length and abs(covered - source_length) > max(2, int(source_length * 0.02)):
        _hard(
            failures,
            "segment_coverage_invalid",
            f"分段覆盖 {covered} 字符，原文为 {source_length} 字符",
        )
    if any(not str(segment.get("content") or "").strip() for segment in segments):
        _hard(failures, "empty_translated_segment", "存在空的分段译文")


def _validate_digest(
    *,
    source: str,
    candidate: str,
    target_language: str,
    failures: list[GateIssue],
    warnings: list[GateIssue],
    metrics: dict[str, Any],
) -> None:
    char_limit = 650 if target_language in {"zh-CN", "ja", "ko"} else None
    word_limit = 400 if target_language == "en" else None
    length = len(candidate) if char_limit else len(ENGLISH_WORD_PATTERN.findall(candidate))
    limit = char_limit or word_limit
    metrics["digestLength"] = length
    metrics["digestLimit"] = limit

    if limit and length > limit:
        if candidate.rstrip().endswith(SENTENCE_ENDINGS):
            metrics["safelyTruncated"] = True
            _warning(warnings, "digest_over_limit_at_sentence", "摘要超长且已在完整句尾结束")
        else:
            _hard(failures, "digest_over_limit_unsafe", "摘要超长且未在完整句尾结束")

    normalized_source = _normalize_text(source)
    normalized_candidate = _normalize_text(candidate)
    if normalized_source and len(normalized_candidate) >= 120:
        if normalized_candidate in normalized_source:
            ratio = 1.0
            longest = len(normalized_candidate)
        else:
            matcher = SequenceMatcher(
                None,
                normalized_candidate,
                normalized_source,
                autojunk=False,
            )
            longest = matcher.find_longest_match(
                0,
                len(normalized_candidate),
                0,
                len(normalized_source),
            ).size
            ratio = longest / max(1, len(normalized_candidate))
        metrics["longestCopiedRun"] = longest
        metrics["copyRatio"] = round(ratio, 4)
        if longest >= max(120, int(len(normalized_candidate) * 0.55)):
            _hard(failures, "digest_copied_from_source", "摘要大段复制原文")
        elif ratio >= 0.35:
            _warning(warnings, "digest_copy_ratio_high", "摘要与原文重合度偏高")


def _language_matches(target: str, detected: str) -> bool:
    if detected == "unknown":
        return False
    if target == "zh-CN":
        return detected == "zh-CN" or detected == "ja"
    return target == detected


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or ""))).strip().lower()


def _hard(issues: list[GateIssue], code: str, message: str) -> None:
    issues.append(GateIssue(code=code, message=message))


def _warning(issues: list[GateIssue], code: str, message: str) -> None:
    issues.append(GateIssue(code=code, message=message))
