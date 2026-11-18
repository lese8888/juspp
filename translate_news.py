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
RSS_INTRO_RE = re.compile(
    r"^(?:here(?:'s| is| are)\s+the\s+(?:full details|rundown))\s*[:.!,-]*\s*",
    re.IGNORECASE,
)
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
    text = RSS_INTRO_RE.sub("", text).strip()
    text = re.sub(r"\s*\(?\s*via\s+[^)]+\)?\s*", " ", text, flags=re.IGNORECASE).strip()
    if not text or has_chinese(text):
        return text, False
    translated = argostranslate.translate.translate(text, "en", "zh").strip()
    if not translated:
        raise RuntimeError(f"翻译结果为空：{text[:100]}")
    translated = re.sub(r"\s+([,，.!?;:])", r"\1", translated)
    translated = translated.replace(",", "，").replace("!", "！").replace("?", "？").replace(";", "；").replace(":", "：")
    translated = re.sub(r"\.(?=\s|$)", "。", translated)
    translated = re.sub(r"\s+([，。！？；：])", r"\1", translated)
    return translated, True


def localize_game_terms(original, translated):
    if re.search(r"\bSwitch(?:\s*2)?\b", original):
        if re.search(r"\bNintendo Switch\b", original, re.IGNORECASE):
            translated = re.sub(r"任天堂(?:的)?\s*开关\s*2", "任天堂 Switch 2", translated)
            translated = re.sub(r"任天堂(?:的)?\s*开关", "任天堂 Switch", translated)
        translated = re.sub(r"(?:开关|交换)\s*2", "Switch 2", translated)
        translated = re.sub(r"(?:开关|交换)", "Switch", translated)
        translated = re.sub(r"Switch\s*游戏", "Switch 游戏", translated)
    if re.search(r"\bFighting Game\b", original, re.IGNORECASE):
        translated = re.sub(r"战斗游戏", "格斗游戏", translated)
    if re.search(r"\bimprov(?:e|es|ed|ing)\b", original, re.IGNORECASE):
        translated = re.sub(r"改善", "提升", translated)
    if re.search(r"\bAvatar Legends: The Fighting Game\b", original, re.IGNORECASE):
        translated = re.sub(r"(?:神通|化身|阿凡达)?传说\s*[:：]\s*(?:战斗|格斗)游戏|Avatar Legends", "《Avatar Legends: The Fighting Game》", translated, flags=re.IGNORECASE)
    elif re.search(r"\bAvatar Legends\b", original, re.IGNORECASE):
        translated = re.sub(r"神通传说|化身传说|阿凡达传说", "Avatar Legends", translated)
    if re.search(r"\bPokémon Champions\b", original, re.IGNORECASE):
        translated = re.sub(r"(?:Pokémon|宝可梦)\s*冠军", "《Pokémon Champions》", translated, flags=re.IGNORECASE)
        translated = re.sub(r"(?<!《)Pokémon Champions(?!》)", "《Pokémon Champions》", translated, flags=re.IGNORECASE)
    if re.search(r"\bThe Pokémon Company\b", original, re.IGNORECASE):
        translated = re.sub(r"(?:Pokémon|宝可梦)\s*公司", "宝可梦公司", translated, flags=re.IGNORECASE)
    if re.search(r"\bHalf-Life 2\b", original, re.IGNORECASE):
        translated = re.sub(r"半(?:生|条命)\s*2", "《Half-Life 2》", translated)
    if re.search(r"\bResistance Crossbow\b", original, re.IGNORECASE):
        translated = re.sub(r"抵抗十字弓", "Resistance Crossbow", translated)
    if re.search(r"\bSister Novitiate\b", original, re.IGNORECASE):
        translated = re.sub(r"(?:新)?修女\s*Novitiate(?:\s*(?:阶级|职业|级))?", "见习修女职业", translated, flags=re.IGNORECASE)
    if re.search(r"\bbalance\b", original, re.IGNORECASE):
        translated = re.sub(r"余额(更新|补丁|调整)", r"平衡性\1", translated)
        translated = re.sub(r"获得平衡性?(更新|补丁)", r"将迎来平衡性\1", translated)
        if re.search(r"\bnext month\b", original, re.IGNORECASE):
            translated = re.sub(r"(?:将迎来|获得)?平衡性?补丁\s*下个月更新", "下个月将迎来平衡性补丁更新", translated)
            date_pattern = r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
            translated = re.sub(r"(?:将于)?下月(?:将于|于)\s*" + date_pattern + r"\s*将迎来", r"将于 \1 进行", translated)
            translated = re.sub(r"(?:将于)?下个月(?:将于|于)\s*" + date_pattern + r"\s*将迎来", r"将于 \1 进行", translated)
    if re.search(r"\bfree.to.play\b", original, re.IGNORECASE):
        translated = re.sub(r"自由(?:转会|对游戏)?(?:移动|手机游戏)?", "免费手游", translated)
    if re.search(r"\bmoves?\b", original, re.IGNORECASE):
        translated = re.sub(r"移动更新(?:和调整)?", "招式调整", translated)
    if re.search(r"\bfeatures?\b", original, re.IGNORECASE):
        translated = re.sub(r"特征", "功能", translated)
    if re.search(r"\bcharacters?\b", original, re.IGNORECASE):
        translated = re.sub(r"字符", "角色", translated)
        translated = re.sub(r"人物", "角色", translated)
    if re.search(r"\bPatch 4\.5 Update\b", original, re.IGNORECASE):
        translated = re.sub(r"Patch\s*4\.5\s*Update", "4.5 版本更新", translated, flags=re.IGNORECASE)
    if re.search(r"\brundown\b", original, re.IGNORECASE):
        translated = re.sub(r"(?:以下是|以下为)?倒数[：:]?", "详情如下：", translated)
    translated = re.sub(r"阶级", "职业", translated)
    translated = re.sub(r"见习修女职业职业", "见习修女职业", translated)
    translated = re.sub(r"[\"“”]\s*(《[^》]+》)\s*[\"“”]", r"\1", translated)
    translated = re.sub(r"将于\s+((?:\d{4}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*日)\s+将迎来", r"将于 \1 进行", translated)
    translated = re.sub(r"(?<=[\u3400-\u9fff])(?=[A-Za-z0-9])", " ", translated)
    translated = re.sub(r"(?<=[A-Za-z0-9])(?=[\u3400-\u9fff])", " ", translated)
    translated = re.sub(r"倒闭(?=[，。,:：]|$)", "详情如下", translated)
    return translated


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
        title = localize_game_terms(original_title, title)
        summary = localize_game_terms(original_summary, summary)
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

