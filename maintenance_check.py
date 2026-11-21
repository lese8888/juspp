#!/usr/bin/env python3
"""游闻项目的定期静态健康检查。"""

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
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
    ".github/workflows/game-news.yml",
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


def main():
    errors = []
    passed = []

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
            or urlsplit(str(source.get("订阅", ""))).scheme not in {"http", "https"}
            or not urlsplit(str(source.get("订阅", ""))).netloc
        ]
        require("RSS 来源配置完整", bool(active) and not invalid_sources,
                "没有可用订阅源" if not active else "地址无效：" + "、".join(invalid_sources))

    report = parsed.get("news.json")
    articles = report.get("articles") if isinstance(report, dict) else None
    if isinstance(report, dict) and isinstance(articles, list):
        require("新闻报告标记为简体中文", report.get("contentLanguage") == "zh-CN",
                str(report.get("contentLanguage", "未设置")))
        incomplete = []
        duplicate_urls = set()
        seen_urls = set()
        for index, article in enumerate(articles, start=1):
            if not isinstance(article, dict):
                incomplete.append(f"第 {index} 条不是对象")
                continue
            for key in ("title", "url", "timestamp", "source", "originalTitle", "videoScript"):
                if article.get(key) in (None, ""):
                    incomplete.append(f"第 {index} 条缺少 {key}")
            if article.get("title") and not HAN_RE.search(str(article["title"])):
                incomplete.append(f"第 {index} 条标题没有中文")
            if article.get("summary") and not HAN_RE.search(str(article["summary"])):
                incomplete.append(f"第 {index} 条摘要没有中文")
            if article.get("videoScript") and not HAN_RE.search(str(article["videoScript"])):
                incomplete.append(f"第 {index} 条口播稿没有中文")
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
        if duplicate_urls:
            incomplete.append(f"有 {len(duplicate_urls)} 个重复链接")
        require("新闻条目字段完整且有中文口播稿", not incomplete,
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
                    if item.findtext("title") != article.get("title") or item.findtext("link") != article.get("url"):
                        rss_valid = False
                        break
            require("RSS XML 完整且与新闻数据一致", rss_valid,
                    "XML、语言、条目数量或内容不匹配")
        except (ET.ParseError, OSError) as error:
            require("RSS XML 完整且与新闻数据一致", False, str(error))

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

    print(f"\n检查汇总：{len(passed)} 项通过，{len(errors)} 项失败。")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("## 游闻项目健康检查\n\n")
            summary.write(f"- 通过：{len(passed)} 项\n- 失败：{len(errors)} 项\n")
            if errors:
                summary.write("\n### 需要处理\n\n")
                summary.writelines(f"- {item}\n" for item in errors)
            else:
                summary.write("\n冲突标记、关键文件、新闻数据、RSS、网页资源与语法检查均通过。\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
