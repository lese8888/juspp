"""单篇单语完整正文翻译编排，不直接写入生产文件。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from .glossary import Glossary
from .llm import ChatUsage
from .segmentation import (
    DEFAULT_MAX_SEGMENT_CHARS,
    DEFAULT_MIN_SEGMENT_CHARS,
    TextSegment,
    segment_text,
)


FULL_L10N_CONTENT_POLICY = "full_l10n"
FULL_L10N_PROMPT_VERSION = "full-l10n-v1"
SUPPORTED_TARGET_LANGUAGES = frozenset({"zh-CN", "en", "ja", "ko"})

DIRECT_SYSTEM_PROMPT = """你是资深游戏媒体编辑。请把收到的游戏新闻完整翻译成目标语言。
必须翻译标题、来源摘要和完整正文，不得概括、删段或把正文缩写成摘要。

只输出 JSON：
{"title":"...","summary":"...","content":"..."}
不要输出 Markdown 代码块，不要解释。

要求：
1. 只使用原文事实，不添加评论、推测或原文没有的信息。
2. 保留全部有意义的段落、列表和事实顺序。
3. 数字、日期、价格、销量、百分比和单位必须准确一致。
4. 游戏名、厂商名、平台名和术语表中要求保留的英文必须原样保留。
5. title 和 summary 必须完整翻译，content 必须是完整正文，不是总结。
6. 任一字段无法完成时返回空字符串，禁止伪造。"""

METADATA_SYSTEM_PROMPT = """你是资深游戏媒体编辑。请把游戏新闻标题和来源摘要完整翻译成目标语言。
只输出 JSON：{"title":"...","summary":"..."}
不要输出 Markdown 代码块，不要解释。
保留数字、日期、价格、平台和专名；不得扩写或改变事实。"""

SEGMENT_SYSTEM_PROMPT = """你是资深游戏媒体编辑。请完整翻译收到的新闻正文片段。
只输出 JSON：{"content":"..."}
不要输出 Markdown 代码块，不要解释。

要求：
1. 只翻译当前片段，不概括、不删句、不添加事实。
2. 保持段落结构、列表和有意义的换行。
3. 数字、日期、价格、销量、百分比和单位必须准确一致。
4. 游戏名、厂商名、平台名和术语表中要求保留的英文必须原样保留。
5. 无法完整翻译时返回空字符串，禁止用总结代替正文。"""


class FullL10nClient(Protocol):
    """完整翻译所需的最小模型客户端接口。"""

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any], ChatUsage]:
        """返回结构化 JSON 与调用用量。"""


class FullL10nGenerationError(RuntimeError):
    """完整正文生成失败，调用方不得发布部分结果。"""


@dataclass(frozen=True)
class FullL10nConfig:
    """完整正文生成的分段配置。"""

    min_segment_chars: int = DEFAULT_MIN_SEGMENT_CHARS
    max_segment_chars: int = DEFAULT_MAX_SEGMENT_CHARS

    def __post_init__(self) -> None:
        if self.min_segment_chars <= 0 or self.max_segment_chars <= 0:
            raise ValueError("分段字符上限和下限必须是正整数")
        if self.min_segment_chars > self.max_segment_chars:
            raise ValueError("min_segment_chars 不能大于 max_segment_chars")


@dataclass(frozen=True)
class TranslatedSegment:
    """一个源文片段及其完整译文。"""

    index: int
    total: int
    start: int
    end: int
    source_char_count: int
    content: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ordinal": self.index + 1,
            "total": self.total,
            "start": self.start,
            "end": self.end,
            "sourceCharCount": self.source_char_count,
            "translatedCharCount": len(self.content),
        }


@dataclass(frozen=True)
class FullL10nResult:
    """一篇新闻在一种目标语言下的完整翻译结果。"""

    title: str
    summary: str
    content: str
    usage: ChatUsage
    requests: int
    segments: tuple[TranslatedSegment, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    target_language: str = ""
    content_policy: str = FULL_L10N_CONTENT_POLICY
    prompt_version: str = FULL_L10N_PROMPT_VERSION

    @property
    def source_char_count(self) -> int:
        return sum(segment.source_char_count for segment in self.segments)

    @property
    def translated_char_count(self) -> int:
        return len(self.content)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "usage": {
                "inputTokens": self.usage.input_tokens,
                "outputTokens": self.usage.output_tokens,
                "estimated": self.usage.estimated,
            },
            "requests": self.requests,
            "segments": [segment.as_dict() for segment in self.segments],
            "segmentCount": len(self.segments),
            "sourceCharCount": self.source_char_count,
            "translatedCharCount": self.translated_char_count,
            "warnings": list(self.warnings),
            "targetLanguage": self.target_language,
            "contentPolicy": self.content_policy,
            "promptVersion": self.prompt_version,
        }


def generate_full_l10n(
    *,
    client: FullL10nClient,
    article: dict[str, Any],
    language: str,
    glossary: Glossary,
    config: FullL10nConfig | None = None,
) -> FullL10nResult:
    """生成单篇单语完整翻译。

    正文不超过单段上限时使用一次请求。长文先请求标题与来源摘要，再逐段
    请求完整正文；任一段失败都会使整篇失败，绝不返回部分正文。
    """

    if not isinstance(article, dict):
        raise ValueError("article 必须是对象")
    if language not in SUPPORTED_TARGET_LANGUAGES:
        raise ValueError(f"不支持的完整翻译目标语言：{language}")

    title_text = str(article.get("originalTitle") or "").strip()
    summary_text = str(article.get("originalSummary") or "").strip()
    body_text = str(article.get("originalContent") or "").strip()
    if not title_text or not summary_text or not body_text:
        raise ValueError("标题、来源摘要和原文正文都不能为空")

    active_config = config or FullL10nConfig()
    segmentation = segment_text(
        body_text,
        min_chars=active_config.min_segment_chars,
        max_chars=active_config.max_segment_chars,
    )

    if len(segmentation.segments) == 1:
        return _generate_direct(
            client=client,
            article=article,
            title=title_text,
            summary=summary_text,
            body=body_text,
            language=language,
            glossary=glossary,
        )

    return _generate_segmented(
        client=client,
        article=article,
        title=title_text,
        summary=summary_text,
        language=language,
        glossary=glossary,
        segments=segmentation.segments,
    )


def _generate_direct(
    *,
    client: FullL10nClient,
    article: dict[str, Any],
    title: str,
    summary: str,
    body: str,
    language: str,
    glossary: Glossary,
) -> FullL10nResult:
    payload = {
        "目标语言": language,
        "来源语言": article.get("sourceLanguage", "unknown"),
        "术语对照": glossary.prompt_lines(
            f"{title}\n{summary}\n{body}",
            language,
        ),
        "新闻": {
            "title": title,
            "summary": summary,
            "body": body,
        },
    }
    result, usage = _safe_complete(
        client,
        system_prompt=f"{DIRECT_SYSTEM_PROMPT}\n目标语言：{language}",
        user_prompt=json.dumps(payload, ensure_ascii=False),
        stage="direct",
    )
    translated_title, translated_summary, translated_body = _required_fields(
        result,
        ("title", "summary", "content"),
        stage="direct",
    )
    segment = TranslatedSegment(
        index=0,
        total=1,
        start=0,
        end=len(body),
        source_char_count=len(body),
        content=translated_body,
    )
    return FullL10nResult(
        title=translated_title,
        summary=translated_summary,
        content=translated_body,
        usage=usage,
        requests=1,
        segments=(segment,),
        target_language=language,
    )


def _generate_segmented(
    *,
    client: FullL10nClient,
    article: dict[str, Any],
    title: str,
    summary: str,
    language: str,
    glossary: Glossary,
    segments: tuple[TextSegment, ...],
) -> FullL10nResult:
    metadata_payload = {
        "目标语言": language,
        "来源语言": article.get("sourceLanguage", "unknown"),
        "术语对照": glossary.prompt_lines(f"{title}\n{summary}", language),
        "title": title,
        "summary": summary,
    }
    metadata, total_usage = _safe_complete(
        client,
        system_prompt=f"{METADATA_SYSTEM_PROMPT}\n目标语言：{language}",
        user_prompt=json.dumps(metadata_payload, ensure_ascii=False),
        stage="metadata",
    )
    translated_title, translated_summary, _ = _required_fields(
        metadata,
        ("title", "summary"),
        stage="metadata",
    )

    translated_segments: list[TranslatedSegment] = []
    for segment in segments:
        segment_payload = {
            "目标语言": language,
            "来源语言": article.get("sourceLanguage", "unknown"),
            "新闻标题": title,
            "新闻摘要": summary,
            "片段序号": segment.ordinal,
            "片段总数": segment.total,
            "术语对照": glossary.prompt_lines(
                f"{title}\n{summary}\n{segment.text}",
                language,
            ),
            "正文片段": segment.text,
        }
        result, usage = _safe_complete(
            client,
            system_prompt=(
                f"{SEGMENT_SYSTEM_PROMPT}\n目标语言：{language}"
            ),
            user_prompt=json.dumps(segment_payload, ensure_ascii=False),
            stage=f"segment[{segment.ordinal}/{segment.total}]",
        )
        translated_body = _required_text(
            result,
            "content",
            stage=f"segment[{segment.ordinal}/{segment.total}]",
        )
        total_usage += usage
        translated_segments.append(
            TranslatedSegment(
                index=segment.index,
                total=segment.total,
                start=segment.start,
                end=segment.end,
                source_char_count=segment.char_count,
                content=translated_body,
            )
        )

    return FullL10nResult(
        title=translated_title,
        summary=translated_summary,
        content="\n\n".join(
            segment.content.strip() for segment in translated_segments
        ),
        usage=total_usage,
        requests=len(translated_segments) + 1,
        segments=tuple(translated_segments),
        target_language=language,
    )


def _safe_complete(
    client: FullL10nClient,
    *,
    system_prompt: str,
    user_prompt: str,
    stage: str,
) -> tuple[dict[str, Any], ChatUsage]:
    try:
        result, usage = client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except Exception as exc:
        raise FullL10nGenerationError(
            f"完整翻译模型请求失败（stage={stage}）："
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(result, dict):
        raise FullL10nGenerationError(
            f"完整翻译返回的 JSON 顶层不是对象（stage={stage}）"
        )
    if not isinstance(usage, ChatUsage):
        raise FullL10nGenerationError(
            f"完整翻译没有返回 ChatUsage（stage={stage}）"
        )
    return result, usage


def _required_fields(
    result: dict[str, Any],
    names: tuple[str, ...],
    *,
    stage: str,
) -> tuple[str, ...]:
    values = tuple(_required_text(result, name, stage=stage) for name in names)
    if not names:
        return ()
    # 三个字段接口统一补一个空的 content，便于直接解包。
    if names == ("title", "summary"):
        return (*values, "")
    return values


def _required_text(
    result: dict[str, Any],
    name: str,
    *,
    stage: str,
) -> str:
    value = str(result.get(name) or "").strip()
    if not value:
        raise FullL10nGenerationError(
            f"完整翻译返回空字段 {name}（stage={stage}）"
        )
    return value
