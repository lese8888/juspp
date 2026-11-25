"""生产翻译引擎适配层。

LLM 生产模式复用已经通过影子验证的混合内容流水线，但只接收调用方传入的
新文章。冻结文章的保留和最终写入门禁由生产入口负责，本模块不会修改任何
生产文件。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .llm import OpenAICompatibleClient
from .mixed_shadow import MixedShadowConfig, run_mixed_shadow


class LLMProductionError(RuntimeError):
    """LLM 生产生成失败；调用方必须让整批任务失败。"""


@dataclass(frozen=True)
class LLMEngineConfig:
    """LLM 生产引擎的运行边界。"""

    cache_dir: Path | None = None
    concurrency: int = 3
    max_cost_cny: float = 20.0
    hard_deadline_minutes: float = 60.0
    price_input_cny_per_m: float = 2.0
    price_output_cny_per_m: float = 8.0

    def __post_init__(self) -> None:
        if self.concurrency <= 0:
            raise ValueError("LLM 并发数必须大于 0")
        if self.max_cost_cny <= 0:
            raise ValueError("LLM 单次费用上限必须大于 0")
        if self.hard_deadline_minutes <= 0:
            raise ValueError("LLM 硬截止必须大于 0")


@dataclass(frozen=True)
class ProductionEngineResult:
    """LLM 新文章生成结果与可审计元数据。"""

    articles: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]


def create_client_from_environment() -> OpenAICompatibleClient:
    """根据 Actions Secret/Variable 创建 OpenAI 兼容客户端。"""

    return OpenAICompatibleClient(
        api_key=os.environ.get("LLM_API_KEY", ""),
        base_url=os.environ.get(
            "LLM_BASE_URL",
            "https://api.deepseek.com/v1",
        ),
        model=os.environ.get("LLM_MODEL", "deepseek-chat"),
        timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.environ.get("LLM_MAX_RETRIES", "3")),
        json_mode=os.environ.get("LLM_JSON_MODE", "1") == "1",
        max_tokens=int(os.environ.get("LLM_ARTICLE_OUTPUT_TOKENS", "16000")),
        temperature=float(os.environ.get("LLM_TEMPERATURE", "0.2")),
    )


def config_from_environment() -> LLMEngineConfig:
    """读取生产运行的费用、并发和硬截止配置。"""

    cache_value = os.environ.get("LLM_CACHE_DIR", "").strip()
    return LLMEngineConfig(
        cache_dir=Path(cache_value) if cache_value else None,
        concurrency=int(os.environ.get("LLM_CONCURRENCY", "3")),
        max_cost_cny=float(
            os.environ.get("LLM_MAX_COST_CNY_PER_RUN", "20")
        ),
        hard_deadline_minutes=float(
            os.environ.get("LLM_HARD_DEADLINE_MINUTES", "60")
        ),
        price_input_cny_per_m=float(
            os.environ.get("LLM_PRICE_INPUT_CNY_PER_M", "2.0")
        ),
        price_output_cny_per_m=float(
            os.environ.get("LLM_PRICE_OUTPUT_CNY_PER_M", "8.0")
        ),
    )


class LLMProductionEngine:
    """把新文章交给混合 LLM 流水线，禁止静默回退。"""

    def __init__(
        self,
        *,
        client: OpenAICompatibleClient,
        config: LLMEngineConfig | None = None,
        source_document: Mapping[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.config = config or LLMEngineConfig()
        self.source_document = dict(source_document or {})

    def translate(
        self,
        articles: Sequence[Mapping[str, Any]],
    ) -> ProductionEngineResult:
        """只为传入的新文章生成五语内容。"""

        normalized = [dict(article) for article in articles]
        if not normalized:
            return ProductionEngineResult(
                articles=(),
                metadata={
                    "engine": "llm",
                    "model": self.client.model,
                    "articlesGenerated": 0,
                    "requests": 0,
                    "tokens": {"input": 0, "output": 0, "estimated": False},
                    "estimatedCostCny": 0.0,
                    "cache": {"hits": 0, "misses": 0},
                },
            )

        with tempfile.TemporaryDirectory(prefix="youwen-llm-production-") as temp:
            root = Path(temp)
            input_path = root / "input.json"
            output_dir = root / "output"
            source = {
                **self.source_document,
                "generatedAt": self.source_document.get("generatedAt"),
                "timezone": self.source_document.get(
                    "timezone",
                    "Asia/Hong_Kong",
                ),
                "periodHours": int(
                    self.source_document.get("periodHours") or 24
                ),
                "articles": normalized,
            }
            input_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            shadow_config = MixedShadowConfig(
                input_file=input_path,
                output_dir=output_dir,
                limit=len(normalized),
                engine="llm",
                concurrency=self.config.concurrency,
                cache_dir=self.config.cache_dir,
                max_cost_cny=self.config.max_cost_cny,
                hard_deadline_minutes=self.config.hard_deadline_minutes,
                price_input_cny_per_m=self.config.price_input_cny_per_m,
                price_output_cny_per_m=self.config.price_output_cny_per_m,
            )
            report = run_mixed_shadow(shadow_config, client=self.client)
            if report.get("status") != "passed":
                failures = json.dumps(
                    report.get("failures") or [],
                    ensure_ascii=False,
                )
                raise LLMProductionError(
                    f"LLM 生产生成未通过质量闸门：{failures}"
                )

            generated = json.loads(
                (output_dir / "news.shadow.json").read_text(encoding="utf-8")
            )
            original_by_url = {
                _normalized_url(article.get("url")): article
                for article in normalized
            }
            output_articles: list[dict[str, Any]] = []
            for article in generated.get("articles") or []:
                item = dict(article)
                original = original_by_url.get(
                    _normalized_url(item.get("url")),
                    {},
                )
                item["originalContent"] = original.get("originalContent", "")
                item.pop("partialShadow", None)
                item.pop("plannedRouting", None)
                item.pop("actualRouting", None)
                output_articles.append(item)

            if len(output_articles) != len(normalized):
                raise LLMProductionError(
                    "LLM 生产结果数量与新增文章数量不一致："
                    f"expected={len(normalized)}, actual={len(output_articles)}"
                )
            output_articles.sort(
                key=lambda item: (
                    -int(item.get("heatScore") or 0),
                    -int(item.get("timestamp") or 0),
                    str(item.get("url") or ""),
                )
            )
            metadata = {
                "engine": "llm",
                "model": report.get("model"),
                "articlesGenerated": report.get("generatedArticles"),
                "requests": report.get("requests"),
                "tokens": report.get("tokens"),
                "estimatedCostCny": report.get("estimatedCostCny"),
                "durationSeconds": report.get("durationSeconds"),
                "contentRoutingVersion": report.get("contentRoutingVersion"),
                "plannedPolicyCounts": report.get("plannedPolicyCounts"),
                "actualPolicyCounts": report.get("actualPolicyCounts"),
                "cache": report.get("cache"),
                "budget": report.get("budget"),
                "warnings": report.get("warnings") or [],
            }
            return ProductionEngineResult(
                articles=tuple(output_articles),
                metadata=metadata,
            )


def _normalized_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/").lower()
