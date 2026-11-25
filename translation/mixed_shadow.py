"""混合内容模式影子翻译；只生成候选，不修改生产新闻与 RSS。"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .budget import BudgetedClient
from .cache import (
    CacheIdentity,
    TranslationCache,
)
from .digest import (
    DIGEST_PROMPT_VERSION,
    DigestConfig,
    DigestResult,
    generate_digest,
)
from .full_l10n import (
    FULL_L10N_PROMPT_VERSION,
    FullL10nConfig,
    FullL10nResult,
    generate_full_l10n,
)
from .gates import GateReport, validate_translation
from .glossary import Glossary, load_glossary
from .llm import ChatUsage, OpenAICompatibleClient
from .routing import (
    CONTENT_ROUTING_VERSION,
    ContentRoute,
    route_content,
)
from .shadow import (
    _mock_generation,
    _zh_tw_converter,
    article_id,
    select_articles,
    source_hash,
)


SUPPORTED_LANGUAGES = ("zh-CN", "zh-TW", "en", "ja", "ko")
GENERATED_LANGUAGES = ("zh-CN", "en", "ja", "ko")
DEFAULT_PRICE_INPUT_CNY_PER_M = 2.0
DEFAULT_PRICE_OUTPUT_CNY_PER_M = 8.0
FULL_SEGMENT_VERSION = "full-segments-4000-6000-v1"
DEFAULT_DIGEST_CHARS = 650


def _sha256_file(path: Path) -> str:
    """计算文件 SHA256，用于证明影子任务没有修改输入。"""

    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class MixedShadowConfig:
    """混合影子任务配置。"""

    input_file: Path
    output_dir: Path
    limit: int = 20
    engine: str = "llm"
    language: str | None = None
    concurrency: int = 4
    cache_dir: Path | None = None
    max_cost_cny: float = 20.0
    hard_deadline_minutes: float = 60.0
    price_input_cny_per_m: float = DEFAULT_PRICE_INPUT_CNY_PER_M
    price_output_cny_per_m: float = DEFAULT_PRICE_OUTPUT_CNY_PER_M
    full_config: FullL10nConfig = field(default_factory=FullL10nConfig)
    digest_config: DigestConfig = field(default_factory=DigestConfig)

    def __post_init__(self) -> None:
        if self.engine not in {"llm", "mock"}:
            raise ValueError(f"不支持的影子引擎：{self.engine}")
        if self.limit <= 0:
            raise ValueError("影子抽样数量必须大于 0")
        if self.concurrency <= 0:
            raise ValueError("并发数必须大于 0")
        if self.max_cost_cny <= 0:
            raise ValueError("单次费用上限必须大于 0")
        if self.hard_deadline_minutes <= 0:
            raise ValueError("硬截止分钟数必须大于 0")
        if self.language is not None and self.language not in GENERATED_LANGUAGES:
            raise ValueError(f"不支持的影子目标语言：{self.language}")


@dataclass
class Candidate:
    """不同内容模式统一后的单篇单语候选。"""

    title: str
    summary: str
    content: str
    usage: ChatUsage
    requests: int
    content_policy: str
    target_language: str
    warnings: tuple[str, ...] = ()
    segments: tuple[dict[str, Any], ...] = ()
    cache_hit: bool = False
    gate: GateReport | None = None

    def as_cache_payload(self) -> dict[str, Any]:
        """生成可安全序列化的缓存结果。"""

        return {
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "usage": {
                "inputTokens": self.usage.input_tokens,
                "outputTokens": self.usage.output_tokens,
                "estimated": self.usage.estimated,
            },
            "requests": self.requests,
            "contentPolicy": self.content_policy,
            "targetLanguage": self.target_language,
            "warnings": list(self.warnings),
            "segments": list(self.segments),
        }


def _cache_identity(
    *,
    article: dict[str, Any],
    route: ContentRoute,
    language: str,
    glossary: Glossary,
    config: MixedShadowConfig,
    client: Any | None,
) -> CacheIdentity | None:
    """缓存关闭时返回 None；真实模型模式才建立严格身份。"""

    if config.cache_dir is None or config.engine != "llm":
        return None
    model = str(getattr(client, "model", "") or "").strip()
    if not model:
        raise RuntimeError("真实影子缓存缺少模型名称")
    identifier = article_id(article)
    if route.content_policy == "full_l10n":
        return CacheIdentity(
            article_id=identifier,
            source_hash=source_hash(article),
            language=language,
            model=model,
            prompt_version=FULL_L10N_PROMPT_VERSION,
            glossary_version=glossary.version,
            content_policy="full_l10n",
            content_routing_version=CONTENT_ROUTING_VERSION,
            full_text_segment_version=FULL_SEGMENT_VERSION,
        )
    return CacheIdentity(
        article_id=identifier,
        source_hash=source_hash(article),
        language=language,
        model=model,
        prompt_version=DIGEST_PROMPT_VERSION,
        glossary_version=glossary.version,
        content_policy="digest",
        content_routing_version=CONTENT_ROUTING_VERSION,
        digest_chars=DEFAULT_DIGEST_CHARS,
    )


def _candidate_from_full(result: FullL10nResult) -> Candidate:
    return Candidate(
        title=result.title,
        summary=result.summary,
        content=result.content,
        usage=result.usage,
        requests=result.requests,
        content_policy=result.content_policy,
        target_language=result.target_language,
        warnings=result.warnings,
        segments=tuple(segment.as_dict() | {"content": segment.content} for segment in result.segments),
    )


def _candidate_from_digest(result: DigestResult) -> Candidate:
    return Candidate(
        title=result.title,
        summary=result.summary,
        content=result.content,
        usage=result.usage,
        requests=result.requests,
        content_policy=result.content_policy,
        target_language=result.target_language,
        warnings=result.warnings,
    )


def _candidate_from_cache(
    payload: dict[str, Any],
    *,
    language: str,
    route: ContentRoute,
) -> Candidate:
    """从缓存恢复候选，并拒绝身份不完整的损坏结构。"""

    if not isinstance(payload, dict):
        raise ValueError("缓存 result 不是对象")
    usage_payload = payload.get("usage") or {}
    if not isinstance(usage_payload, dict):
        raise ValueError("缓存 usage 不是对象")
    policy = str(payload.get("contentPolicy") or "")
    if policy != route.content_policy:
        raise ValueError(
            f"缓存内容模式与路由不一致：{policy} != {route.content_policy}"
        )
    candidate = Candidate(
        title=str(payload.get("title") or ""),
        summary=str(payload.get("summary") or ""),
        content=str(payload.get("content") or ""),
        # 历史 usage 不属于本次运行成本；本次命中缓存没有产生模型请求。
        usage=ChatUsage(),
        # 本次运行没有产生模型请求；缓存中的历史请求数不计入当前账本。
        requests=0,
        content_policy=policy,
        target_language=str(payload.get("targetLanguage") or language),
        warnings=tuple(str(item) for item in payload.get("warnings") or ()),
        segments=tuple(payload.get("segments") or ()),
        cache_hit=True,
    )
    if not all((candidate.title.strip(), candidate.summary.strip(), candidate.content.strip())):
        raise ValueError("缓存候选存在空字段")
    return candidate


def _generate_candidate(
    *,
    client: Any | None,
    article: dict[str, Any],
    route: ContentRoute,
    language: str,
    glossary: Glossary,
    config: MixedShadowConfig,
) -> Candidate:
    """按路由调用全文或摘要生成器。"""

    if config.engine == "mock":
        mock = _mock_generation(article, language, glossary)
        return Candidate(
            title=mock.title,
            summary=mock.summary,
            content=mock.content,
            usage=mock.usage,
            requests=mock.requests,
            content_policy=route.content_policy,
            target_language=language,
            warnings=route.warnings,
        )

    if client is None:
        raise RuntimeError("真实混合影子任务缺少 LLM 客户端")
    if route.content_policy == "full_l10n":
        result = generate_full_l10n(
            client=client,
            article=article,
            language=language,
            glossary=glossary,
            config=config.full_config,
        )
        candidate = _candidate_from_full(result)
    else:
        result = generate_digest(
            client=client,
            article=article,
            language=language,
            glossary=glossary,
            config=config.digest_config,
        )
        candidate = _candidate_from_digest(result)

    return candidate


def _gate_candidate(
    *,
    article: dict[str, Any],
    route: ContentRoute,
    language: str,
    candidate: Candidate,
) -> GateReport:
    """对真实模型候选执行统一质量闸门；缓存命中结果也重新验证。"""

    return validate_translation(
        source_text="\n".join(
            str(article.get(field) or "")
            for field in (
                "originalTitle",
                "originalSummary",
                "originalContent",
            )
        ),
        source_body_text=str(article.get("originalContent") or ""),
        candidate_title=candidate.title,
        candidate_summary=candidate.summary,
        candidate_content=candidate.content,
        target_language=language,
        content_policy=route.content_policy,
        source_language=route.source_language,
        segments=candidate.segments or None,
    )


def _set_cache(
    *,
    cache: TranslationCache | None,
    identity: CacheIdentity | None,
    candidate: Candidate,
) -> None:
    """仅把通过闸门且没有未解决警告的结果写入缓存。"""

    if cache is None or identity is None or candidate.gate is None:
        return
    eligibility = candidate.gate.cache_eligibility()
    if not eligibility.eligible:
        return
    cache.put(
        identity,
        candidate.as_cache_payload(),
        eligibility=eligibility,
        usage=candidate.as_cache_payload()["usage"],
    )


def _language_output(candidate: Candidate) -> dict[str, Any]:
    return {
        "title": candidate.title.strip(),
        "summary": candidate.summary.strip(),
        "content": candidate.content.strip(),
    }


def run_mixed_shadow(
    config: MixedShadowConfig,
    *,
    client: OpenAICompatibleClient | None = None,
    glossary: Glossary | None = None,
    zh_tw_converter: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """运行混合路由影子任务，所有产物只写入 output_dir。"""

    started = time.monotonic()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    glossary = glossary or load_glossary()
    if zh_tw_converter is not None:
        convert_zh_tw = zh_tw_converter
    else:
        try:
            convert_zh_tw = _zh_tw_converter()
        except RuntimeError:
            # 本地 mock 冒烟不应依赖 OpenCC；真实模型模式仍必须安装并使用
            # OpenCC，不能把未经转换的简体中文伪装成繁体中文。
            if config.engine != "mock":
                raise
            convert_zh_tw = lambda value: value
    input_before = _sha256_file(config.input_file)
    source = json.loads(config.input_file.read_text(encoding="utf-8"))
    all_articles = source.get("articles") or []
    selected = select_articles(all_articles, config.limit)
    languages = (
        (config.language,)
        if config.language in GENERATED_LANGUAGES
        else GENERATED_LANGUAGES
    )
    if config.engine == "llm" and client is None:
        raise RuntimeError("真实混合影子任务缺少 LLM 客户端")

    budgeted_client: BudgetedClient | None = None
    effective_client: Any | None = client
    if config.engine == "llm":
        budgeted_client = BudgetedClient(
            client,
            max_cost_cny=config.max_cost_cny,
            price_input_cny_per_m=config.price_input_cny_per_m,
            price_output_cny_per_m=config.price_output_cny_per_m,
            hard_deadline_minutes=config.hard_deadline_minutes,
            max_output_tokens=int(getattr(client, "max_tokens", 16000)),
        )
        effective_client = budgeted_client

    cache = (
        TranslationCache(config.cache_dir)
        if config.cache_dir is not None and config.engine == "llm"
        else None
    )
    routes = {
        article_id(article): route_content(article)
        for article in selected
    }
    jobs = [
        (article, language)
        for article in selected
        for language in languages
    ]
    results: dict[tuple[str, str], Candidate] = {}
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    policy_mismatch_count = 0

    def execute(article: dict[str, Any], language: str) -> Candidate:
        identifier = article_id(article)
        route = routes[identifier]
        identity = _cache_identity(
            article=article,
            route=route,
            language=language,
            glossary=glossary,
            config=config,
            client=effective_client,
        )
        if cache is not None and identity is not None:
            lookup = cache.get(identity)
            if lookup.status == "hit":
                candidate = _candidate_from_cache(
                    lookup.result,
                    language=language,
                    route=route,
                )
                candidate.gate = _gate_candidate(
                    article=article,
                    route=route,
                    language=language,
                    candidate=candidate,
                )
                if candidate.gate.passed:
                    return candidate
                raise ValueError("缓存结果未通过当前质量闸门")

        candidate = _generate_candidate(
            client=effective_client,
            article=article,
            route=route,
            language=language,
            glossary=glossary,
            config=config,
        )
        if config.engine == "llm":
            candidate.gate = _gate_candidate(
                article=article,
                route=route,
                language=language,
                candidate=candidate,
            )
            _set_cache(
                cache=cache,
                identity=identity,
                candidate=candidate,
            )
        return candidate

    with ThreadPoolExecutor(max_workers=max(1, config.concurrency)) as executor:
        future_map = {
            executor.submit(execute, article, language): (article, language)
            for article, language in jobs
        }
        for future in as_completed(future_map):
            article, language = future_map[future]
            identifier = article_id(article)
            route = routes[identifier]
            try:
                candidate = future.result()
                if candidate.content_policy != route.content_policy:
                    policy_mismatch_count += 1
                    raise RuntimeError(
                        "实际内容模式与计划不一致："
                        f"{candidate.content_policy} != {route.content_policy}"
                    )
                if candidate.gate is not None:
                    for issue in candidate.gate.warnings:
                        warnings.append(
                            {
                                "articleId": identifier,
                                "title": article.get("originalTitle", ""),
                                "language": language,
                                "warning": issue.code,
                            }
                        )
                if candidate.gate is not None and not candidate.gate.passed:
                    failures.append(
                        {
                            "articleId": identifier,
                            "title": article.get("originalTitle", ""),
                            "language": language,
                            "contentPolicy": route.content_policy,
                            "failures": [
                                issue.code for issue in candidate.gate.hard_failures
                            ],
                            "gate": candidate.gate.as_dict(),
                        }
                    )
                    continue
                results[(identifier, language)] = candidate
            except Exception as error:
                failures.append(
                    {
                        "articleId": identifier,
                        "title": article.get("originalTitle", ""),
                        "language": language,
                        "contentPolicy": route.content_policy,
                        "failures": [f"{type(error).__name__}：{error}"],
                    }
                )

    output_articles: list[dict[str, Any]] = []
    for article in selected:
        identifier = article_id(article)
        generated: dict[str, Candidate] = {}
        for language in GENERATED_LANGUAGES:
            candidate = results.get((identifier, language))
            if candidate is not None:
                generated[language] = candidate

        if config.language not in (None, "zh-TW"):
            if config.language not in generated:
                continue
            if config.language == "zh-CN":
                generated["zh-TW"] = _make_zh_tw(
                    generated["zh-CN"],
                    convert_zh_tw,
                )
        else:
            if any(language not in generated for language in GENERATED_LANGUAGES):
                continue
            generated["zh-TW"] = _make_zh_tw(generated["zh-CN"], convert_zh_tw)

        ordered_languages = [
            language for language in SUPPORTED_LANGUAGES if language in generated
        ]
        route = routes[identifier]
        output_articles.append(
            {
                "id": identifier,
                "url": article.get("url"),
                "source": article.get("source"),
                "sourceKey": article.get("sourceKey"),
                "sourceTypeKey": article.get("sourceTypeKey"),
                "categoryKey": article.get("categoryKey"),
                "sourceLanguage": route.source_language,
                "publishedAt": article.get("publishedAt"),
                "timestamp": int(article.get("timestamp") or 0),
                "heatScore": int(article.get("heatScore") or 0),
                "sourceHash": source_hash(article),
                "contentPolicy": route.content_policy,
                "contentPolicyReason": route.content_policy_reason,
                "plannedRouting": route.as_dict(),
                "actualRouting": route.as_dict(),
                "originalTitle": article.get("originalTitle"),
                "originalSummary": article.get("originalSummary"),
                "translations": {
                    language: _language_output(generated[language])
                    for language in ordered_languages
                },
                "translationInfo": {
                    language: {
                        "source": (
                            "opencc"
                            if language == "zh-TW"
                            else "mock"
                            if config.engine == "mock"
                            else "llm"
                        ),
                        "model": (
                            "mock-shadow"
                            if config.engine == "mock"
                            else getattr(effective_client, "model", "")
                        ),
                        "glossaryVersion": glossary.version,
                        "contentPolicy": generated[language].content_policy,
                        "contentRoutingVersion": CONTENT_ROUTING_VERSION,
                        "cacheHit": generated[language].cache_hit,
                        "gatePassed": (
                            None
                            if generated[language].gate is None
                            else generated[language].gate.passed
                        ),
                        "requests": generated[language].requests,
                    }
                    for language in ordered_languages
                },
                "partialShadow": bool(config.language),
            }
        )

    planned_policy_counts = {"full_l10n": 0, "digest": 0}
    actual_policy_counts = {"full_l10n": 0, "digest": 0}
    planned_route_warnings: dict[str, int] = {}
    for article in selected:
        route = routes[article_id(article)]
        planned_policy_counts[route.content_policy] += 1
        for warning in route.warnings:
            planned_route_warnings[warning] = (
                planned_route_warnings.get(warning, 0) + 1
            )
    for article in output_articles:
        actual_policy_counts[article["contentPolicy"]] += 1

    total_usage = ChatUsage()
    request_count = 0
    cache_hits = 0
    for candidate in results.values():
        total_usage += candidate.usage
        request_count += candidate.requests
        cache_hits += int(candidate.cache_hit)
    estimated_cost = (
        total_usage.input_tokens * config.price_input_cny_per_m
        + total_usage.output_tokens * config.price_output_cny_per_m
    ) / 1_000_000
    duration_seconds = round(time.monotonic() - started, 3)
    input_after = _sha256_file(config.input_file)
    cache_stats = (
        cache.stats()
        if cache is not None
        else {
            "hits": cache_hits,
            "misses": 0,
            "invalid": 0,
            "writes": 0,
            "rejected_writes": 0,
        }
    )
    budget_snapshot = (
        budgeted_client.snapshot().as_dict()
        if budgeted_client is not None
        else None
    )

    shadow_news = {
        "schemaVersion": 3,
        "shadow": True,
        "supportedLanguages": list(SUPPORTED_LANGUAGES),
        "defaultLanguage": "zh-CN",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timezone": source.get("timezone", "Asia/Hong_Kong"),
        "periodHours": int(source.get("periodHours") or 24),
        "sourceCount": len({article.get("sourceKey") for article in selected}),
        "translation": {
            "engine": config.engine,
            "model": (
                "mock-shadow"
                if config.engine == "mock"
                else getattr(effective_client, "model", "")
            ),
            "promptVersion": "mixed-shadow-v1",
            "glossaryVersion": glossary.version,
            "contentRoutingVersion": CONTENT_ROUTING_VERSION,
            "plannedPolicyCounts": planned_policy_counts,
            "actualPolicyCounts": actual_policy_counts,
            "plannedRouteWarnings": planned_route_warnings,
            "articlesSelected": len(selected),
            "articlesGenerated": len(output_articles),
            "requests": request_count,
            "tokens": {
                "input": total_usage.input_tokens,
                "output": total_usage.output_tokens,
                "estimated": total_usage.estimated,
            },
            "estimatedCostCny": round(estimated_cost, 6),
            "durationSeconds": duration_seconds,
            "cache": cache_stats,
            "budget": budget_snapshot,
            "hardDeadlineMinutes": config.hard_deadline_minutes,
        },
        "articles": output_articles,
    }
    report = {
        "status": "passed" if not failures else "failed",
        "shadow": True,
        "engine": config.engine,
        "model": shadow_news["translation"]["model"],
        "generatedAt": shadow_news["generatedAt"],
        "selectedArticles": len(selected),
        "generatedArticles": len(output_articles),
        "requestedLanguages": list(languages),
        "requests": request_count,
        "tokens": shadow_news["translation"]["tokens"],
        "estimatedCostCny": round(estimated_cost, 6),
        "durationSeconds": duration_seconds,
        "contentRoutingVersion": CONTENT_ROUTING_VERSION,
        "plannedPolicyCounts": planned_policy_counts,
        "actualPolicyCounts": actual_policy_counts,
        "policyMismatchCount": policy_mismatch_count,
        "plannedRouteWarnings": planned_route_warnings,
        "cache": cache_stats,
        "budget": budget_snapshot,
        "hardDeadlineMinutes": config.hard_deadline_minutes,
        "inputFileSha256Before": input_before,
        "inputFileSha256After": input_after,
        "inputFileUnchanged": input_before == input_after,
        "failures": failures,
        "warnings": warnings,
        "productionFilesModified": False,
    }
    (config.output_dir / "news.shadow.json").write_text(
        json.dumps(shadow_news, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (config.output_dir / "translation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("## 游闻混合影子验证\n\n")
            handle.write(f"- 结果：{report['status']}\n")
            handle.write(f"- 引擎：{config.engine} / {report['model']}\n")
            handle.write(f"- 计划模式：{planned_policy_counts}\n")
            handle.write(f"- 实际模式：{actual_policy_counts}\n")
            handle.write(f"- 抽样 / 生成：{len(selected)} / {len(output_articles)}\n")
            handle.write(f"- 硬截止：{config.hard_deadline_minutes} 分钟\n")
            if budget_snapshot is not None:
                handle.write(
                    f"- 预算：已结算 ¥{budget_snapshot['spentCny']:.6f}"
                    f" / ¥{budget_snapshot['maxCostCny']:.2f}\n"
                )
                handle.write(
                    f"- 未结算预留：¥{budget_snapshot['unsettledCny']:.6f}\n"
                )
            handle.write(f"- 生产文件：未修改\n")
    return report


def _make_zh_tw(
    zh_cn: Candidate,
    converter: Callable[[str], str],
) -> Candidate:
    """从已通过闸门的简体中文生成繁体中文候选。"""

    return Candidate(
        title=converter(zh_cn.title),
        summary=converter(zh_cn.summary),
        content=converter(zh_cn.content),
        usage=ChatUsage(),
        requests=0,
        content_policy=zh_cn.content_policy,
        target_language="zh-TW",
        warnings=zh_cn.warnings,
        segments=zh_cn.segments,
        cache_hit=zh_cn.cache_hit,
    )
