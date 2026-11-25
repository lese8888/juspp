#!/usr/bin/env python3
"""运行游闻 LLM 影子任务；绝不修改生产新闻与 RSS。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from translation.llm import LLMConfigurationError, OpenAICompatibleClient
from translation.mixed_shadow import MixedShadowConfig, run_mixed_shadow
from translation.shadow import ShadowConfig, run_shadow


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="游闻 LLM 影子验证")
    parser.add_argument("--input", type=Path, default=ROOT / "news.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".shadow-output")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--engine", choices=("llm", "mock"), default="llm")
    parser.add_argument(
        "--content-mode",
        choices=("mixed", "digest"),
        default=os.environ.get("SHADOW_CONTENT_MODE", "mixed"),
        help="mixed 按原文长度自动选择全文/摘要；digest 保留旧摘要影子链路",
    )
    parser.add_argument("--language", choices=("zh-CN", "en", "ja", "ko"))
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument(
        "--max-cost-cny",
        type=float,
        default=float(os.environ.get("LLM_MAX_COST_CNY_PER_RUN", "20")),
        help="单次影子运行最坏成本硬上限",
    )
    parser.add_argument(
        "--hard-deadline-minutes",
        type=float,
        default=float(os.environ.get("LLM_HARD_DEADLINE_MINUTES", "60")),
        help="程序级硬截止时间",
    )
    parser.add_argument(
        "--max-body-chars",
        type=int,
        default=int(os.environ.get("LLM_MAX_BODY_CHARS", "24000")),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("LLM_CONCURRENCY", "4")),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    common = {
        "input_file": args.input.resolve(),
        "output_dir": output_dir,
        "limit": max(1, args.limit),
        "engine": args.engine,
        "language": args.language,
        "concurrency": min(6, max(1, args.concurrency)),
        "price_input_cny_per_m": float(
            os.environ.get("LLM_PRICE_INPUT_CNY_PER_M", "2.0")
        ),
        "price_output_cny_per_m": float(
            os.environ.get("LLM_PRICE_OUTPUT_CNY_PER_M", "8.0")
        ),
    }
    if args.content_mode == "mixed":
        config = MixedShadowConfig(
            **common,
            cache_dir=args.cache_dir.resolve() if args.cache_dir else None,
            max_cost_cny=args.max_cost_cny,
            hard_deadline_minutes=args.hard_deadline_minutes,
        )
    else:
        config = ShadowConfig(
            **common,
            max_body_chars=max(6000, args.max_body_chars),
        )
    client = None
    if args.engine == "llm":
        try:
            client = OpenAICompatibleClient(
                api_key=os.environ.get("LLM_API_KEY", ""),
                base_url=os.environ.get(
                    "LLM_BASE_URL",
                    "https://api.deepseek.com/v1",
                ),
                model=os.environ.get("LLM_MODEL", "deepseek-chat"),
                timeout_seconds=float(
                    os.environ.get("LLM_TIMEOUT_SECONDS", "120")
                ),
                max_retries=int(os.environ.get("LLM_MAX_RETRIES", "3")),
                json_mode=os.environ.get("LLM_JSON_MODE", "1") == "1",
                max_tokens=int(
                    os.environ.get("LLM_ARTICLE_OUTPUT_TOKENS", "16000")
                ),
                temperature=float(os.environ.get("LLM_TEMPERATURE", "0.2")),
            )
        except LLMConfigurationError as error:
            report = {
                "status": "blocked",
                "shadow": True,
                "error": str(error),
                "productionFilesModified": False,
            }
            (output_dir / "translation-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(str(error))
            return 2

    if args.content_mode == "mixed":
        report = run_mixed_shadow(config, client=client)
    else:
        report = run_shadow(config, client=client)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
