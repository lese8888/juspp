#!/usr/bin/env python3
"""只读统计当前新闻数据的多语言内容路由。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from translation.routing import route_content


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查新闻内容路由分布")
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "news.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    articles = source.get("articles") or source.get("items") or []
    routes = [route_content(article) for article in articles]

    languages = Counter(route.source_language for route in routes)
    policies = Counter(route.content_policy for route in routes)
    reasons = Counter(route.content_policy_reason for route in routes)
    warnings = Counter(
        warning for route in routes for warning in route.warnings
    )
    english_counts = [
        route.english_word_count
        for route in routes
        if route.source_language == "en"
    ]
    chinese_counts = [
        route.chinese_character_count
        for route in routes
        if route.source_language == "zh"
    ]

    report = {
        "input": str(args.input.resolve()),
        "articles": len(routes),
        "languages": dict(sorted(languages.items())),
        "policies": {
            "full_l10n": policies.get("full_l10n", 0),
            "digest": policies.get("digest", 0),
        },
        "reasons": dict(sorted(reasons.items())),
        "warnings": dict(sorted(warnings.items())),
        "englishWords": {
            "articles": len(english_counts),
            "total": sum(english_counts),
            "average": (
                round(sum(english_counts) / len(english_counts), 1)
                if english_counts
                else 0
            ),
            "p95": percentile(english_counts, 0.95),
            "max": max(english_counts, default=0),
        },
        "chineseCharacters": {
            "articles": len(chinese_counts),
            "total": sum(chinese_counts),
            "average": (
                round(sum(chinese_counts) / len(chinese_counts), 1)
                if chinese_counts
                else 0
            ),
            "p95": percentile(chinese_counts, 0.95),
            "max": max(chinese_counts, default=0),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * ratio)]


if __name__ == "__main__":
    raise SystemExit(main())
