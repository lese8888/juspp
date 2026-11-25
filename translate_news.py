from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from html import unescape
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from translation.protected_articles import validate_candidate_articles


ROOT = Path(__file__).resolve().parent
SUPPORTED_LANGUAGES = ("zh-CN", "zh-TW", "en", "ja", "ko")
DEFAULT_LANGUAGE = "zh-CN"
TARGET_MODEL_CODES = {"zh-CN": "zh", "zh-TW": "zh", "ja": "ja", "ko": "ko"}
DEFAULT_SITE_URL = "https://youwen-game-news.game-intel-brief.workers.dev/"
LEGACY_CATEGORY_KEYS = {
    "平台公告": "platform",
    "行业与商业": "industry",
    "开发与行业": "development",
    "日本与全球游戏新闻": "japan-global",
    "日本游戏新闻": "japan",
    "PC 游戏": "pc",
    "任天堂游戏": "nintendo",
    "PlayStation 游戏": "playstation",
    "移动游戏": "mobile",
}

# 长术语放在前面，避免较短术语先替换后破坏完整品牌名。
GLOSSARY = (
    "Avatar Legends: The Fighting Game",
    "The Pokémon Company",
    "Pokémon Champions",
    "Nintendo Everything",
    "Xbox Series X|S",
    "Nintendo Switch 2",
    "PlayStation 5",
    "Grand Theft Auto",
    "Rockstar Games",
    "GeForce NOW",
    "Half-Life 2",
    "Resistance Crossbow",
    "Nintendo Switch",
    "Xbox Series X",
    "Xbox Series S",
    "Steam Deck",
    "Take-Two",
    "PlayStation",
    "Nintendo",
    "Rockstar",
    "Switch 2",
    "Switch",
    "Xbox",
    "Steam",
    "GTA 6",
    "NVIDIA",
    "DLC",
    "AI",
)

CHINESE_TERM_FIXES = (
    (r"下面是倒闭", "详情如下"),
    (r"(?:Nintendo|Nintendo 公司)\s*每件事", "Nintendo Everything"),
    (r"阶级", "职业"),
    (r"特征", "功能"),
    (r"任天堂(?:的)?\s*开关\s*2", "任天堂 Switch 2"),
    (r"任天堂(?:的)?\s*开关", "任天堂 Switch"),
    (r"(?:开关|交换)\s*2", "Switch 2"),
    (r"(?:开关|交换)", "Switch"),
    (r"自由(?:转会|对游戏)?(?:移动|手机游戏)?", "免费手游"),
    (r"(?:字符|人物)", "角色"),
    (r"余额(更新|补丁|调整)", r"平衡性\1"),
)


class TranslationService(Protocol):
    def prepare(self) -> None: ...

    def translate(self, text: str, target_language: str) -> str: ...


class OfflineTranslationService:
    def __init__(
        self,
        model_translator: Callable[[str, str], str] | None = None,
        zh_tw_converter: Callable[[str], str] | None = None,
    ) -> None:
        self._model_translator = model_translator
        self._zh_tw_converter = zh_tw_converter
        self._prepared = False

    def prepare(self) -> None:
        if self._prepared:
            return
        if self._model_translator is None:
            self._model_translator = self._install_and_bind_models()
        if self._zh_tw_converter is None:
            try:
                from opencc import OpenCC
            except ImportError as error:
                raise RuntimeError("缺少 OpenCC，无法生成繁体中文。") from error
            converter = OpenCC("s2twp")
            self._zh_tw_converter = converter.convert
        self._prepared = True

    def _install_and_bind_models(self) -> Callable[[str, str], str]:
        try:
            import argostranslate.package
            import argostranslate.translate
        except ImportError as error:
            raise RuntimeError("缺少 Argos Translate，无法执行离线五语翻译。") from error

        required_codes = {"zh", "ja", "ko"}
        installed = argostranslate.translate.get_installed_languages()
        installed_by_code = {language.code: language for language in installed}
        english = installed_by_code.get("en")

        missing = [
            code
            for code in required_codes
            if not english
            or code not in installed_by_code
            or english.get_translation(installed_by_code[code]) is None
        ]
        if missing:
            argostranslate.package.update_package_index()
            available = argostranslate.package.get_available_packages()
            for code in missing:
                package = next(
                    (
                        item
                        for item in available
                        if item.from_code == "en" and item.to_code == code
                    ),
                    None,
                )
                if package is None:
                    raise RuntimeError(f"离线翻译模型索引中缺少英语到 {code} 的模型。")
                argostranslate.package.install_from_path(package.download())

        installed = argostranslate.translate.get_installed_languages()
        installed_by_code = {language.code: language for language in installed}
        english = installed_by_code.get("en")
        if not english:
            raise RuntimeError("未安装英语翻译模型。")
        translators = {}
        for code in required_codes:
            target = installed_by_code.get(code)
            translation = english.get_translation(target) if target else None
            if translation is None:
                raise RuntimeError(f"英语到 {code} 的翻译模型不可用。")
            translators[code] = translation

        def translate_with_models(text: str, target_code: str) -> str:
            translation = translators.get(target_code)
            if translation is None:
                raise RuntimeError(f"不支持目标语言代码：{target_code}")
            return translation.translate(text)

        return translate_with_models

    def translate(self, text: str, target_language: str) -> str:
        self.prepare()
        source = str(text or "").strip()
        if not source:
            raise ValueError("翻译源文本为空。")
        if target_language == "en":
            return source
        if target_language not in TARGET_MODEL_CODES:
            raise ValueError(f"不支持目标语言：{target_language}")

        protected, terms = protect_glossary(source)
        target_code = TARGET_MODEL_CODES[target_language]
        translated = self._model_translator(protected, target_code)
        translated = restore_glossary(translated, terms)
        if has_unrestored_marker(translated):
            translated = self._translate_segmented(source, target_code)
        translated = normalize_translation(translated, target_language)
        translated = remove_unmatched_square_brackets(translated)
        if has_unrestored_marker(translated):
            raise RuntimeError(
                f"{target_language} 翻译后仍有未恢复的术语占位符：{source[:120]}"
            )
        if not translated.strip():
            raise RuntimeError(f"{target_language} 翻译结果为空：{source[:100]}")

        if target_language == "zh-TW":
            translated = self._zh_tw_converter(translated).strip()
            if not translated:
                raise RuntimeError(f"繁体转换结果为空：{source[:100]}")
            markers = find_unrestored_markers(translated)
            if markers:
                raise RuntimeError(
                    f"繁体转换后仍有内部术语占位符 {markers[0]}：{source[:120]}"
                )
        return translated

    def _translate_segmented(self, source: str, target_code: str) -> str:
        pattern = glossary_pattern()
        translated_parts: list[str] = []
        position = 0
        for match in pattern.finditer(source):
            before = source[position : match.start()]
            if before:
                translated_parts.append(
                    translate_segment(self._model_translator, before, target_code)
                )
            translated_parts.append(
                GLOSSARY_CANONICAL.get(match.group(0).lower(), match.group(0))
            )
            position = match.end()
        if position < len(source):
            translated_parts.append(
                translate_segment(self._model_translator, source[position:], target_code)
            )
        return "".join(translated_parts)


GLOSSARY_CANONICAL = {term.lower(): term for term in GLOSSARY}
INTERNAL_MARKER_PATTERN = re.compile(r"Q[\s-]*Z(?:[\s-]*[A-Z]){5,8}")
BRACKET_MARKER_PATTERN = re.compile(r"\[{1,4}\s*[A-Z]{5,10}\s*\]{1,4}")


def find_unrestored_markers(text: str) -> list[str]:
    """返回文本中残留的内部术语占位符，不依赖单词边界。"""
    normalized = str(text or "")
    matches = [
        *INTERNAL_MARKER_PATTERN.finditer(normalized),
        *BRACKET_MARKER_PATTERN.finditer(normalized),
    ]
    return [
        match.group(0)
        for match in sorted(matches, key=lambda item: item.start())
    ]


def has_unrestored_marker(text: str) -> bool:
    return bool(find_unrestored_markers(text))


def glossary_pattern() -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(term) for term in sorted(GLOSSARY, key=len, reverse=True)
    )
    return re.compile(
        rf"(?<![A-Za-z0-9])(?:{alternatives})(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def translate_segment(
    translator: Callable[[str, str], str],
    segment: str,
    target_code: str,
) -> str:
    if not segment.strip():
        return segment
    leading = " " if segment[0].isspace() else ""
    trailing = " " if segment[-1].isspace() else ""
    translated = translator(segment.strip(), target_code).strip()
    return f"{leading}{translated}{trailing}"


def protect_glossary(text: str) -> tuple[str, list[tuple[str, str]]]:
    protected = text
    terms: list[tuple[str, str]] = []
    for canonical in GLOSSARY:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(canonical)}(?![A-Za-z0-9])",
            re.IGNORECASE,
        )

        def replace(match: re.Match[str]) -> str:
            # 字母型标记比数字更稳定，Argos 偶尔增删括号但不会改写代码主体。
            token_id = glossary_code(len(terms))
            terms.append((token_id, canonical))
            return f"[[{token_id}]]"

        protected = pattern.sub(replace, protected)
    return protected, terms


def restore_glossary(text: str, terms: list[tuple[str, str]]) -> str:
    restored = str(text)
    mapping = dict(terms)
    token_pattern = re.compile(
        r"(?:\[\s*)*\s*(?P<token>Q[\s-]*Z(?:[\s-]*[A-Z]){5,8})\s*(?:\s*\])*"
    )

    def replace(match: re.Match[str]) -> str:
        token = re.sub(r"[^A-Z]", "", match.group("token"))
        return mapping.get(token, match.group(0))

    return token_pattern.sub(replace, restored)


def normalize_translation(text: str, target_language: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    normalized = re.sub(r"\s+([,，.!?;:。！？；：])", r"\1", normalized)
    if target_language.startswith("zh"):
        for pattern, replacement in CHINESE_TERM_FIXES:
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
        normalized = normalized.replace(",", "，").replace("!", "！").replace("?", "？")
        normalized = normalized.replace(";", "；").replace(":", "：")
        normalized = re.sub(r"\.(?=\s|$)", "。", normalized)
        normalized = re.sub(r"(?<=[\u3400-\u9fff])(?=[A-Za-z0-9])", " ", normalized)
        normalized = re.sub(r"(?<=[A-Za-z0-9])(?=[\u3400-\u9fff])", " ", normalized)
    return normalized.strip()


def glossary_code(index: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    digest = hashlib.sha1(str(index).encode("ascii")).digest()
    letters = []
    for byte in digest:
        character = alphabet[byte % 26]
        if character not in letters:
            letters.append(character)
        if len(letters) == 6:
            break
    return f"QZ{''.join(letters)}"


def remove_unmatched_square_brackets(text: str) -> str:
    stack: list[int] = []
    unmatched: set[int] = set()
    for index, character in enumerate(text):
        if character == "[":
            stack.append(index)
        elif character == "]":
            if stack:
                stack.pop()
            else:
                unmatched.add(index)
    unmatched.update(stack)
    return "".join(
        character for index, character in enumerate(text) if index not in unmatched
    )


def clean_source_text(value: object) -> str:
    text = re.sub(
        r"<!\[CDATA\[([\s\S]*?)\]\]>", r"\1", str(value or "")
    )
    text = re.sub(r"<\s*br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]*>", " ", text)
    text = unescape(text).replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"\s*(?:(?:read|continue reading)\s+(?:the\s+)?(?:full\s+)?(?:article|story|post)(?:\s+(?:on|at)\s+[A-Za-z0-9.-]+)?|read more(?:\s+(?:on|at)\s+[A-Za-z0-9.-]+)?)\s*\.?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def clean_source_content(value: object) -> str:
    text = re.sub(
        r"<!\[CDATA\[([\s\S]*?)\]\]>", r"\1", str(value or "")
    )
    text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|h[1-6]|li)>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]*>", " ", text)
    text = unescape(text).replace("\u00a0", " ")
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        normalized = re.sub(r"\s+", " ", paragraph).strip()
        if normalized:
            paragraphs.append(normalized)
    return "\n\n".join(paragraphs)


def validate_article(article: dict) -> None:
    title = str(article.get("originalTitle") or "").strip()
    summary = str(article.get("originalSummary") or "").strip()
    content = str(article.get("originalContent") or "").strip()
    url = str(article.get("url") or "").strip()
    timestamp = int(article.get("timestamp") or 0)
    if not title:
        raise ValueError("新闻缺少原始标题。")
    if not summary:
        raise ValueError(f"新闻缺少原始摘要：{title}")
    if not content:
        raise ValueError(f"新闻缺少原始正文：{title}")
    if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
        raise ValueError(f"新闻原文链接无效：{url}")
    if timestamp <= 0:
        raise ValueError(f"新闻时间戳无效：{title}")
    required_fields = (
        "sourceLanguage",
        "sourceKey",
        "sourceTypeKey",
        "categoryKey",
    )
    missing = [field for field in required_fields if not article.get(field)]
    if missing:
        raise ValueError(f"新闻缺少稳定字段 {', '.join(missing)}：{title}")
    if article["sourceLanguage"] != "en":
        raise ValueError(f"本轮仅支持英语原文：{title}")


def source_key_from_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return normalized or "unknown-source"


def normalize_article(
    article: dict, source_configs: dict[str, dict] | None = None
) -> dict:
    normalized = dict(article)
    normalized["originalTitle"] = clean_source_text(
        normalized.get("originalTitle") or normalized.get("title") or ""
    )
    normalized["originalSummary"] = clean_source_text(
        normalized.get("originalSummary") or normalized.get("summary") or ""
    )
    normalized["originalContent"] = clean_source_content(
        normalized.get("originalContent")
        or normalized.get("content")
        or normalized["originalSummary"]
    )
    source_name = str(normalized.get("source") or "").strip()
    source_config = (source_configs or {}).get(source_name, {})
    normalized["source"] = source_name or source_config.get("名称") or "Unknown"
    normalized["sourceKey"] = (
        normalized.get("sourceKey")
        or source_config.get("来源键")
        or source_key_from_name(normalized["source"])
    )
    normalized["sourceLanguage"] = (
        normalized.get("sourceLanguage") or source_config.get("语言") or "en"
    )
    normalized["sourceTypeKey"] = (
        normalized.get("sourceTypeKey")
        or source_config.get("类型键")
        or ("official" if normalized.get("sourceType") == "官方" else "media")
    )
    normalized["categoryKey"] = (
        normalized.get("categoryKey")
        or source_config.get("分类键")
        or LEGACY_CATEGORY_KEYS.get(normalized.get("category"))
        or source_key_from_name(normalized.get("category") or "other")
    )
    normalized["heatScore"] = int(
        normalized.get("heatScore")
        or int(normalized.get("timestamp") or 0)
        + (6 * 60 * 60 * 1000 if normalized["sourceTypeKey"] == "official" else 0)
    )
    return normalized


def normalize_source_articles(
    report: dict,
    source_configs: dict[str, dict] | None = None,
) -> list[dict]:
    """规范化并验证输入文章，不修改调用方传入的报告。"""

    normalized_articles = []
    for raw_article in report.get("articles", []):
        article = normalize_article(raw_article, source_configs)
        validate_article(article)
        normalized_articles.append(article)
    return normalized_articles


def translate_articles(
    report: dict,
    service: TranslationService,
    source_configs: dict[str, dict] | None = None,
) -> list[dict]:
    service.prepare()
    cache: dict[tuple[str, str], str] = {}
    translated_articles = []

    for article in normalize_source_articles(report, source_configs):
        original_title = article["originalTitle"].strip()
        original_summary = article["originalSummary"].strip()
        original_content = article["originalContent"].strip()
        translations = {}

        for language in SUPPORTED_LANGUAGES:
            if language == "en":
                translations[language] = {
                    "title": original_title,
                    "summary": original_summary,
                    "content": original_content,
                }
                continue
            translated_title = cached_translation(
                service, cache, original_title, language
            )
            translated_summary = cached_translation(
                service, cache, original_summary, language
            )
            translated_content = cached_content_translation(
                service, cache, original_content, language
            )
            if (
                not translated_title.strip()
                or not translated_summary.strip()
                or not translated_content.strip()
            ):
                raise RuntimeError(
                    f"{language} 翻译存在空标题、摘要或正文：{original_title}"
                )
            translations[language] = {
                "title": translated_title.strip(),
                "summary": translated_summary.strip(),
                "content": translated_content.strip(),
            }

        translated_articles.append(
            {
                "originalTitle": original_title,
                "originalSummary": original_summary,
                "originalContent": original_content,
                "sourceLanguage": article["sourceLanguage"],
                "sourceKey": article["sourceKey"],
                "sourceTypeKey": article["sourceTypeKey"],
                "categoryKey": article["categoryKey"],
                "source": article.get("source") or article["sourceKey"],
                "url": article["url"].strip(),
                "publishedAt": article["publishedAt"],
                "timestamp": int(article["timestamp"]),
                "heatScore": int(article.get("heatScore") or article["timestamp"]),
                "translations": translations,
            }
        )
    return sorted(
        translated_articles,
        key=lambda article: (
            -int(article["heatScore"]),
            -int(article["timestamp"]),
            article["url"],
        ),
    )


def translate_normalized_articles(
    articles: Sequence[dict],
    service: TranslationService,
) -> list[dict]:
    """用当前离线引擎翻译已经通过输入验证的文章。"""

    return translate_articles(
        {"articles": list(articles)},
        service,
    )


def normalized_url(value: object) -> str:
    """生成文章去重键；不修改 URL 的公开值。"""

    return str(value or "").strip().rstrip("/").lower()


def merge_existing_articles(
    existing_articles: Sequence[dict],
    translated_new_articles: Sequence[dict],
) -> list[dict]:
    """原样保留既有文章，只把新文章追加到末尾。"""

    merged = [dict(article) for article in existing_articles]
    existing_urls = {
        normalized_url(article.get("url")) for article in merged
    }
    for article in translated_new_articles:
        url = normalized_url(article.get("url"))
        if url and url in existing_urls:
            continue
        merged.append(dict(article))
        if url:
            existing_urls.add(url)
    return merged


def cached_translation(
    service: TranslationService,
    cache: dict[tuple[str, str], str],
    text: str,
    language: str,
) -> str:
    key = (language, text)
    if key not in cache:
        cache[key] = service.translate(text, language)
    return cache[key]


def split_translation_units(text: str, max_chars: int = 1200) -> list[str]:
    """把正文拆成适合离线模型处理的段落或句子组。"""
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", str(text or "")):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?。！？])\s+", paragraph):
            if not sentence:
                continue
            if current and len(current) + len(sentence) + 1 > max_chars:
                units.append(current.strip())
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            units.append(current.strip())
    return units


def cached_content_translation(
    service: TranslationService,
    cache: dict[tuple[str, str], str],
    text: str,
    language: str,
) -> str:
    translated = [
        cached_translation(service, cache, unit, language)
        for unit in split_translation_units(text)
    ]
    return "\n\n".join(part.strip() for part in translated if part.strip())


def xml_text(value: object) -> str:
    return escape(str(value or ""), {'"': "&quot;", "'": "&apos;"})


def make_rss(articles: list[dict], period_hours: int, site_url: str) -> str:
    items = []
    for article in articles:
        localized = article["translations"]["zh-CN"]
        content_policy = str(
            article.get("contentPolicy")
            or (article.get("translationInfo") or {}).get("contentPolicy")
            or "full_l10n"
        )
        content_mode = "内容摘要" if content_policy == "digest" else "完整译文"
        description = f"【{content_mode}】{localized['summary']}"
        published = datetime.fromtimestamp(
            int(article["timestamp"]) / 1000, timezone.utc
        )
        items.append(
            "\n    <item>"
            f"\n      <title>{xml_text(localized['title'])}</title>"
            f"\n      <link>{xml_text(article['url'])}</link>"
            f"\n      <description>{xml_text(description)}</description>"
            f"\n      <pubDate>{format_datetime(published, usegmt=True)}</pubDate>"
            f"\n      <guid isPermaLink=\"true\">{xml_text(article['url'])}</guid>"
            f"\n      <category>{xml_text(article['categoryKey'])}</category>"
            "\n    </item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>\n'
        "  <title>游闻 · 多语言游戏新闻</title>\n"
        f"  <link>{xml_text(site_url)}</link>\n"
        f"  <description>过去 {period_hours} 小时游戏新闻简体中文 RSS</description>\n"
        f"  <language>zh-CN</language>{''.join(items)}\n"
        "</channel></rss>\n"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析生产入口参数；默认引擎保持 Argos。"""

    parser = argparse.ArgumentParser(description="游闻五语生产翻译")
    parser.add_argument(
        "--translation-engine",
        choices=("argos", "llm"),
        default=os.environ.get("TRANSLATION_ENGINE", "argos"),
    )
    parser.add_argument("--baseline-news-file", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / ".agent-baselines" / "manifest.json",
    )
    return parser.parse_args(argv)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(
    news_file: Path | None = None,
    rss_file: Path | None = None,
    service: TranslationService | None = None,
    *,
    translation_engine: str | None = None,
    baseline_news_file: Path | None = None,
    manifest_path: Path | None = None,
    llm_engine: Any | None = None,
) -> None:
    news_path = news_file or Path(os.environ.get("NEWS_FILE", ROOT / "news.json"))
    rss_path = rss_file or Path(os.environ.get("RSS_FILE", ROOT / "rss.xml"))
    site_url = os.environ.get("SITE_URL", DEFAULT_SITE_URL)
    engine_name = (
        translation_engine
        or os.environ.get("TRANSLATION_ENGINE", "argos")
    ).strip().lower()
    if engine_name not in {"argos", "llm"}:
        raise ValueError(f"不支持的翻译引擎：{engine_name}")

    baseline_path = baseline_news_file
    if baseline_path is None:
        environment_baseline = os.environ.get("BASELINE_NEWS_FILE", "").strip()
        if environment_baseline:
            baseline_path = Path(environment_baseline)
    manifest = manifest_path or Path(
        os.environ.get(
            "NEWS_BASELINE_MANIFEST",
            ROOT / ".agent-baselines" / "manifest.json",
        )
    )

    report = json.loads(news_path.read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    source_configs = {source["名称"]: source for source in sources.get("来源", [])}
    normalized_articles = normalize_source_articles(report, source_configs)

    existing_articles: list[dict] = []
    if baseline_path is not None:
        existing_payload = json.loads(baseline_path.read_text(encoding="utf-8"))
        existing_articles = list(existing_payload.get("articles") or [])
    elif engine_name == "llm":
        raise RuntimeError(
            "LLM 生产模式必须通过 BASELINE_NEWS_FILE 提供既有生产数据，"
            "以保留冻结文章。"
        )

    existing_urls = {
        normalized_url(article.get("url"))
        for article in existing_articles
    }
    new_articles = [
        article
        for article in normalized_articles
        if normalized_url(article.get("url")) not in existing_urls
    ]

    translation_metadata: dict[str, Any] | None = None
    if engine_name == "argos":
        translator = service or OfflineTranslationService()
        translated_new_articles = translate_normalized_articles(
            new_articles,
            translator,
        )
        public_engine = "Argos Translate + OpenCC"
    else:
        if llm_engine is None:
            from translation.engine import (
                LLMProductionEngine,
                config_from_environment,
                create_client_from_environment,
            )

            llm_engine = LLMProductionEngine(
                client=create_client_from_environment(),
                config=config_from_environment(),
                source_document=report,
            )
        llm_result = llm_engine.translate(new_articles)
        translated_new_articles = list(llm_result.articles)
        translation_metadata = dict(llm_result.metadata)
        public_engine = (
            f"LLM / {translation_metadata.get('model') or 'unknown'}"
        )

    translated_articles = merge_existing_articles(
        existing_articles,
        translated_new_articles,
    )
    if baseline_path is not None:
        validate_candidate_articles(
            translated_articles,
            manifest,
            mode="production",
        )

    output: dict[str, Any] = {
        "schemaVersion": 2,
        "supportedLanguages": list(SUPPORTED_LANGUAGES),
        "defaultLanguage": DEFAULT_LANGUAGE,
        "generatedAt": report["generatedAt"],
        "timezone": report["timezone"],
        "periodHours": int(report["periodHours"]),
        "sourceCount": len(
            {
                str(article.get("sourceKey") or article.get("source") or "")
                for article in translated_articles
                if article.get("sourceKey") or article.get("source")
            }
        ),
        "translationEngine": public_engine,
        "articles": translated_articles,
    }
    if translation_metadata is not None:
        output["translation"] = translation_metadata
    rss = make_rss(translated_articles, output["periodHours"], site_url)
    write_text_atomic(news_path, json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    write_text_atomic(rss_path, rss)
    print(
        f"五语翻译完成：新增 {len(translated_new_articles)} 条，"
        f"总计 {len(translated_articles)} 条；简体中文 RSS 已生成。"
    )


if __name__ == "__main__":
    arguments = parse_args()
    main(
        translation_engine=arguments.translation_engine,
        baseline_news_file=arguments.baseline_news_file,
        manifest_path=arguments.manifest,
    )
