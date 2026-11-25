"""单篇单语新闻摘要生成，不直接写入任何生产文件。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .glossary import Glossary
from .llm import ChatUsage


DIGEST_PROMPT_VERSION = "digest-v1"
DIGEST_CONTENT_POLICY = "digest"
DEFAULT_DIRECT_BODY_CHARS = 6000
DEFAULT_CHUNK_CHARS = 3000
DEFAULT_MAX_BODY_CHARS = 24000
DEFAULT_CJK_MAX_CHARS = 650
DEFAULT_ENGLISH_MAX_WORDS = 400

SYSTEM_PROMPT = """你是资深游戏媒体编辑。你会收到一篇游戏新闻，
请为目标语言完成三项工作：
1. 翻译标题；
2. 翻译来源摘要；
3. 阅读全文后写一段事实型新闻总结。

只输出 JSON：
{"title":"...","summary":"...","content":"..."}
不要输出 Markdown 代码块，不要输出解释。

标题必须自然、准确，不附加来源名。
总结只使用原文事实，不评论、不推测。
必须保留事件主体、发生了什么、时间、关键数字、平台和必要背景。
原文中的数字、日期、价格、销量、百分比和单位必须保持一致。
游戏名、厂商名、平台名和术语表中要求保留的英文必须原样保留。
无法完成时返回空字符串，不要编造。"""

MAP_PROMPT = """阅读以下新闻片段，输出事实清单。
只列谁做了什么、关键数字、时间、平台和直接影响，不评论，不推测。
只输出 JSON：{"facts":"..."}"""

REDUCE_PROMPT = """根据标题、来源摘要和事实清单，用目标语言写新闻内容总结。
只使用给出的事实，不得新增数字或结论。
只输出 JSON：{"title":"...","summary":"...","content":"..."}"""

SENTENCE_PATTERN = re.compile(r"[.!?。！？](?:[\"'”’）)\]]*)")
ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")


class DigestClient(Protocol):
    """摘要生成所需的最小模型客户端接口。"""

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any], ChatUsage]:
        """返回结构化 JSON 和调用用量。"""


class DigestGenerationError(RuntimeError):
    """摘要生成失败，调用方不得把部分结果当作成功。"""


@dataclass(frozen=True)
class DigestConfig:
    """摘要生成的确定性配置。"""

    direct_body_chars: int = DEFAULT_DIRECT_BODY_CHARS
    chunk_chars: int = DEFAULT_CHUNK_CHARS
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS
    cjk_max_chars: int = DEFAULT_CJK_MAX_CHARS
    english_max_words: int = DEFAULT_ENGLISH_MAX_WORDS

    def __post_init__(self) -> None:
        for name in (
            "direct_body_chars",
            "chunk_chars",
            "max_body_chars",
            "cjk_max_chars",
            "english_max_words",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数")
        if self.chunk_chars > self.direct_body_chars:
            raise ValueError("chunk_chars 不能大于 direct_body_chars")
        if self.max_body_chars < self.chunk_chars:
            raise ValueError("max_body_chars 不能小于 chunk_chars")


@dataclass(frozen=True)
class DigestResult:
    """一篇新闻在一种目标语言下的摘要结果。"""

    title: str
    summary: str
    content: str
    usage: ChatUsage
    requests: int
    warnings: tuple[str, ...] = field(default_factory=tuple)
    content_truncated: bool = False
    body_truncated: bool = False
    target_language: str = ""
    content_policy: str = DIGEST_CONTENT_POLICY
    prompt_version: str = DIGEST_PROMPT_VERSION

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
            "warnings": list(self.warnings),
            "contentTruncated": self.content_truncated,
            "bodyTruncated": self.body_truncated,
            "targetLanguage": self.target_language,
            "contentPolicy": self.content_policy,
            "promptVersion": self.prompt_version,
        }


def generate_digest(
    *,
    client: DigestClient,
    article: dict[str, Any],
    language: str,
    glossary: Glossary,
    config: DigestConfig | None = None,
) -> DigestResult:
    """生成单篇单语的新闻摘要。

    短正文直接调用一次模型；长正文先按片段提取事实，再汇总成摘要。
    任一阶段返回空字段或异常都会使整篇失败，不返回部分成功结果。
    """

    if language not in {"zh-CN", "en", "ja", "ko"}:
        raise ValueError(f"不支持的摘要目标语言：{language}")
    if not isinstance(article, dict):
        raise ValueError("article 必须是对象")

    active_config = config or DigestConfig()
    body, body_truncated = _limit_source_body(
        str(article.get("originalContent") or ""),
        active_config.max_body_chars,
    )

    if len(body) <= active_config.direct_body_chars:
        payload = dict(article)
        payload["originalContent"] = body
        result, usage = _complete_direct(
            client=client,
            article=payload,
            language=language,
            glossary=glossary,
        )
        requests = 1
    else:
        result, usage, requests = _complete_long_article(
            client=client,
            article=article,
            body=body,
            language=language,
            glossary=glossary,
            config=active_config,
        )

    title, summary, content = _required_fields(result)
    content, content_truncated, warnings = trim_digest_content(
        content,
        language=language,
        cjk_max_chars=active_config.cjk_max_chars,
        english_max_words=active_config.english_max_words,
    )
    if body_truncated:
        warnings.append("source_body_truncated")

    return DigestResult(
        title=title,
        summary=summary,
        content=content,
        usage=usage,
        requests=requests,
        warnings=tuple(warnings),
        content_truncated=content_truncated,
        body_truncated=body_truncated,
        target_language=language,
    )


def trim_digest_content(
    content: str,
    *,
    language: str,
    cjk_max_chars: int = DEFAULT_CJK_MAX_CHARS,
    english_max_words: int = DEFAULT_ENGLISH_MAX_WORDS,
) -> tuple[str, bool, list[str]]:
    """只在完整句尾截短超长摘要，并返回明确警告。"""

    text = str(content or "").strip()
    if not text:
        return text, False, []
    if language in {"zh-CN", "ja", "ko"}:
        if len(text) <= cjk_max_chars:
            return text, False, []
        boundary = _last_sentence_boundary(text, cjk_max_chars)
        if boundary is None:
            return text, False, ["content_over_limit_without_sentence_boundary"]
        return (
            text[:boundary].strip(),
            True,
            ["content_trimmed_at_sentence_boundary"],
        )
    if language == "en":
        words = ENGLISH_WORD_PATTERN.findall(text)
        if len(words) <= english_max_words:
            return text, False, []
        boundary = _english_sentence_boundary(text, english_max_words)
        if boundary is None:
            return text, False, ["content_over_limit_without_sentence_boundary"]
        return (
            text[:boundary].strip(),
            True,
            ["content_trimmed_at_sentence_boundary"],
        )
    raise ValueError(f"不支持的摘要目标语言：{language}")


def _complete_direct(
    *,
    client: DigestClient,
    article: dict[str, Any],
    language: str,
    glossary: Glossary,
) -> tuple[dict[str, Any], ChatUsage]:
    payload = {
        "目标语言": language,
        "内容总结目标": "中文、日文、韩文约 450–580 字；英文约 250–380 词",
        "术语对照": glossary.prompt_lines(
            f"{article.get('originalTitle', '')}\n"
            f"{article.get('originalSummary', '')}",
            language,
        ),
        "新闻": {
            "sourceLanguage": article.get("sourceLanguage", "unknown"),
            "title": article.get("originalTitle", ""),
            "summary": article.get("originalSummary", ""),
            "body": article.get("originalContent", ""),
        },
    }
    return _safe_complete(
        client,
        system_prompt=f"{SYSTEM_PROMPT}\n目标语言：{language}",
        user_prompt=json.dumps(payload, ensure_ascii=False),
        stage="direct",
    )


def _complete_long_article(
    *,
    client: DigestClient,
    article: dict[str, Any],
    body: str,
    language: str,
    glossary: Glossary,
    config: DigestConfig,
) -> tuple[dict[str, Any], ChatUsage, int]:
    chunks = _split_body(body, config.chunk_chars)
    if not chunks:
        raise DigestGenerationError("长文正文为空，无法生成摘要")

    facts: list[str] = []
    total_usage = ChatUsage()
    requests = 0
    for index, chunk in enumerate(chunks, start=1):
        map_result, usage = _safe_complete(
            client,
            system_prompt=f"{MAP_PROMPT}\n目标语言：{language}",
            user_prompt=f"片段 {index}/{len(chunks)}\n\n{chunk}",
            stage=f"map[{index}/{len(chunks)}]",
        )
        fact = str(map_result.get("facts") or "").strip()
        if not fact:
            raise DigestGenerationError(
                f"长文 map 阶段第 {index}/{len(chunks)} 段返回空事实清单"
            )
        facts.append(fact)
        total_usage += usage
        requests += 1

    reduce_input = json.dumps(
        {
            "目标语言": language,
            "标题": article.get("originalTitle", ""),
            "来源摘要": article.get("originalSummary", ""),
            "事实清单": facts,
            "术语对照": glossary.prompt_lines(
                f"{article.get('originalTitle', '')}\n"
                f"{article.get('originalSummary', '')}",
                language,
            ),
        },
        ensure_ascii=False,
    )
    result, usage = _safe_complete(
        client,
        system_prompt=f"{REDUCE_PROMPT}\n目标语言：{language}",
        user_prompt=reduce_input,
        stage="reduce",
    )
    return result, total_usage + usage, requests + 1


def _safe_complete(
    client: DigestClient,
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
        raise DigestGenerationError(
            f"摘要模型请求失败（stage={stage}）：{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(result, dict):
        raise DigestGenerationError(
            f"摘要模型返回的 JSON 顶层不是对象（stage={stage}）"
        )
    if not isinstance(usage, ChatUsage):
        raise DigestGenerationError(
            f"摘要模型没有返回 ChatUsage（stage={stage}）"
        )
    return result, usage


def _required_fields(result: dict[str, Any]) -> tuple[str, str, str]:
    fields = tuple(
        str(result.get(name) or "").strip()
        for name in ("title", "summary", "content")
    )
    if any(not value for value in fields):
        empty = [
            name
            for name, value in zip(("title", "summary", "content"), fields)
            if not value
        ]
        raise DigestGenerationError(
            f"摘要模型返回空字段：{', '.join(empty)}"
        )
    return fields


def _limit_source_body(body: str, max_chars: int) -> tuple[str, bool]:
    text = str(body or "").strip()
    if len(text) <= max_chars:
        return text, False
    tail_chars = min(3000, max_chars // 4)
    head_chars = max_chars - tail_chars - 7
    return f"{text[:head_chars]}\n\n[...]\n\n{text[-tail_chars:]}", True


def _split_body(body: str, max_chars: int) -> list[str]:
    paragraphs = [
        part.strip()
        for part in re.split(r"\n\s*\n", str(body or ""))
        if part.strip()
    ]
    units: list[str] = []
    for paragraph in paragraphs or [str(body or "").strip()]:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        matched = False
        for match in SENTENCE_PATTERN.finditer(paragraph):
            matched = True
            sentence = paragraph[: match.end()].strip()
            if sentence:
                units.append(sentence)
            paragraph = paragraph[match.end() :].strip()
        if paragraph:
            units.append(paragraph)
        if not matched and paragraph:
            units.extend(
                paragraph[index : index + max_chars]
                for index in range(0, len(paragraph), max_chars)
            )

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 2 > max_chars:
            chunks.append(current.strip())
            current = unit
        else:
            current = f"{current}\n\n{unit}".strip()
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def _last_sentence_boundary(text: str, limit: int) -> int | None:
    boundaries = [
        match.end()
        for match in SENTENCE_PATTERN.finditer(text)
        if match.end() <= limit
    ]
    return max(boundaries) if boundaries else None


def _english_sentence_boundary(text: str, max_words: int) -> int | None:
    word_count = 0
    last_boundary: int | None = None
    for match in SENTENCE_PATTERN.finditer(text):
        segment = text[
            (last_boundary or 0) : match.end()
        ]
        word_count += len(ENGLISH_WORD_PATTERN.findall(segment))
        if word_count <= max_words:
            last_boundary = match.end()
        else:
            break
    return last_boundary
