#!/usr/bin/env python3
"""创建并校验新闻冻结基线。

该工具只读取当前新闻文件，并将冻结副本写入独立的基线目录。它不修改
news.json、rss.xml 或任何生产发布文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROTECTED_ARTICLE_COUNT = 75


class BaselineError(RuntimeError):
    """冻结基线无法创建或校验失败。"""


def canonical_json(value: Any) -> str:
    """按稳定字段顺序生成紧凑 JSON，供哈希计算使用。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_bytes(value: bytes) -> str:
    """计算字节内容的 SHA256。"""

    return hashlib.sha256(value).hexdigest()


def load_news_document(path: Path) -> tuple[bytes, dict[str, Any]]:
    """读取新闻文件，并确认顶层结构和文章数组有效。"""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BaselineError(f"无法读取新闻文件 {path}: {exc}") from exc

    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineError(f"新闻文件不是有效 UTF-8 JSON: {path}: {exc}") from exc

    if not isinstance(document, dict):
        raise BaselineError("新闻文件顶层必须是 JSON 对象")
    articles = document.get("articles")
    if not isinstance(articles, list):
        raise BaselineError("新闻文件缺少 articles 数组")
    if not all(isinstance(article, dict) for article in articles):
        raise BaselineError("articles 中的每一项都必须是 JSON 对象")
    return raw, document


def article_key(article: dict[str, Any]) -> str:
    """返回跨版本稳定的文章键。

    当前生产数据没有文章 ID，因此优先使用 id；缺失时使用原文 URL。二者
    都缺失或不是非空字符串时立即失败，避免用数组下标掩盖重排或替换。
    """

    for field in ("id", "url"):
        value = article.get(field)
        if isinstance(value, str) and value.strip():
            return f"{field}:{value.strip()}"
    raise BaselineError("文章同时缺少有效 id 和 url，无法建立稳定基线键")


def article_hash(article: dict[str, Any]) -> str:
    """计算单篇文章全部字段的规范化哈希。"""

    return sha256_bytes(canonical_json(article).encode("utf-8"))


def describe_articles(articles: list[dict[str, Any]]) -> dict[str, Any]:
    """计算文章顺序、单篇哈希和文章块哈希。"""

    keys = [article_key(article) for article in articles]
    if len(keys) != len(set(keys)):
        duplicates = sorted(
            key for key in set(keys) if keys.count(key) > 1
        )
        raise BaselineError(f"文章稳定键重复: {duplicates}")

    hashes = [article_hash(article) for article in articles]
    return {
        "articlesSha256": sha256_bytes(
            canonical_json(articles).encode("utf-8")
        ),
        "orderedArticleKeys": keys,
        "articleHashes": dict(zip(keys, hashes)),
    }


def build_manifest(
    *,
    raw: bytes,
    document: dict[str, Any],
    baseline_path: Path,
    created_at: str,
) -> dict[str, Any]:
    """根据冻结文件生成机器可读清单。"""

    articles = document["articles"]
    article_details = describe_articles(articles)
    return {
        "manifestVersion": 1,
        "policy": {
            "description": (
                "原冻结文章的全部字段和顺序不得变化；"
                "新增文章只能显式追加到末尾"
            ),
            "protectedArticleCount": len(articles),
            "allowAppendedArticles": True,
        },
        "createdAt": created_at,
        "source": {
            "generatedAt": document.get("generatedAt"),
            "schemaVersion": document.get("schemaVersion"),
            "translationEngine": document.get("translationEngine"),
        },
        "baselinePath": baseline_path.as_posix(),
        "fileSha256": sha256_bytes(raw),
        "articlesSha256": article_details["articlesSha256"],
        "orderedArticleKeys": article_details["orderedArticleKeys"],
        "articleHashes": article_details["articleHashes"],
    }


def create_baseline(
    *,
    source_path: Path,
    baseline_dir: Path,
    expected_article_count: int | None,
    created_at: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """复制当前新闻并创建 manifest。"""

    raw, document = load_news_document(source_path)
    articles = document["articles"]
    if expected_article_count is not None:
        if len(articles) != expected_article_count:
            raise BaselineError(
                "冻结文章数量不符合预期："
                f"期望 {expected_article_count}，实际 {len(articles)}"
            )

    baseline_dir.mkdir(parents=True, exist_ok=True)
    file_hash = sha256_bytes(raw)
    baseline_path = (
        baseline_dir / f"news-{len(articles)}-{file_hash[:12]}.json"
    )
    baseline_path.write_bytes(raw)

    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    manifest = build_manifest(
        raw=raw,
        document=document,
        # 保存相对路径，避免冻结清单绑定当前工作站的绝对路径。
        baseline_path=Path(baseline_path.name),
        created_at=timestamp,
    )
    manifest_path = baseline_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return baseline_path, manifest_path, manifest


def validate_manifest(manifest: dict[str, Any]) -> int:
    """检查 manifest 必要字段并返回受保护文章数量。"""

    if manifest.get("manifestVersion") != 1:
        raise BaselineError("不支持的 manifestVersion")
    policy = manifest.get("policy")
    if not isinstance(policy, dict):
        raise BaselineError("manifest 缺少 policy")
    count = policy.get("protectedArticleCount")
    if not isinstance(count, int) or count <= 0:
        raise BaselineError("manifest.protectedArticleCount 必须是正整数")
    for field in (
        "baselinePath",
        "fileSha256",
        "articlesSha256",
        "orderedArticleKeys",
        "articleHashes",
    ):
        if field not in manifest:
            raise BaselineError(f"manifest 缺少字段 {field}")
    return count


def compare_with_baseline(
    *,
    current_path: Path,
    manifest_path: Path,
    allow_appended_articles: bool = False,
) -> dict[str, Any]:
    """比较当前新闻和冻结基线，返回结构化差异。"""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"无法读取 manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BaselineError("manifest 顶层必须是 JSON 对象")

    protected_count = validate_manifest(manifest)
    baseline_path = Path(manifest["baselinePath"])
    if not baseline_path.is_absolute():
        baseline_path = manifest_path.parent / baseline_path

    try:
        baseline_raw = baseline_path.read_bytes()
    except OSError as exc:
        raise BaselineError(f"无法读取冻结副本 {baseline_path}: {exc}") from exc
    if sha256_bytes(baseline_raw) != manifest["fileSha256"]:
        raise BaselineError("冻结副本文件哈希与 manifest 不一致")

    baseline_document = json.loads(baseline_raw.decode("utf-8"))
    baseline_articles = baseline_document.get("articles")
    if not isinstance(baseline_articles, list):
        raise BaselineError("冻结副本缺少 articles 数组")
    baseline_details = describe_articles(baseline_articles)
    if baseline_details["articlesSha256"] != manifest["articlesSha256"]:
        raise BaselineError("冻结副本文章块哈希与 manifest 不一致")
    if len(baseline_articles) != protected_count:
        raise BaselineError("冻结副本文章数与 manifest 不一致")

    current_raw, current_document = load_news_document(current_path)
    current_articles = current_document["articles"]
    issues: list[str] = []

    if len(current_articles) < protected_count:
        issues.append(
            f"文章数量少于冻结数量：当前 {len(current_articles)}，"
            f"冻结 {protected_count}"
        )
    if len(current_articles) > protected_count and not allow_appended_articles:
        issues.append(
            f"检测到 {len(current_articles) - protected_count} 篇新增文章；"
            "默认校验不允许追加，请显式使用 --allow-appended-articles"
        )

    prefix = current_articles[:protected_count]
    current_prefix_details = describe_articles(prefix)
    for index, expected_key in enumerate(
        manifest["orderedArticleKeys"]
    ):
        if index >= len(prefix):
            break
        current_key = current_prefix_details["orderedArticleKeys"][index]
        if current_key != expected_key:
            issues.append(
                f"第 {index} 篇文章顺序或身份变化："
                f"期望 {expected_key}，实际 {current_key}"
            )

    for key, expected_hash in manifest["articleHashes"].items():
        actual_hash = current_prefix_details["articleHashes"].get(key)
        if actual_hash is None:
            issues.append(f"冻结文章缺失：{key}")
        elif actual_hash != expected_hash:
            issues.append(f"冻结文章字段发生变化：{key}")

    if (
        len(current_articles) >= protected_count
        and current_prefix_details["articlesSha256"]
        != manifest["articlesSha256"]
        and not any("字段发生变化" in item or "顺序或身份变化" in item for item in issues)
    ):
        issues.append("冻结文章块哈希变化，但未定位到具体文章")

    appended_count = max(0, len(current_articles) - protected_count)
    status = "failed" if issues else (
        "unchanged_with_appended_articles"
        if appended_count
        else "unchanged_exact"
    )
    return {
        "status": status,
        "protectedArticleCount": protected_count,
        "currentArticleCount": len(current_articles),
        "appendedArticleCount": appended_count if not issues else 0,
        "baselineFileSha256": manifest["fileSha256"],
        "baselineArticlesSha256": manifest["articlesSha256"],
        "currentFileSha256": sha256_bytes(current_raw),
        "currentPrefixArticlesSha256": current_prefix_details[
            "articlesSha256"
        ],
        "issues": issues,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="创建冻结基线")
    create_parser.add_argument(
        "--source",
        type=Path,
        default=project_root / "news.json",
    )
    create_parser.add_argument(
        "--baseline-dir",
        type=Path,
        default=project_root / ".agent-baselines",
    )
    create_parser.add_argument(
        "--expected-count",
        type=int,
        default=DEFAULT_PROTECTED_ARTICLE_COUNT,
    )

    check_parser = subparsers.add_parser("check", help="校验冻结基线")
    check_parser.add_argument(
        "--current",
        type=Path,
        default=project_root / "news.json",
    )
    check_parser.add_argument(
        "--manifest",
        type=Path,
        default=project_root / ".agent-baselines" / "manifest.json",
    )
    check_parser.add_argument(
        "--allow-appended-articles",
        action="store_true",
        help="允许在冻结文章之后追加新文章",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """执行创建或校验命令。"""

    args = parse_args(argv)
    try:
        if args.command == "create":
            baseline_path, manifest_path, manifest = create_baseline(
                source_path=args.source,
                baseline_dir=args.baseline_dir,
                expected_article_count=args.expected_count,
            )
            output = {
                "status": "created",
                "baselinePath": baseline_path.as_posix(),
                "manifestPath": manifest_path.as_posix(),
                "fileSha256": manifest["fileSha256"],
                "articlesSha256": manifest["articlesSha256"],
                "protectedArticleCount": manifest["policy"][
                    "protectedArticleCount"
                ],
            }
        else:
            output = compare_with_baseline(
                current_path=args.current,
                manifest_path=args.manifest,
                allow_appended_articles=args.allow_appended_articles,
            )
    except BaselineError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
