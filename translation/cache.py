"""按内容、模型和策略版本复用已通过闸门的翻译结果。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


CACHE_VERSION = 1
VALID_CONTENT_POLICIES = frozenset({"full_l10n", "digest"})
VALID_LANGUAGES = frozenset({"zh-CN", "en", "ja", "ko"})


class CacheError(RuntimeError):
    """缓存操作失败的基类。"""


class CacheWriteRejected(CacheError):
    """候选结果未通过闸门，禁止进入长期缓存。"""


@dataclass(frozen=True)
class CacheIdentity:
    """决定缓存是否可复用的全部版本字段。"""

    article_id: str
    source_hash: str
    language: str
    model: str
    prompt_version: str
    glossary_version: str
    content_policy: Literal["full_l10n", "digest"]
    content_routing_version: str
    digest_chars: int | None = None
    full_text_segment_version: str | None = None

    def __post_init__(self) -> None:
        required_text = {
            "article_id": self.article_id,
            "source_hash": self.source_hash,
            "language": self.language,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "glossary_version": self.glossary_version,
            "content_routing_version": self.content_routing_version,
        }
        for name, value in required_text.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 必须是非空字符串")
        if self.language not in VALID_LANGUAGES:
            raise ValueError(f"不支持的缓存目标语言：{self.language}")
        if self.content_policy not in VALID_CONTENT_POLICIES:
            raise ValueError(f"不支持的内容策略：{self.content_policy}")

        if self.content_policy == "digest":
            if (
                isinstance(self.digest_chars, bool)
                or not isinstance(self.digest_chars, int)
                or self.digest_chars <= 0
            ):
                raise ValueError("digest 缓存必须提供正整数 digest_chars")
            if self.full_text_segment_version:
                raise ValueError("digest 缓存不能包含全文分段版本")
        else:
            if not str(self.full_text_segment_version or "").strip():
                raise ValueError(
                    "full_l10n 缓存必须提供 full_text_segment_version"
                )
            if self.digest_chars is not None:
                raise ValueError("full_l10n 缓存不能包含 digest_chars")

    def as_dict(self) -> dict[str, Any]:
        return {
            "articleId": self.article_id,
            "sourceHash": self.source_hash,
            "language": self.language,
            "model": self.model,
            "promptVersion": self.prompt_version,
            "glossaryVersion": self.glossary_version,
            "contentPolicy": self.content_policy,
            "contentRoutingVersion": self.content_routing_version,
            "digestChars": self.digest_chars,
            "fullTextSegmentVersion": self.full_text_segment_version,
        }

    @property
    def key(self) -> str:
        """返回规范 JSON 的 SHA256，不能由文件系统路径参与。"""

        return hashlib.sha256(
            json.dumps(
                self.as_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class CacheEligibility:
    """缓存写入前的质量资格声明。"""

    gate_passed: bool
    hard_failures: tuple[str, ...] = field(default_factory=tuple)
    unresolved_warnings: tuple[str, ...] = field(default_factory=tuple)
    content_truncated: bool = False

    @property
    def eligible(self) -> bool:
        return (
            self.gate_passed
            and not self.hard_failures
            and not self.unresolved_warnings
            and not self.content_truncated
        )

    def rejection_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.gate_passed:
            reasons.append("gate_not_passed")
        if self.hard_failures:
            reasons.append("hard_failures_present")
        if self.unresolved_warnings:
            reasons.append("unresolved_warnings_present")
        if self.content_truncated:
            reasons.append("content_truncated")
        return tuple(reasons)


@dataclass(frozen=True)
class CacheLookup:
    """一次缓存查询结果，不把损坏缓存当作命中。"""

    status: Literal["hit", "miss", "invalid"]
    key: str
    result: Any = None
    error: str | None = None


class TranslationCache:
    """按身份字段索引的文件型缓存。"""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._stats = {
            "hits": 0,
            "misses": 0,
            "invalid": 0,
            "writes": 0,
            "rejected_writes": 0,
        }

    def path_for(self, identity: CacheIdentity) -> Path:
        """返回只含 SHA256 文件名的缓存路径，避免路径穿越。"""

        return self.directory / f"{identity.key}.json"

    def get(self, identity: CacheIdentity) -> CacheLookup:
        """读取缓存；缺失为 miss，损坏或身份不一致为 invalid。"""

        path = self.path_for(identity)
        if not path.exists():
            self._stats["misses"] += 1
            return CacheLookup(status="miss", key=identity.key)

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._stats["invalid"] += 1
            self._stats["misses"] += 1
            return CacheLookup(
                status="invalid",
                key=identity.key,
                error=f"缓存文件无法解析：{type(exc).__name__}: {exc}",
            )

        if not isinstance(payload, dict):
            self._stats["invalid"] += 1
            self._stats["misses"] += 1
            return CacheLookup(
                status="invalid",
                key=identity.key,
                error="缓存顶层不是对象",
            )
        if payload.get("cacheVersion") != CACHE_VERSION:
            self._stats["invalid"] += 1
            self._stats["misses"] += 1
            return CacheLookup(
                status="invalid",
                key=identity.key,
                error="缓存版本不匹配",
            )
        if payload.get("key") != identity.key:
            self._stats["invalid"] += 1
            self._stats["misses"] += 1
            return CacheLookup(
                status="invalid",
                key=identity.key,
                error="缓存键与文件内容不一致",
            )
        if payload.get("identity") != identity.as_dict():
            self._stats["invalid"] += 1
            self._stats["misses"] += 1
            return CacheLookup(
                status="invalid",
                key=identity.key,
                error="缓存身份字段与请求不一致",
            )
        if "result" not in payload:
            self._stats["invalid"] += 1
            self._stats["misses"] += 1
            return CacheLookup(
                status="invalid",
                key=identity.key,
                error="缓存缺少 result",
            )
        expected_result_hash = _result_sha256(payload["result"])
        if payload.get("resultSha256") != expected_result_hash:
            self._stats["invalid"] += 1
            self._stats["misses"] += 1
            return CacheLookup(
                status="invalid",
                key=identity.key,
                error="缓存 result 完整性校验失败",
            )

        self._stats["hits"] += 1
        return CacheLookup(
            status="hit",
            key=identity.key,
            result=payload["result"],
        )

    def put(
        self,
        identity: CacheIdentity,
        result: Any,
        *,
        eligibility: CacheEligibility,
        usage: dict[str, Any] | None = None,
    ) -> Path:
        """原子写入合格结果；未过闸门或存在未解决警告时拒绝。"""

        if not eligibility.eligible:
            self._stats["rejected_writes"] += 1
            reasons = ", ".join(eligibility.rejection_reasons())
            raise CacheWriteRejected(f"结果不符合缓存条件：{reasons}")

        payload = {
            "cacheVersion": CACHE_VERSION,
            "key": identity.key,
            "identity": identity.as_dict(),
            "result": result,
            "resultSha256": _result_sha256(result),
            "usage": usage or {},
            "eligibility": {
                "gatePassed": True,
                "hardFailures": [],
                "unresolvedWarnings": [],
                "contentTruncated": False,
            },
        }
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise CacheError(f"缓存结果不能序列化为 JSON：{exc}") from exc

        path = self.path_for(identity)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.directory,
                prefix=f".{identity.key[:12]}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(serialized)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except Exception as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise CacheError(f"缓存原子写入失败：{exc}") from exc

        self._stats["writes"] += 1
        return path

    def stats(self) -> dict[str, int]:
        """返回当前进程内命中、未命中和无效缓存统计。"""

        return dict(self._stats)


def _result_sha256(result: Any) -> str:
    """计算缓存结果规范 JSON 的 SHA256，用于发现静默篡改。"""

    serialized = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
