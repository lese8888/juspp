"""不发布生产数据的 LLM 影子翻译流水线。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .glossary import Glossary, load_glossary
from .llm import ChatUsage, OpenAICompatibleClient


SUPPORTED_LANGUAGES = ("zh-CN", "zh-TW", "en", "ja", "ko")
GENERATED_LANGUAGES = ("zh-CN", "en", "ja", "ko")
CONTENT_MODE = "digest"
DEFAULT_MAX_BODY_CHARS = 24000
DEFAULT_PRICE_INPUT_CNY_PER_M = 2.0
DEFAULT_PRICE_OUTPUT_CNY_PER_M = 8.0

SYSTEM_PROMPT = """你是资深游戏媒体编辑。你会收到一篇英文游戏新闻，
请为目标语言完成三件事：
1. 翻译标题；
2. 翻译来源摘要；
3. 阅读全文后写一段新闻内容总结。

只输出 JSON：
{"title":"...","summary":"...","content":"..."}
不要输出 Markdown 代码块，不要输出解释。

标题必须像自然新闻标题，不附加来源名，不出现“翻译”“原文”等字样。
摘要用目标语言自然表达，交代事件主体和核心事实。
内容总结只使用原文事实，不添加原文没有的信息，不做评论和推测。
必须覆盖事件主体、发生了什么、时间、关键数字、平台和必要背景。
原文中的数字、日期、价格、销量、百分比和单位必须保持一致。
游戏名、厂商名、平台名和术语表中要求保留的英文必须原样保留。
无法完成时返回空字符串，不要编造。"""

MAP_PROMPT = """阅读以下新闻片段，输出 150–200 字的纯事实清单。
只列谁做了什么、关键数字、时间、平台和直接影响，不评论，不推测。
只输出 JSON：{"facts":"..."}"""

REDUCE_PROMPT = """根据标题、来源摘要和事实清单，用目标语言写新闻内容总结。
只使用给出的事实，不得新增数字或结论。
只输出 JSON：{"title":"...","summary":"...","content":"..."}"""

FENCE_PATTERN = re.compile(r"```")
PLACEHOLDER_PATTERN = re.compile(r"Q[\s-]*Z(?:[\s-]*[A-Z]){5,8}|\[{1,4}\s*[A-Z]{5,10}\s*\]{1,4}")
NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.])(\d+(?:[.,]\d+)?)\s*(thousand|million|billion|万|亿|千|%|％)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ShadowConfig:
    input_file: Path
    output_dir: Path
    limit: int = 20
    engine: str = "llm"
    language: str | None = None
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS
    concurrency: int = 4
    price_input_cny_per_m: float = DEFAULT_PRICE_INPUT_CNY_PER_M
    price_output_cny_per_m: float = DEFAULT_PRICE_OUTPUT_CNY_PER_M


@dataclass
class GenerationResult:
    title: str
    summary: str
    content: str
    usage: ChatUsage
    requests: int
    body_truncated: bool = False


def article_id(article: dict[str, Any]) -> str:
    return hashlib.sha1(str(article.get("url") or "").encode("utf-8")).hexdigest()[:16]


def source_hash(article: dict[str, Any]) -> str:
    value = "\n".join(
        str(article.get(field) or "")
        for field in ("originalTitle", "originalSummary", "originalContent")
    )
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def select_articles(articles: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """按来源轮询抽取，避免样本全部来自单一媒体。"""
    if limit <= 0:
        raise ValueError("影子抽样数量必须大于 0。")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        grouped.setdefault(str(article.get("sourceKey") or "unknown"), []).append(article)
    for values in grouped.values():
        values.sort(
            key=lambda item: (
                -int(item.get("heatScore") or 0),
                -int(item.get("timestamp") or 0),
                str(item.get("url") or ""),
            )
        )
    selected: list[dict[str, Any]] = []
    source_keys = sorted(grouped)
    position = 0
    while len(selected) < min(limit, len(articles)):
        added = False
        for key in source_keys:
            values = grouped[key]
            if position < len(values):
                selected.append(values[position])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        position += 1
    return selected


def extract_numbers(text: str) -> set[str]:
    """将常见英文和中文数量换算为可比较的绝对数值。"""
    multipliers = {
        "thousand": 1_000,
        "million": 1_000_000,
        "billion": 1_000_000_000,
        "千": 1_000,
        "万": 10_000,
        "亿": 100_000_000,
        "%": 1,
        "％": 1,
    }
    values: set[str] = set()
    for match in NUMBER_PATTERN.finditer(str(text or "")):
        raw = match.group(1).replace(",", "")
        if "." in raw and "," not in raw:
            number = float(raw)
        else:
            number = float(raw)
        unit = (match.group(2) or "").lower()
        number *= multipliers.get(unit, 1)
        if number.is_integer():
            values.add(str(int(number)))
        else:
            values.add(f"{number:.4f}".rstrip("0").rstrip("."))
    month_numbers = {
        "january": 1,
        "jan": 1,
        "february": 2,
        "feb": 2,
        "march": 3,
        "mar": 3,
        "april": 4,
        "apr": 4,
        "may": 5,
        "june": 6,
        "jun": 6,
        "july": 7,
        "jul": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sep": 9,
        "sept": 9,
        "october": 10,
        "oct": 10,
        "november": 11,
        "nov": 11,
        "december": 12,
        "dec": 12,
    }
    normalized_text = str(text or "").lower()
    for month, number in month_numbers.items():
        if re.search(rf"\b{re.escape(month)}\.?\b", normalized_text):
            values.add(str(number))
    number_words = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
    }
    for word, number in number_words.items():
        if re.search(rf"\b{re.escape(word)}\b", normalized_text):
            values.add(str(number))
    for hour, meridiem in re.findall(r"\b(1[0-2]|0?[1-9])\s*(a\.?m\.?|p\.?m\.?)\b", normalized_text):
        numeric_hour = int(hour)
        if meridiem.replace(".", "").startswith("p") and numeric_hour < 12:
            numeric_hour += 12
        elif meridiem.replace(".", "").startswith("a") and numeric_hour == 12:
            numeric_hour = 0
        values.add(str(numeric_hour))
    return values


def _language_failures(language: str, content: str) -> list[str]:
    text = str(content or "")
    total = max(1, len(re.sub(r"\s+", "", text)))
    han = len(re.findall(r"[\u3400-\u9fff]", text))
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    hangul = len(re.findall(r"[\uac00-\ud7af]", text))
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    failures = []
    if language == "ja" and (kana / total < 0.03 or (kana + han) / total < 0.2):
        failures.append("日文语言比例异常")
    elif language == "ko" and (hangul / total < 0.1 or kana > 0):
        failures.append("韩文语言比例异常")
    elif language.startswith("zh") and (
        han / total < 0.3 or kana > 0 or hangul > 0
    ):
        failures.append("中文语言比例异常")
    elif language == "en" and (
        ascii_letters / total < 0.7 or (han + kana + hangul) / total > 0.05
    ):
        failures.append("英文语言比例异常")
    return failures


def validate_result(
    *,
    article: dict[str, Any],
    language: str,
    result: GenerationResult,
    glossary: Glossary,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    for field in ("title", "summary", "content"):
        if not str(getattr(result, field) or "").strip():
            failures.append(f"{field} 为空")
    if failures:
        return failures, warnings

    serialized = f"{result.title}\n{result.summary}\n{result.content}"
    if FENCE_PATTERN.search(serialized):
        failures.append("输出包含 Markdown 代码围栏")
    if PLACEHOLDER_PATTERN.search(serialized):
        failures.append("输出包含内部占位符")

    content_length = len(result.content.strip())
    if language in {"zh-CN", "zh-TW", "ja", "ko"} and not 350 <= content_length <= 700:
        failures.append(f"总结长度为 {content_length}，不在 350–700 范围")
    if language == "en":
        word_count = len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*", result.content))
        if not 200 <= word_count <= 450:
            failures.append(f"英文总结为 {word_count} 词，不在 200–450 范围")

    failures.extend(_language_failures(language, result.content))
    source = f"{article.get('originalTitle', '')}\n{article.get('originalSummary', '')}"
    source_numbers = extract_numbers(
        f"{source}\n{article.get('originalContent', '')}"
    )
    output_numbers = extract_numbers(serialized)
    required_numbers = extract_numbers(source)
    missing = sorted(required_numbers - output_numbers)
    if missing:
        failures.append(f"关键数字缺失：{', '.join(missing[:8])}")
    invented = sorted(output_numbers - source_numbers)
    if invented:
        failures.append(f"出现原文没有的数字：{', '.join(invented[:8])}")

    glossary_failures, glossary_warnings = glossary.validate(
        serialized,
        source,
        language,
    )
    failures.extend(glossary_failures)
    warnings.extend(glossary_warnings)
    return failures, warnings


def split_body(body: str, max_chars: int = 3000) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs or [body.strip()]:
        if len(paragraph) > max_chars:
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        else:
            sentences = [paragraph]
        for sentence in sentences:
            if current and len(current) + len(sentence) + 1 > max_chars:
                chunks.append(current.strip())
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current.strip())
    return chunks


def _article_prompt(article: dict[str, Any], language: str, glossary: Glossary) -> str:
    terms = glossary.prompt_lines(
        f"{article.get('originalTitle', '')}\n{article.get('originalSummary', '')}",
        language,
    )
    return json.dumps(
        {
            "目标语言": language,
            "内容总结目标": "中文、日文、韩文 450–580 字；英文 250–380 词",
            "术语对照": terms,
            "新闻": {
                "title": article.get("originalTitle", ""),
                "summary": article.get("originalSummary", ""),
                "body": article.get("originalContent", ""),
            },
        },
        ensure_ascii=False,
    )


def _mock_generation(
    article: dict[str, Any],
    language: str,
    glossary: Glossary,
) -> GenerationResult:
    source_reference = (
        f"{article.get('originalTitle', '')} {article.get('originalSummary', '')}"
    )
    terms = [entry.term for entry in glossary.terms_in(source_reference)]
    if not terms:
        terms = re.findall(
            r"(?:PlayStation|Xbox|Nintendo|Steam|GTA|Rockstar|Switch|DLC)",
            source_reference,
            flags=re.IGNORECASE,
        )
    numbers = sorted(extract_numbers(
        f"{article.get('originalTitle', '')} {article.get('originalSummary', '')}"
    ))
    term_text = "、".join(dict.fromkeys(terms)) or "游戏"
    number_text = "、".join(numbers) or "无"
    if language == "zh-CN":
        title = f"影子测试新闻：{term_text}"
        summary = "这是一段用于验证影子任务的简体中文摘要，只检查结构、语言、数字和术语，不作为正式发布内容。"
        content = (
            "这是影子验证阶段生成的中文测试内容，只用于检查请求编排、结构化解析、质量检测和报告输出是否能够完整运行。"
            "测试流程会读取当前新闻数据中的标题、摘要和正文，并调用隔离的生成器产生候选结果，但不会覆盖正式新闻文件。"
            "所有候选结果只写入独立的输出目录，随后通过制品上传供人工审核，因此线上页面和公开接口都不会发生变化。"
            "本次检查同时关注目标语言比例、关键数字是否保留、专名是否完整以及输出是否存在代码围栏或内部占位符。"
            "如果检查发现异常，任务会记录具体文章、语言、字段和原因，并保留报告，便于定位问题后重新运行影子任务。"
            "测试不会触发页面发布流程，也不会提交新的新闻数据，主分支中的正式数据因此继续保持稳定和可回滚。"
            "人工审核时应重点核对事件主体、关键数字、日期、平台和游戏名称，并判断表达是否自然、事实是否完整。"
            "通过多篇样本还可以估算真实模型的请求数量、输入输出规模、平均耗时和预计费用，为后续预算提供依据。"
            "当前结果是模拟引擎生成的测试文本，不代表模型翻译质量，也不能用于评价供应商或决定生产切换。"
            "真实模型测试需要在仓库密钥中配置接口凭据，再由人工手动触发影子工作流，并只上传候选数据和审核报告。"
            "只有当真实样本通过人工审核、数字错误为零、语言检查稳定且成本在预算内时，才具备讨论灰度的条件。"
            "在此之前生产链路继续使用现有翻译方案，任何影子结果都不进入新闻列表、RSS、搜索索引或缓存的公开页面。"
            "本次样本同时包含不同来源和不同正文长度，以便观察长文拆分、并发请求和错误处理是否稳定。"
            "报告会分别记录成功、失败、警告和请求次数，方便后续比较不同模型、提示词和术语表的实际效果。"
            "如果模型输出为空、语言错误、数字缺失或出现原文没有的数量，该样本会被标记为失败而不是静默通过。"
            f"本样本涉及的相关专名包括：{term_text}。本样本识别到的关键数字包括：{number_text}。"
        )
    elif language == "en":
        title = f"Shadow test story: {term_text}"
        summary = (
            "This summary verifies the isolated shadow workflow. It checks structure, language, "
            "numbers and terminology only and is not production content."
        )
        content = (
            "This is a synthetic English shadow-test digest used to verify the complete generation and validation pipeline. "
            "It is not a real translation and will never appear on the public news page. "
            "The shadow job reads the current article title, summary and body, sends a request to an isolated generator, "
            "parses structured output and records the resulting usage and validation status. "
            "Candidate data is written only to a dedicated artifact directory, which keeps production files untouched. "
            "The checks inspect whether every expected field is present and whether the target language is represented correctly. "
            "They also compare numbers, dates, prices and other quantities with the source article to catch obvious mistakes. "
            "A missing key number, an invented quantity, an empty field or an unsupported language causes the sample to fail. "
            "Glossary rules are included so the test can verify that platform names and game titles remain identifiable. "
            "The workflow deliberately avoids any commit, push, deployment or page invalidation operation. "
            "That separation allows engineers to evaluate a model without changing the reader-facing product or API contract. "
            "The report records request counts, token usage, elapsed time and estimated cost for each selected article. "
            "These values help calibrate a daily budget before any real provider is approved for production traffic. "
            "The sample set includes several sources and article lengths to expose concurrency, timeout and parsing problems. "
            "Warnings remain visible for human review while hard failures prevent a candidate from being treated as successful. "
            "A person must inspect natural style, factual coverage and title quality before deciding whether a model is useful. "
            "Only after repeated real-model runs pass review should the team discuss a controlled production rollout. "
            "Until then the existing translation service remains the sole source for published news and RSS content. "
            "This mock result is intentionally generic and demonstrates plumbing rather than editorial quality. "
            "The shadow artifact can be deleted or regenerated without affecting Git history or the deployed site. "
            "The report also captures whether long articles require splitting and whether the final output is truncated. "
            "Each run is isolated by timestamp and can be compared with later runs using the same deterministic sample policy. "
            "The test keeps source facts close to the request so reviewers can compare the digest with the original text. "
            "If a real provider returns malformed JSON or an empty message, the request layer reports a retryable failure. "
            "Authentication and permission failures stop immediately so a bad key cannot cause repeated charges. "
            "Every successful candidate is still marked as shadow data so downstream tools cannot mistake it for production. "
            f"Relevant entities in this sample include {term_text}. "
            f"Key quantities recognized from the source include {number_text}. "
            "This final sentence exists only to keep the mock output above the minimum English word threshold. "
        )
    elif language == "ja":
        title = f"シャドーテスト記事：{term_text}"
        summary = "これは影検証用の日本語要約です。構造、言語、数字、用語だけを確認し、正式な公開内容としては使用しません。"
        content = (
            "これは影検証段階で生成した日本語のテスト内容であり、リクエスト編成、JSON解析、品質検査、レポート出力が正常に動くかを確認するためのものです。"
            "処理は現在の記事にある見出し、要約、本文を読み取り、分離された生成器で候補を作りますが、正式なニュースファイルは上書きしません。"
            "候補データは専用の出力ディレクトリだけに保存され、成果物としてアップロードされるため、公開ページやAPIは変更されません。"
            "検査では対象言語の比率、重要な数字の保持、固有名詞の扱い、コードフェンスや内部プレースホルダーの有無を確認します。"
            "異常が見つかった場合は記事、言語、項目、理由をレポートに記録し、修正後に影検証を再実行できるようにします。"
            "この処理はページ公開を起動せず、ニュースデータもコミットしないため、本番データは安定した状態を保ちます。"
            "人による確認では出来事の主体、数字、日付、対応機種、ゲーム名を照合し、文章が自然で事実が十分かを評価します。"
            "複数記事の結果から実際のモデルに必要なリクエスト数、入出力量、時間、概算費用を測り、予算判断に使います。"
            "現在の文章は模擬生成器によるテストであり、モデル品質の評価や本番切り替えの判断には使用できません。"
            "実モデルの検証にはリポジトリの秘密情報へ認証情報を設定し、手動で影検証ワークフローを起動する必要があります。"
            "実サンプルが人による確認、数字の正確性、言語検査、費用の条件を満たした時だけ、段階公開を検討できます。"
            "それまでは既存の翻訳処理が公開ニュースとRSSの唯一の生成元として使われます。"
            "今回のサンプルには複数の情報源と異なる本文長を含め、長文分割、同時実行、エラー処理の挙動を確認します。"
            "レポートには成功、失敗、警告、リクエスト回数を記録し、モデルやプロンプトの比較に利用します。"
            "空の出力、誤った言語、数字の欠落、原文にない数量があれば、そのサンプルは黙って成功扱いせず失敗になります。"
            f"このサンプルで確認した固有名詞は {term_text}、重要な数値は {number_text} です。"
        )
    elif language == "ko":
        title = f"그림자 테스트 기사: {term_text}"
        summary = "이 문장은 그림자 검증용 한국어 요약입니다. 구조, 언어, 숫자와 용어만 확인하며 실제 공개 콘텐츠로 사용하지 않습니다."
        content = (
            "이것은 그림자 검증 단계에서 생성한 한국어 테스트 내용이며 요청 구성, JSON 분석, 품질 검사와 보고서 출력이 정상적으로 작동하는지 확인하기 위한 것입니다."
            "처리 과정은 현재 기사 데이터의 제목, 요약과 본문을 읽고 분리된 생성기에서 후보 결과를 만들지만 공식 뉴스 파일은 덮어쓰지 않습니다."
            "후보 데이터는 전용 출력 디렉터리에만 기록되고 산출물로 업로드되므로 공개 페이지와 API에는 어떤 변경도 적용되지 않습니다."
            "검사에서는 대상 언어 비율, 핵심 숫자의 유지, 고유명사 처리, 코드 블록과 내부 자리표시자의 존재 여부를 확인합니다."
            "문제가 발견되면 기사, 언어, 필드와 원인을 보고서에 기록하여 수정한 뒤 그림자 작업을 다시 실행할 수 있게 합니다."
            "이 작업은 페이지 배포를 시작하지 않고 뉴스 데이터를 커밋하지 않으므로 운영 데이터는 안정적인 상태를 유지합니다."
            "사람이 검토할 때는 사건의 주체, 숫자, 날짜, 플랫폼과 게임 이름을 대조하고 문장이 자연스럽고 사실이 충분한지 평가합니다."
            "여러 기사의 결과를 통해 실제 모델에 필요한 요청 수, 입력과 출력 규모, 소요 시간과 예상 비용을 측정할 수 있습니다."
            "현재 결과는 모의 생성기로 만든 테스트 문장이며 모델 품질 평가나 운영 전환 판단에 사용할 수 없습니다."
            "실제 모델 검증에는 저장소 비밀 정보에 인증 키를 설정하고 수동으로 그림자 워크플로를 실행해야 합니다."
            "실제 샘플이 사람의 검토, 숫자 정확성, 언어 검사와 비용 조건을 통과한 뒤에만 단계적 공개를 논의할 수 있습니다."
            "그 전까지 기존 번역 서비스가 공개 뉴스와 RSS의 유일한 생성 경로로 유지됩니다."
            "이번 샘플에는 여러 출처와 다양한 본문 길이를 포함하여 장문 분할, 동시 요청과 오류 처리를 확인합니다."
            "보고서에는 성공, 실패, 경고와 요청 횟수를 기록하여 모델과 프롬프트를 비교하는 데 사용합니다."
            "빈 출력, 잘못된 언어, 숫자 누락 또는 원문에 없는 수량이 발견되면 해당 샘플은 조용히 통과하지 않고 실패로 표시됩니다."
            f"이 샘플에서 확인한 고유명사는 {term_text}, 핵심 숫자는 {number_text}입니다."
        )
    else:
        raise ValueError(f"不支持的影子语言：{language}")

    if language in {"zh-CN", "ja", "ko"}:
        boundary = max(
            content.rfind("。", 0, 540),
            content.rfind("！", 0, 540),
            content.rfind("？", 0, 540),
            content.rfind(".", 0, 540),
        )
        content = content[: boundary + 1] if boundary >= 300 else content[:540]
        if language == "zh-CN":
            content += f"相关专名：{term_text}。关键数字：{number_text}。"
        elif language == "ja":
            content += f"関連する固有名詞は {term_text}、重要な数値は {number_text} です。"
        else:
            content += f"관련 고유명사는 {term_text}, 핵심 숫자는 {number_text}입니다."
    else:
        words = content.split()
        content = " ".join(words[:330])
        content += f" Relevant entities include {term_text}. Key quantities include {number_text}."
    return GenerationResult(
        title=title,
        summary=summary,
        content=content,
        usage=ChatUsage(),
        requests=0,
    )


def generate_article(
    *,
    client: OpenAICompatibleClient | None,
    article: dict[str, Any],
    language: str,
    glossary: Glossary,
    config: ShadowConfig,
) -> GenerationResult:
    if config.engine == "mock":
        return _mock_generation(article, language, glossary)
    if client is None:
        raise RuntimeError("真实影子任务缺少 LLM 客户端。")

    body = str(article.get("originalContent") or "").strip()
    truncated = False
    if len(body) > config.max_body_chars:
        body = f"{body[: config.max_body_chars - 3000]}\n\n[...]\n\n{body[-3000:]}"
        truncated = True

    if len(body) <= 6000:
        payload = dict(article)
        payload["originalContent"] = body
        result, usage = client.complete_json(
            system_prompt=f"{SYSTEM_PROMPT}\n目标语言：{language}",
            user_prompt=_article_prompt(payload, language, glossary),
        )
        return GenerationResult(
            title=str(result.get("title") or ""),
            summary=str(result.get("summary") or ""),
            content=str(result.get("content") or ""),
            usage=usage,
            requests=1,
            body_truncated=truncated,
        )

    chunks = split_body(body)
    facts = []
    total_usage = ChatUsage()
    request_count = 0
    for chunk in chunks:
        map_result, usage = client.complete_json(
            system_prompt=f"{MAP_PROMPT}\n目标语言：{language}",
            user_prompt=chunk,
        )
        total_usage += usage
        request_count += 1
        fact = str(map_result.get("facts") or "").strip()
        if not fact:
            raise RuntimeError("长文 map 阶段返回空事实清单。")
        facts.append(fact)

    reduce_input = json.dumps(
        {
            "目标语言": language,
            "标题": article.get("originalTitle", ""),
            "来源摘要": article.get("originalSummary", ""),
            "事实清单": facts,
            "术语对照": glossary.prompt_lines(
                f"{article.get('originalTitle', '')}\n{article.get('originalSummary', '')}",
                language,
            ),
        },
        ensure_ascii=False,
    )
    result, usage = client.complete_json(
        system_prompt=f"{REDUCE_PROMPT}\n目标语言：{language}",
        user_prompt=reduce_input,
    )
    total_usage += usage
    request_count += 1
    return GenerationResult(
        title=str(result.get("title") or ""),
        summary=str(result.get("summary") or ""),
        content=str(result.get("content") or ""),
        usage=total_usage,
        requests=request_count,
        body_truncated=truncated,
    )


@lru_cache(maxsize=1)
def _zh_tw_converter() -> Callable[[str], str]:
    try:
        from opencc import OpenCC
    except ImportError as error:
        raise RuntimeError("缺少 OpenCC，无法为影子数据生成繁体中文。") from error
    return OpenCC("s2twp").convert


def run_shadow(
    config: ShadowConfig,
    *,
    client: OpenAICompatibleClient | None = None,
    glossary: Glossary | None = None,
    zh_tw_converter: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    glossary = glossary or load_glossary()
    convert_zh_tw = zh_tw_converter or _zh_tw_converter()
    source = json.loads(config.input_file.read_text(encoding="utf-8"))
    all_articles = source.get("articles") or []
    selected = select_articles(all_articles, config.limit)
    languages = (
        (config.language,)
        if config.language in GENERATED_LANGUAGES
        else GENERATED_LANGUAGES
    )
    jobs = [
        (article, language)
        for article in selected
        for language in languages
    ]

    results: dict[tuple[str, str], GenerationResult] = {}
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if config.engine == "llm" and client is None:
        raise RuntimeError("真实影子任务缺少 LLM 客户端。")

    with ThreadPoolExecutor(max_workers=max(1, config.concurrency)) as executor:
        future_map = {
            executor.submit(
                generate_article,
                client=client,
                article=article,
                language=language,
                glossary=glossary,
                config=config,
            ): (article, language)
            for article, language in jobs
        }
        for future in as_completed(future_map):
            article, language = future_map[future]
            try:
                result = future.result()
                gate_failures, gate_warnings = validate_result(
                    article=article,
                    language=language,
                    result=result,
                    glossary=glossary,
                )
                if gate_failures:
                    failures.append(
                        {
                            "articleId": article_id(article),
                            "title": article.get("originalTitle", ""),
                            "language": language,
                            "failures": gate_failures,
                        }
                    )
                for warning in gate_warnings:
                    warnings.append(
                        {
                            "articleId": article_id(article),
                            "title": article.get("originalTitle", ""),
                            "language": language,
                            "warning": warning,
                        }
                    )
                results[(article_id(article), language)] = result
            except Exception as error:
                failures.append(
                    {
                        "articleId": article_id(article),
                        "title": article.get("originalTitle", ""),
                        "language": language,
                        "failures": [f"{type(error).__name__}：{error}"],
                    }
                )

    output_articles = []
    for article in selected:
        identifier = article_id(article)
        generated = {}
        for language in GENERATED_LANGUAGES:
            result = results.get((identifier, language))
            if result is None:
                continue
            generated[language] = {
                "title": result.title.strip(),
                "summary": result.summary.strip(),
                "content": result.content.strip(),
            }
        if config.language:
            if config.language not in generated:
                continue
        elif any(
            language not in generated for language in GENERATED_LANGUAGES
        ):
            continue
        if "zh-CN" in generated:
            zh = generated["zh-CN"]
            generated["zh-TW"] = {
                "title": convert_zh_tw(zh["title"]).strip(),
                "summary": convert_zh_tw(zh["summary"]).strip(),
                "content": convert_zh_tw(zh["content"]).strip(),
            }
        generated = {
            language: generated[language]
            for language in SUPPORTED_LANGUAGES
            if language in generated
        }
        output_articles.append(
            {
                "id": identifier,
                "url": article.get("url"),
                "source": article.get("source"),
                "sourceKey": article.get("sourceKey"),
                "sourceTypeKey": article.get("sourceTypeKey"),
                "categoryKey": article.get("categoryKey"),
                "sourceLanguage": article.get("sourceLanguage", "en"),
                "publishedAt": article.get("publishedAt"),
                "timestamp": int(article.get("timestamp") or 0),
                "heatScore": int(article.get("heatScore") or 0),
                "sourceHash": source_hash(article),
                "originalTitle": article.get("originalTitle"),
                "originalSummary": article.get("originalSummary"),
                "translations": generated,
                "translationInfo": {
                    language: {
                        "source": (
                            "opencc"
                            if language == "zh-TW"
                            else "mock"
                            if config.engine == "mock"
                            else "llm"
                        ),
                        "model": (
                            "mock-shadow"
                            if config.engine == "mock"
                            else getattr(client, "model", "")
                        ),
                        "glossaryVersion": glossary.version,
                    }
                    for language in generated
                },
                "partialShadow": bool(config.language),
            }
        )

    total_usage = ChatUsage()
    request_count = 0
    body_truncated = 0
    for result in results.values():
        total_usage += result.usage
        request_count += result.requests
        body_truncated += int(result.body_truncated)
    estimated_cost = (
        total_usage.input_tokens * config.price_input_cny_per_m
        + total_usage.output_tokens * config.price_output_cny_per_m
    ) / 1_000_000
    duration_seconds = round(time.monotonic() - started, 3)

    shadow_news = {
        "schemaVersion": 3,
        "shadow": True,
        "supportedLanguages": list(SUPPORTED_LANGUAGES),
        "defaultLanguage": "zh-CN",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timezone": source.get("timezone", "Asia/Hong_Kong"),
        "periodHours": int(source.get("periodHours") or 24),
        "sourceCount": len({article.get("sourceKey") for article in selected}),
        "translation": {
            "engine": "mock" if config.engine == "mock" else "llm",
            "model": "mock-shadow" if config.engine == "mock" else getattr(client, "model", ""),
            "promptVersion": "shadow-v1",
            "glossaryVersion": glossary.version,
            "contentPolicy": CONTENT_MODE,
            "digestChars": 500,
            "articlesSelected": len(selected),
            "articlesGenerated": len(output_articles),
            "requests": request_count,
            "tokens": {
                "input": total_usage.input_tokens,
                "output": total_usage.output_tokens,
                "estimated": total_usage.estimated,
            },
            "estimatedCostCny": round(estimated_cost, 6),
            "durationSeconds": duration_seconds,
        },
        "articles": output_articles,
    }
    report = {
        "status": "passed" if not failures else "failed",
        "shadow": True,
        "engine": shadow_news["translation"]["engine"],
        "model": shadow_news["translation"]["model"],
        "generatedAt": shadow_news["generatedAt"],
        "selectedArticles": len(selected),
        "generatedArticles": len(output_articles),
        "requestedLanguages": list(languages),
        "requests": request_count,
        "tokens": shadow_news["translation"]["tokens"],
        "estimatedCostCny": round(estimated_cost, 6),
        "durationSeconds": duration_seconds,
        "truncatedBodies": body_truncated,
        "failures": failures,
        "warnings": warnings,
        "productionFilesModified": False,
    }
    (config.output_dir / "news.shadow.json").write_text(
        json.dumps(shadow_news, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (config.output_dir / "translation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("## 游闻 LLM 影子验证\n\n")
            handle.write(f"- 结果：{report['status']}\n")
            handle.write(f"- 引擎：{report['engine']} / {report['model']}\n")
            handle.write(f"- 抽样 / 生成：{len(selected)} / {len(output_articles)}\n")
            handle.write(f"- 请求数：{request_count}\n")
            handle.write(f"- 预计成本：¥{estimated_cost:.6f}\n")
            handle.write(f"- 耗时：{duration_seconds:.1f} 秒\n")
            handle.write(f"- 失败 / 警告：{len(failures)} / {len(warnings)}\n")
            handle.write("- 生产文件：未修改\n")
    return report
