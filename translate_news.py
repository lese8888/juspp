import json
import os
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import argostranslate.package
import argostranslate.translate


ROOT = Path(__file__).resolve().parent
NEWS_FILE = Path(os.environ.get("NEWS_FILE", ROOT / "news.json"))
RSS_FILE = ROOT / "rss.xml"
SITE_URL = os.environ.get("SITE_URL", "https://lese8888.github.io/juspp/")
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
RSS_FOOTER_RE = re.compile(r"\s*Read the full article on\s+\S+\.?\s*$", re.IGNORECASE)
RUMOR_RE = re.compile(r"\b(report(?:ed|edly)?|rumou?r|allegedly|leak|unconfirmed|according to|sources say)\b", re.IGNORECASE)


def has_chinese(text):
    han_count = len(HAN_RE.findall(text))
    latin_count = len(LATIN_RE.findall(text))
    if han_count and not latin_count:
        return True
    return han_count >= 6 and han_count >= latin_count * 0.75


def ensure_translation_model():
    installed = argostranslate.translate.get_installed_languages()
    english = next((language for language in installed if language.code == "en"), None)
    chinese = next((language for language in installed if language.code == "zh"), None)
    if english and chinese and english.get_translation(chinese):
        return

    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    package = next(
        (item for item in available if item.from_code == "en" and item.to_code == "zh"),
        None,
    )
    if package is None:
        raise RuntimeError("离线翻译模型索引中没有英语到中文的模型。")
    argostranslate.package.install_from_path(package.download())


def translate_text(text):
    text = RSS_FOOTER_RE.sub("", str(text or "")).strip()
    if not text or has_chinese(text):
        return text, False
    translated = argostranslate.translate.translate(text, "en", "zh").strip()
    if not translated:
        raise RuntimeError(f"翻译结果为空：{text[:100]}")
    return translated, True


def video_script(article, title, summary):
    original_text = f"{article.get('originalTitle', '')} {article.get('originalSummary', '')}"
    if RUMOR_RE.search(original_text):
        reminder = "这条内容涉及媒体报道或未证实消息，发布前请确认官方是否回应。"
    elif article.get("sourceType") == "官方":
        reminder = "具体平台、地区和时间安排，请以官方公告为准。"
    else:
        reminder = "发布前请对照来源原文，核实人名、数字和发售地区。"
    body = summary or "订阅源没有提供摘要，制作视频前请打开原文补充信息。"
    return (
        f"【开场】{title}\n"
        f"【正文】{body}\n"
        f"【核实提醒】{reminder}\n"
        "【互动】你最关注这条消息的哪一点？欢迎在评论区聊聊。"
    )


def xml_text(value):
    return escape(str(value or ""), {'"': "&quot;", "'": "&apos;"})


def make_rss(articles, period_hours):
    items = []
    for article in articles:
        published = datetime.fromtimestamp(
            int(article.get("timestamp") or 0) / 1000, timezone.utc
        )
        items.append(
            "\n    <item>"
            f"\n      <title>{xml_text(article.get('title'))}</title>"
            f"\n      <link>{xml_text(article.get('url'))}</link>"
            f"\n      <description>{xml_text(article.get('summary'))}</description>"
            f"\n      <pubDate>{format_datetime(published, usegmt=True)}</pubDate>"
            f"\n      <guid isPermaLink=\"true\">{xml_text(article.get('url'))}</guid>"
            f"\n      <category>{xml_text(article.get('category') or '游戏新闻')}</category>"
            "\n    </item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>\n'
        "  <title>游闻 · 中文游戏新闻</title>\n"
        f"  <link>{xml_text(SITE_URL)}</link>\n"
        f"  <description>过去 {period_hours} 小时游戏新闻简体中文摘要与选题</description>\n"
        f"  <language>zh-CN</language>{''.join(items)}\n"
        "</channel></rss>\n"
    )


def write_atomic(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main():
    report = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    needs_translation = any(
        not has_chinese(article.get("originalTitle") or article.get("title") or "")
        or not has_chinese(article.get("originalSummary") or article.get("summary") or "")
        for article in report.get("articles", [])
    )
    if needs_translation:
        ensure_translation_model()
    translated_articles = []
    for article in report.get("articles", []):
        original_title = article.get("originalTitle") or article.get("title") or ""
        original_summary = article.get("originalSummary") or article.get("summary") or ""
        title, title_translated = translate_text(original_title)
        summary, summary_translated = translate_text(original_summary)
        article["originalTitle"] = original_title
        article["originalSummary"] = original_summary
        article["title"] = title
        article["summary"] = summary
        article["videoScript"] = video_script(article, title, summary)
        article["translationStatus"] = (
            "已转为简体中文" if title_translated or summary_translated else "原文已是中文"
        )
        translated_articles.append(article)

    report["contentLanguage"] = "zh-CN"
    report["translationEngine"] = "Argos Translate 离线模型"
    report["articles"] = translated_articles
    write_atomic(NEWS_FILE, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_atomic(RSS_FILE, make_rss(translated_articles, report.get("periodHours", 24)))
    print(f"中文转换完成：{len(translated_articles)} 条新闻，已生成中文摘要与视频口播稿。")


if __name__ == "__main__":
    main()

