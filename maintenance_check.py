#!/usr/bin/env python3
"""游闻项目的定期健康检查。"""

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CONFLICT_RE = re.compile(r"^(?:<<<<<<<(?:\s|$)|=======$|>>>>>>>(?:\s|$))", re.MULTILINE)
CODE_SUFFIXES = {".mjs", ".js", ".py", ".html", ".yml", ".yaml", ".json"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
REQUIRED_FILES = [
    "index.html",
    "news.json",
    "rss.xml",
    "sources.json",
    "collect.mjs",
    "server.mjs",
    "translate_news.py",
    "package.json",
    "requirements.txt",
    "lib/news.mjs",
    ".github/workflows/game-intel.yml",
    ".github/workflows/pages.yml",
    ".github/workflows/maintenance.yml",
    "MAINTENANCE.md",
]


class PageResources(HTMLParser):
    """收集网页标记中的静态资源地址与元素 ID。"""

    def __init__(self):
        super().__init__()
        self.resources = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        for attribute in ("href", "src"):
            value = values.get(attribute, "")
            if value:
                self.resources.append((tag, attribute, value))


def check(label, condition, detail=""):
    if condition:
        print(f"通过：{label}")
        return True
    message = f"失败：{label}"
    if detail:
        message += f"（{detail}）"
    print(message)
    return False


def local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def check_feed(source):
    """获取并解析一个 RSS 或 Atom 订阅源。"""
    name = str(source.get("名称") or "未命名来源")
    feed_url = str(source.get("订阅") or "")
    request = Request(
        feed_url,
        headers={
            "User-Agent": "GameIntelRSSHealth/1.0",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.getcode()
            if not 200 <= status < 300:
                return {"name": name, "ok": False, "detail": f"HTTP {status}"}
            payload = response.read(5_000_001)
        if len(payload) > 5_000_000:
            return {"name": name, "ok": False, "detail": "订阅内容超过 5 MB"}
        root = ET.fromstring(payload)
        root_type = local_name(root.tag)
        if root_type == "rss":
            entries = [node for node in root.iter() if local_name(node.tag) == "item"]
        elif root_type == "feed":
            entries = [node for node in root if local_name(node.tag) == "entry"]
        elif root_type == "rdf":
            entries = [node for node in root.iter() if local_name(node.tag) == "item"]
        else:
            return {"name": name, "ok": False, "detail": f"不是 RSS/Atom XML（根节点：{root_type}）"}
        if not entries:
            return {"name": name, "ok": False, "detail": "XML 有效，但没有新闻条目"}

        valid_entries = 0
        for entry in entries:
            children = [child for child in entry if isinstance(child.tag, str)]
            title = next(
                ("".join(child.itertext()).strip() for child in children if local_name(child.tag) == "title"),
                "",
            )
            links = [child for child in children if local_name(child.tag) == "link"]
            has_link = any(
                child.attrib.get("href") or "".join(child.itertext()).strip()
                for child in links
            )
            if not has_link:
                has_link = any(local_name(child.tag) == "guid" for child in children)
            if title and has_link:
                valid_entries += 1
        if not valid_entries:
            return {"name": name, "ok": False, "detail": "条目缺少标题或链接"}
        return {"name": name, "ok": True, "detail": f"{valid_entries} 条有效新闻"}
    except HTTPError as error:
        return {"name": name, "ok": False, "detail": f"HTTP {error.code}"}
    except URLError as error:
        return {"name": name, "ok": False, "detail": f"连接失败：{error.reason}"}
    except (TimeoutError, ET.ParseError, OSError, ValueError) as error:
        return {"name": name, "ok": False, "detail": f"{type(error).__name__}：{error}"}


def main():
    errors = []
    warnings = []
    passed = []
    feed_results = []

    def require(label, condition, detail=""):
        if check(label, condition, detail):
            passed.append(label)
        else:
            errors.append(label)

    missing = [name for name in REQUIRED_FILES if not (ROOT / name).is_file()]
    require("项目关键文件齐全", not missing, "缺少：" + "、".join(missing) if missing else "")

    conflict_files = []
    if not missing:
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in CODE_SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
                continue
            if path.name in {"news.json", "rss.xml"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                conflict_files.append(f"{path.relative_to(ROOT)} 无法读取：{error}")
                continue
            if CONFLICT_RE.search(text):
                conflict_files.append(str(path.relative_to(ROOT)))
    require("源代码与配置无合并冲突标记", not conflict_files, "；".join(conflict_files) if conflict_files else "")

    parsed = {}
    for name in ("package.json", "sources.json", "news.json"):
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            parsed[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"{name} JSON 格式错误")
            print(f"失败：{name} 可解析（{error}）")
        else:
            passed.append(f"{name} JSON 可解析")
            print(f"通过：{name} JSON 可解析")

    package = parsed.get("package.json")
    if isinstance(package, dict):
        scripts = package.get("scripts", {})
        references = []
        for command in scripts.values() if isinstance(scripts, dict) else []:
            references.extend(re.findall(r"\b(?:node|python3?)\s+([A-Za-z0-9_./-]+\.(?:mjs|js|py))", command))
        absent = [name for name in references if not (ROOT / name).is_file()]
        require("npm 脚本引用的程序文件存在", not absent, "缺少：" + "、".join(absent) if absent else "")

    sources = parsed.get("sources.json")
    if isinstance(sources, dict):
        source_list = sources.get("来源")
        active = [
            source for source in source_list
            if isinstance(source, dict) and source.get("启用") and source.get("订阅")
        ] if isinstance(source_list, list) else []
        invalid_sources = [
            str(source.get("名称", "未命名来源"))
            for source in active
            if not source.get("名称")
            or not source.get("来源键")
            or source.get("语言") != "en"
            or source.get("类型键") not in {"official", "media"}
            or not source.get("分类键")
            or urlsplit(str(source.get("订阅", ""))).scheme not in {"http", "https"}
            or not urlsplit(str(source.get("订阅", ""))).netloc
        ]
        require("RSS 来源配置完整", bool(active) and not invalid_sources,
                "没有可用订阅源" if not active else "地址无效：" + "、".join(invalid_sources))
        if active and not invalid_sources:
            with ThreadPoolExecutor(max_workers=min(6, len(active))) as executor:
                feed_results = list(executor.map(check_feed, active))
            for result in feed_results:
                if result["ok"]:
                    print(f"通过：RSS 源 {result['name']}（{result['detail']}）")
                else:
                    warning = f"{result['name']}：{result['detail']}"
                    warnings.append(warning)
                    print(f"警告：RSS 源 {warning}")
            healthy_count = sum(result["ok"] for result in feed_results)
            require(
                "RSS/Atom 订阅源可连接并包含有效新闻",
                healthy_count > 0,
                f"{healthy_count}/{len(feed_results)} 个源有效；" + "；".join(
                    f"{item['name']}：{item['detail']}" for item in feed_results if not item["ok"]
                ),
            )

    report = parsed.get("news.json")
    articles = report.get("articles") if isinstance(report, dict) else None
    if isinstance(report, dict) and isinstance(articles, list):
        languages = ["zh-CN", "zh-TW", "en", "ja", "ko"]
        require("新闻数据使用 schemaVersion 2", report.get("schemaVersion") == 2,
                str(report.get("schemaVersion", "未设置")))
        require("五语支持列表完整", report.get("supportedLanguages") == languages,
                str(report.get("supportedLanguages", "未设置")))
        require("默认语言为简体中文", report.get("defaultLanguage") == "zh-CN",
                str(report.get("defaultLanguage", "未设置")))
        require("新闻报告标记翻译引擎", bool(report.get("translationEngine")),
                str(report.get("translationEngine", "未设置")))
        incomplete = []
        duplicate_urls = set()
        seen_urls = set()
        previous_heat = None
        for index, article in enumerate(articles, start=1):
            if not isinstance(article, dict):
                incomplete.append(f"第 {index} 条不是对象")
                continue
            for key in (
                "originalTitle",
                "originalSummary",
                "sourceLanguage",
                "sourceKey",
                "sourceTypeKey",
                "categoryKey",
                "source",
                "url",
                "timestamp",
                "heatScore",
                "translations",
            ):
                if article.get(key) in (None, ""):
                    incomplete.append(f"第 {index} 条缺少 {key}")
            if article.get("sourceLanguage") != "en":
                incomplete.append(f"第 {index} 条原文语言不是英语")
            if article.get("sourceTypeKey") not in {"official", "media"}:
                incomplete.append(f"第 {index} 条来源类型键无效")
            if "videoScript" in article or "translationStatus" in article:
                incomplete.append(f"第 {index} 条仍包含旧口播字段")
            serialized = json.dumps(article, ensure_ascii=False)
            if re.search(r"QZ[A-Z]{5,8}|\[\[\s*\d{5,}", serialized):
                incomplete.append(f"第 {index} 条包含内部翻译标记")
            translations = article.get("translations")
            if not isinstance(translations, dict):
                incomplete.append(f"第 {index} 条 translations 不是对象")
            else:
                if list(translations.keys()) != languages:
                    incomplete.append(f"第 {index} 条五语顺序或字段不完整")
                for language in languages:
                    localized = translations.get(language)
                    if (
                        not isinstance(localized, dict)
                        or set(localized.keys()) != {"title", "summary"}
                        or not str(localized.get("title", "")).strip()
                        or not str(localized.get("summary", "")).strip()
                    ):
                        incomplete.append(f"第 {index} 条 {language} 标题或摘要为空")
            url = str(article.get("url", "")).rstrip("/").lower()
            if url:
                if url in seen_urls:
                    duplicate_urls.add(url)
                seen_urls.add(url)
                parsed_url = urlsplit(str(article.get("url", "")))
                if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                    incomplete.append(f"第 {index} 条来源链接无效")
            if not isinstance(article.get("timestamp"), (int, float)) or article.get("timestamp", 0) <= 0:
                incomplete.append(f"第 {index} 条时间戳无效")
            if not isinstance(article.get("heatScore"), (int, float)) or article.get("heatScore", 0) <= 0:
                incomplete.append(f"第 {index} 条热度值无效")
            elif previous_heat is not None and article["heatScore"] > previous_heat:
                incomplete.append(f"第 {index} 条未按 heatScore 降序排列")
            else:
                previous_heat = article["heatScore"]
        if duplicate_urls:
            incomplete.append(f"有 {len(duplicate_urls)} 个重复链接")
        require("新闻条目五语字段完整且排序稳定", not incomplete,
                "；".join(incomplete[:12]) if incomplete else "")
    else:
        require("新闻数据包含 articles 数组", False)

    rss_path = ROOT / "rss.xml"
    if rss_path.is_file() and isinstance(articles, list):
        try:
            root = ET.parse(rss_path).getroot()
            channel = root.find("channel")
            items = channel.findall("item") if channel is not None else []
            rss_valid = (
                root.tag == "rss"
                and channel is not None
                and channel.findtext("language") == "zh-CN"
                and len(items) == len(articles)
            )
            if rss_valid:
                for item, article in zip(items, articles):
                    localized = article.get("translations", {}).get("zh-CN", {})
                    if (
                        item.findtext("title") != localized.get("title")
                        or item.findtext("description") != localized.get("summary")
                        or item.findtext("link") != article.get("url")
                    ):
                        rss_valid = False
                        break
            require("简体中文 RSS 完整且与五语新闻数据一致", rss_valid,
                    "XML、语言、条目数量、简体标题摘要或链接不匹配")
        except (ET.ParseError, OSError) as error:
            require("简体中文 RSS 完整且与五语新闻数据一致", False, str(error))

    page = ROOT / "index.html"
    if page.is_file():
        html = page.read_text(encoding="utf-8", errors="replace")
        parser = PageResources()
        try:
            parser.feed(html)
            parser.close()
            require("网页使用简体中文文档声明",
                    '<html lang="zh-cn">' in html.lower())
            referenced_ids = set(re.findall(r"\$\(['\"]#([^'\"]+)['\"]\)", html))
            missing_ids = sorted(referenced_ids - parser.ids)
            require("脚本引用的网页元素均存在", not missing_ids,
                    "缺少 ID：" + "、".join(missing_ids) if missing_ids else "")
            required_page_ids = {"language-select", "story-list", "search-input", "source-filter", "detail-close"}
            missing_page_ids = sorted(required_page_ids - parser.ids)
            require("五语新闻站关键元素齐全", not missing_page_ids,
                    "缺少 ID：" + "、".join(missing_page_ids) if missing_page_ids else "")
            require("网页不包含旧口播工作流文案",
                    not re.search(r"口播稿|视频创作工作流|复制文案", html),
                    "仍包含旧版产品语义")

            broken = []
            for tag, attribute, value in parser.resources:
                address = urlsplit(value)
                if address.scheme or address.netloc or value.startswith("#"):
                    continue
                if address.path.startswith("/"):
                    broken.append(f"{tag} {attribute}={value} 应使用相对路径")
                    continue
                local_path = (ROOT / address.path.removeprefix("./")).resolve()
                if not local_path.is_relative_to(ROOT.resolve()) or not local_path.is_file():
                    broken.append(f"{tag} {attribute}={value} 找不到本地文件")
            require("网页静态资源路径有效且兼容 GitHub Pages 项目站点",
                    not broken, "；".join(broken) if broken else "")
        except Exception as error:
            require("网页结构可检查", False, str(error))


    checks = [
        ("node", ["--check", "collect.mjs"]),
        ("node", ["--check", "server.mjs"]),
        ("node", ["--check", "lib/news.mjs"]),
        (sys.executable, ["-m", "py_compile", "translate_news.py", "maintenance_check.py"]),
    ]
    for executable, args in checks:
        label = " ".join([executable, *args])
        try:
            result = subprocess.run(
                [executable, *args], cwd=ROOT, capture_output=True, text=True, check=False
            )
            output = (result.stdout + result.stderr).strip()
            require(f"语法检查：{label}", result.returncode == 0, output)
        except OSError as error:
            require(f"语法检查：{label}", False, str(error))

    healthy_feeds = sum(result["ok"] for result in feed_results)
    print(
        f"\n检查汇总：{len(passed)} 项通过，{len(errors)} 项失败，"
        f"{len(warnings)} 个 RSS 源需留意（{healthy_feeds}/{len(feed_results)} 个源有效）。"
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("## 游闻项目健康检查\n\n")
            summary.write(
                f"- 通过：{len(passed)} 项\n- 失败：{len(errors)} 项\n"
                f"- RSS 源正常：{healthy_feeds}/{len(feed_results)}\n"
            )
            if warnings:
                summary.write("\n### RSS 源需要留意\n\n")
                summary.writelines(f"- {item}\n" for item in warnings)
            if errors:
                summary.write("\n### 需要处理\n\n")
                summary.writelines(f"- {item}\n" for item in errors)
            if not errors and not warnings:
                summary.write("\n冲突标记、关键文件、新闻数据、全部 RSS 源、网页资源与语法检查均通过。\n")
            elif not errors:
                summary.write("\n项目检查通过；请查看上方 RSS 源警告，判断是否为临时网络问题。\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
