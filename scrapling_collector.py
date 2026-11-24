#!/usr/bin/env python3
"""使用 Scrapling 从固定新闻来源采集标题、摘要和原文发布时间。"""

from __future__ import annotations

import concurrent.futures
import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lxml import etree, html as lxml_html
from scrapling.fetchers import Fetcher

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
RULE = CONFIG["采集规则"]
USER_AGENT = RULE["用户代理"]
TIMEOUT = float(os.environ.get("SCRAPLING_TIMEOUT_SECONDS", "25"))
SOURCE_LIMIT = int(os.environ.get("SCRAPLING_SOURCE_LIMIT", "0"))
OUTPUT_FILE = Path(os.environ.get("SCRAPLING_OUTPUT_FILE", ROOT / "scrapling-raw.json"))
COMPARE_FILE = Path(os.environ.get("COMPARE_NEWS_FILE", ROOT / "news.json"))
try:
    TIMEZONE = ZoneInfo(CONFIG["默认时区"])
except ZoneInfoNotFoundError:
    TIMEZONE = timezone(timedelta(hours=8), name="Asia/Hong_Kong")


def clean_text(value: Any) -> str:
    """清理元数据中的标签、实体和多余空白。"""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_datetime(value: Any) -> datetime | None:
    """解析新闻站常见的 ISO 8601 时间。"""
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def child_text(element: etree._Element, name: str) -> str:
    for child in element:
        if local_name(child.tag) == name:
            return clean_text("".join(child.itertext()))
    return ""


def nested_values(element: etree._Element, name: str) -> list[str]:
    values = []
    for child in element.iter():
        if local_name(child.tag) == name:
            value = clean_text("".join(child.itertext()))
            if value:
                values.append(value)
    return values


def parse_sitemap(xml_body: bytes) -> tuple[str, list[dict[str, str]]]:
    """把 sitemap 解析为子 sitemap 或新闻 URL 条目。"""
    root = etree.fromstring(xml_body)
    kind = local_name(root.tag)
    entries: list[dict[str, str]] = []
    if kind == "sitemapindex":
        for element in root:
            if local_name(element.tag) != "sitemap":
                continue
            entries.append(
                {
                    "loc": child_text(element, "loc"),
                    "lastmod": child_text(element, "lastmod"),
                }
            )
    elif kind == "urlset":
        for element in root:
            if local_name(element.tag) != "url":
                continue
            entry = {
                "loc": child_text(element, "loc"),
                "lastmod": child_text(element, "lastmod"),
            }
            for name in ("publication_date", "title"):
                values = nested_values(element, name)
                if values:
                    entry[name] = values[0]
            entries.append(entry)
    return kind, [entry for entry in entries if entry.get("loc")]


def find_json_ld_value(value: Any, target: str) -> str:
    """递归读取 JSON-LD 中可能位于 @graph 或数组里的字段。"""
    if isinstance(value, dict):
        direct = value.get(target)
        if isinstance(direct, str) and direct.strip():
            return clean_text(direct)
        for child in value.values():
            found = find_json_ld_value(child, target)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_json_ld_value(child, target)
            if found:
                return found
    return ""


def find_json_ld_article(value: Any) -> tuple[int, dict[str, str]]:
    """在所有 JSON-LD 节点中选择最可信的文章元数据。"""
    nodes: list[dict[str, Any]] = []

    def collect(item: Any) -> None:
        if isinstance(item, dict):
            nodes.append(item)
            for child in item.values():
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)

    collect(value)
    article_types = {
        "article": 100,
        "newsarticle": 100,
        "reportagenewsarticle": 100,
        "blogposting": 95,
        "webpage": 40,
    }
    best_score = -1
    best: dict[str, str] = {}
    for node in nodes:
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        score = max((article_types.get(str(item).lower(), 0) for item in types), default=0)
        title = clean_text(node.get("headline") or node.get("name") or "")
        published = clean_text(
            node.get("datePublished") or node.get("dateCreated") or ""
        )
        summary = clean_text(node.get("description") or "")
        if title:
            score += 10
        if published:
            score += 20
        if summary:
            score += 5
        if score > best_score:
            best_score = score
            best = {
                "title": title,
                "published": published,
                "summary": summary,
            }
    return best_score, best


def extract_article_metadata(page_body: bytes, encoding: str = "utf-8") -> dict[str, str]:
    """从新闻页 JSON-LD、Open Graph 和语义 HTML 中提取公开元数据。"""
    page = lxml_html.fromstring(
        page_body,
        parser=lxml_html.HTMLParser(encoding=encoding or "utf-8"),
    )

    def meta(*xpaths: str) -> str:
        for xpath in xpaths:
            values = page.xpath(xpath)
            if values:
                return clean_text(values[0])
        return ""

    json_ld: dict[str, str] = {}
    json_ld_score = -1
    for raw in page.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            candidate = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        score, selected = find_json_ld_article(candidate)
        if score > json_ld_score:
            json_ld_score = score
            json_ld = selected

    title = (
        json_ld.get("title")
        or meta('//meta[@property="og:title"]/@content', '//meta[@name="twitter:title"]/@content')
        or clean_text(" ".join(page.xpath("//h1//text()")))
    )
    summary = (
        json_ld.get("summary")
        or meta(
            '//meta[@name="description"]/@content',
            '//meta[@property="og:description"]/@content',
            '//meta[@name="twitter:description"]/@content',
        )
    )
    if not summary:
        paragraphs = [
            clean_text(" ".join(item.xpath(".//text()")))
            for item in page.xpath("//article//p | //main//p")
        ]
        summary = next((item for item in paragraphs if len(item) >= 40), "")

    published = (
        json_ld.get("published")
        or meta(
            '//meta[@property="article:published_time"]/@content',
            '//meta[@name="date"]/@content',
            '//meta[@itemprop="datePublished"]/@content',
        )
        or meta('//time[@datetime]/@datetime')
    )
    return {
        "title": clean_text(title),
        "summary": clean_text(summary)[:2000],
        "published": published,
    }


def normalize_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


class RobotsCache:
    """缓存各站点的 robots.txt，抓取前检查当前用户代理是否允许。"""

    def __init__(self) -> None:
        self.cache: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.cache:
            parser = RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            try:
                response = Fetcher.get(
                    robots_url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=TIMEOUT,
                    retries=1,
                    follow_redirects=True,
                )
                if response.status < 400:
                    parser.parse(response.get_all_text().splitlines())
                    self.cache[origin] = parser
                else:
                    self.cache[origin] = None
            except Exception:
                self.cache[origin] = None
        parser = self.cache[origin]
        return parser is None or parser.can_fetch(USER_AGENT, url)


ROBOTS = RobotsCache()


def fetch_public(url: str):
    if not ROBOTS.allowed(url):
        raise PermissionError("robots.txt 不允许抓取")
    response = Fetcher.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        retries=1,
        follow_redirects=True,
    )
    if response.status >= 400:
        raise RuntimeError(f"HTTP {response.status}")
    return response


@dataclass
class Candidate:
    url: str
    sitemap_published: datetime | None = None
    sitemap_modified: datetime | None = None
    sitemap_title: str = ""


def resolve_sitemap_url(template: str, now: datetime) -> str:
    local = now.astimezone(TIMEZONE)
    return template.format(year=local.year, month=local.strftime("%B").lower())


def discover_candidates(source: dict[str, Any], now: datetime) -> list[Candidate]:
    pattern = re.compile(source["文章URL模式"])
    if source.get("列表页"):
        response = fetch_public(source["列表页"])
        page = lxml_html.fromstring(
            response.body,
            parser=lxml_html.HTMLParser(
                encoding=getattr(response, "encoding", None) or "utf-8"
            ),
        )
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for anchor in page.xpath("//a[@href]"):
            url = urljoin(str(response.url), anchor.get("href"))
            if url in seen or not pattern.search(url):
                continue
            seen.add(url)
            title = clean_text(" ".join(anchor.xpath(".//text()")))
            candidates.append(Candidate(url=url, sitemap_title=title))
            if len(candidates) >= RULE["每来源最多文章"]:
                break
        return candidates

    sitemap_url = resolve_sitemap_url(source["Sitemap"], now)
    first = fetch_public(sitemap_url)
    kind, entries = parse_sitemap(first.body)

    if kind == "sitemapindex":
        sitemap_pattern = (
            re.compile(source["Sitemap匹配"]) if source.get("Sitemap匹配") else None
        )
        selected = [
            entry
            for entry in entries
            if not sitemap_pattern or sitemap_pattern.search(entry["loc"])
        ]
        if not selected:
            raise ValueError("sitemap 索引中没有匹配分片")
        selected.sort(
            key=lambda item: parse_datetime(item.get("lastmod"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        _, entries = parse_sitemap(fetch_public(selected[0]["loc"]).body)

    cutoff = now - timedelta(hours=CONFIG["采集规则"]["时间范围小时"])
    candidates: list[Candidate] = []
    for entry in entries:
        url = entry["loc"]
        if not pattern.search(url):
            continue
        published = parse_datetime(entry.get("publication_date"))
        modified = parse_datetime(entry.get("lastmod"))
        if published and published < cutoff:
            continue
        if not published and modified and modified < cutoff:
            continue
        candidates.append(
            Candidate(
                url=url,
                sitemap_published=published,
                sitemap_modified=modified,
                sitemap_title=clean_text(entry.get("title", "")),
            )
        )

    candidates.sort(
        key=lambda item: item.sitemap_published
        or item.sitemap_modified
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return candidates[: RULE["每来源最多文章"]]


def heat_score(article: dict[str, Any]) -> int:
    value = article["originalTitle"]
    hashed = 2166136261
    for character in value:
        hashed ^= ord(character)
        hashed = (hashed * 16777619) & 0xFFFFFFFF
    official = 6 * 60 * 60 * 1000 if article["sourceTypeKey"] == "official" else 0
    depth = min(len(article["originalSummary"]), 1200) * 100
    return round(article["timestamp"] + official + hashed + depth)


def collect_source(source: dict[str, Any], now: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result: dict[str, Any] = {
        "name": source["名称"],
        "ok": False,
        "discovered": 0,
        "retained": 0,
        "errors": [],
    }
    articles: list[dict[str, Any]] = []
    try:
        candidates = discover_candidates(source, now)
        result["discovered"] = len(candidates)
        if not candidates:
            result["errors"].append("过去 24 小时未发现可确认发布时间的候选文章")
            return articles, result
        cutoff = now - timedelta(hours=CONFIG["采集规则"]["时间范围小时"])

        def read_candidate(candidate: Candidate) -> dict[str, Any] | None:
            try:
                response = fetch_public(candidate.url)
                metadata = extract_article_metadata(
                    response.body,
                    getattr(response, "encoding", None) or "utf-8",
                )
                published = parse_datetime(metadata["published"]) or candidate.sitemap_published
                if not published or published < cutoff:
                    return None
                title = metadata["title"] or candidate.sitemap_title
                if not title or not metadata["summary"]:
                    return None
                article = {
                    "originalTitle": title,
                    "originalSummary": metadata["summary"],
                    "url": candidate.url,
                    "publishedAt": published.isoformat().replace("+00:00", "Z"),
                    "timestamp": int(published.timestamp() * 1000),
                    "source": source["名称"],
                    "sourceKey": source["来源键"],
                    "sourceLanguage": source["语言"],
                    "sourceTypeKey": source["类型键"],
                    "categoryKey": source["分类键"],
                }
                article["heatScore"] = heat_score(article)
                return article
            except Exception as error:
                result["errors"].append(
                    f"{candidate.url}：{type(error).__name__}：{error}"
                )
                return None

        time.sleep(float(RULE["请求间隔秒"]))
        with concurrent.futures.ThreadPoolExecutor(max_workers=int(RULE["并发数"])) as executor:
            for article in executor.map(read_candidate, candidates):
                if article:
                    articles.append(article)
        result["retained"] = len(articles)
        result["ok"] = bool(articles)
        if articles:
            result["latestPublishedAt"] = max(
                article["publishedAt"] for article in articles
            )
        else:
            result["errors"].append(
                "候选文章均早于过去 24 小时或缺少可确认的文章元数据"
            )
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}：{error}")
    return articles, result


def compare_with_current(report: dict[str, Any]) -> dict[str, Any]:
    try:
        current = json.loads(COMPARE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False}
    current_urls = {
        normalize_url(item.get("url", "")) for item in current.get("articles", [])
    }
    collected_urls = {normalize_url(item.get("url", "")) for item in report["articles"]}
    now = parse_datetime(report["generatedAt"])
    today = now.astimezone(TIMEZONE).date() if now else None
    return {
        "available": True,
        "currentArticles": len(current_urls),
        "collectedArticles": len(collected_urls),
        "overlap": len(current_urls & collected_urls),
        "collectorOnly": len(collected_urls - current_urls),
        "currentOnly": len(current_urls - collected_urls),
        "collectedHongKongToday": sum(
            1
            for article in report["articles"]
            if datetime.fromtimestamp(article["timestamp"] / 1000, TIMEZONE).date()
            == today
        ),
    }


def write_summary(report: dict[str, Any]) -> None:
    comparison = report["comparison"]
    failed = [source for source in report["sourceResults"] if not source["ok"]]
    lines = [
        "## Scrapling 正式采集",
        "",
        f"- 保留新闻：{len(report['articles'])} 条",
        f"- 来源成功：{report['sourceCount'] - len(failed)}/{report['sourceCount']}",
        f"- 香港当天：{comparison.get('collectedHongKongToday', 0)} 条",
        f"- 与当前已发布数据重叠：{comparison.get('overlap', 0)} 条",
        f"- 本次新增：{comparison.get('collectorOnly', 0)} 条",
    ]
    if failed:
        lines.extend(["", "### 失败来源", ""])
        lines.extend(
            f"- {source['name']}：{'；'.join(source['errors'][:3])}"
            for source in failed
        )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> int:
    now = datetime.now(timezone.utc)
    sources = [source for source in CONFIG["来源"] if source.get("启用")]
    if SOURCE_LIMIT > 0:
        sources = sources[:SOURCE_LIMIT]
    all_articles: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source in sources:
        articles, result = collect_source(source, now)
        source_results.append(result)
        for article in articles:
            key = normalize_url(article["url"])
            if key not in seen:
                seen.add(key)
                all_articles.append(article)

    all_articles.sort(
        key=lambda item: (item["heatScore"], item["timestamp"], item["url"]),
        reverse=True,
    )
    report = {
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "timezone": CONFIG["默认时区"],
        "periodHours": CONFIG["采集规则"]["时间范围小时"],
        "sourceCount": len(sources),
        "articles": all_articles,
        "sourceResults": source_results,
    }
    report["comparison"] = compare_with_current(report)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_summary(report)
    return 0 if all_articles else 1


if __name__ == "__main__":
    raise SystemExit(main())
