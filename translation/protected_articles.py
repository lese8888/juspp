"""冻结新闻的生产写入保护器。

本模块只做校验，不修复、不排序、不覆盖任何文章。冻结清单缺失或损坏时
默认拒绝写入，避免配置故障把只读基线意外变成可写数据。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence


WriteMode = Literal["development", "shadow", "production", "release"]

VALID_WRITE_MODES = frozenset(
    {"development", "shadow", "production", "release"}
)
READ_ONLY_WRITE_MODES = frozenset({"development", "shadow"})


class ProtectionError(ValueError):
    """冻结新闻保护失败的基类。"""


class ManifestError(ProtectionError):
    """冻结清单缺失、损坏或自相矛盾。"""


class FrozenArticleViolation(ProtectionError):
    """候选数据试图修改、删除、重排或重译冻结文章。"""


@dataclass(frozen=True)
class FrozenEntry:
    """冻结清单中的一篇文章。"""

    article_key: str
    article_sha256: str
    position: int


@dataclass(frozen=True)
class FrozenManifest:
    """经过严格解析的冻结清单。"""

    entries: tuple[FrozenEntry, ...]
    manifest_sha256: str

    @property
    def article_count(self) -> int:
        return len(self.entries)


@dataclass(frozen=True)
class ProtectionResult:
    """候选集合通过保护检查后的只读统计。"""

    mode: WriteMode
    protected_count: int
    candidate_count: int
    appended_count: int
    appended_ids: tuple[str, ...]
    manifest_sha256: str


def canonical_json(value: Any) -> str:
    """生成与冻结清单一致的规范化 JSON。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json_bytes(value: Any) -> bytes:
    """生成规范化 JSON 的 UTF-8 字节。"""

    return canonical_json(value).encode("utf-8")


def article_sha256(article: Mapping[str, Any]) -> str:
    """计算单篇文章完整对象的规范化 SHA256。"""

    return hashlib.sha256(canonical_json_bytes(dict(article))).hexdigest()


def load_frozen_manifest(path: str | Path) -> FrozenManifest:
    """读取并验证冻结清单；任何不确定情况都拒绝放行。"""

    manifest_path = Path(path)
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise ManifestError(
            f"冻结清单不可读，已拒绝写入：{manifest_path}"
        ) from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(
            f"冻结清单不是有效 UTF-8 JSON，已拒绝写入：{manifest_path}"
        ) from exc

    if not isinstance(payload, dict):
        raise ManifestError("冻结清单顶层必须是对象")

    entries = _parse_entries(payload)
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    return FrozenManifest(entries=entries, manifest_sha256=manifest_sha256)


def validate_candidate_articles(
    candidates: Sequence[Mapping[str, Any]],
    manifest_path: str | Path,
    *,
    mode: WriteMode,
) -> ProtectionResult:
    """校验候选文章是否可以写入指定阶段。

    开发与影子阶段只能写入清单外的新文章。生产与发布阶段必须完整保留
    冻结文章的原内容、原顺序，且只能在末尾追加新文章。
    """

    if mode not in VALID_WRITE_MODES:
        raise ProtectionError(f"未知写入阶段：{mode!r}")
    if isinstance(candidates, (str, bytes)) or not isinstance(
        candidates, Sequence
    ):
        raise ProtectionError("候选文章集合必须是序列")

    normalized_candidates = _normalize_candidates(candidates)
    manifest = load_frozen_manifest(manifest_path)
    protected_keys = {entry.article_key for entry in manifest.entries}

    if mode in READ_ONLY_WRITE_MODES:
        for index, article in enumerate(normalized_candidates):
            article_key = _article_key(article)
            if article_key in protected_keys:
                raise FrozenArticleViolation(
                    "只读阶段不能把冻结文章作为写入目标："
                    f"index={index}, key={article_key!r}"
                )
        appended_ids = tuple(
            _display_identity(article) for article in normalized_candidates
        )
        return ProtectionResult(
            mode=mode,
            protected_count=manifest.article_count,
            candidate_count=len(normalized_candidates),
            appended_count=len(normalized_candidates),
            appended_ids=appended_ids,
            manifest_sha256=manifest.manifest_sha256,
        )

    if len(normalized_candidates) < manifest.article_count:
        raise FrozenArticleViolation(
            "生产候选缺少冻结文章："
            f"expected={manifest.article_count}, "
            f"actual={len(normalized_candidates)}"
        )

    frozen_candidates = normalized_candidates[: manifest.article_count]
    for entry, article in zip(manifest.entries, frozen_candidates):
        article_key = _article_key(article)
        if article_key != entry.article_key:
            raise FrozenArticleViolation(
                "冻结文章顺序或身份发生变化："
                f"position={entry.position}, "
                f"expectedKey={entry.article_key!r}, "
                f"actualKey={article_key!r}"
            )
        actual_hash = article_sha256(article)
        if actual_hash != entry.article_sha256:
            raise FrozenArticleViolation(
                "冻结文章内容发生变化："
                f"position={entry.position}, key={article_key!r}, "
                f"expectedHash={entry.article_sha256}, actualHash={actual_hash}"
            )

    appended = normalized_candidates[manifest.article_count :]
    seen_appended_ids: set[str] = set()
    for offset, article in enumerate(appended):
        article_key = _article_key(article)
        index = manifest.article_count + offset
        if article_key in protected_keys:
            raise FrozenArticleViolation(
                "新文章不得复用或重排冻结文章："
                f"index={index}, key={article_key!r}"
            )
        if article_key in seen_appended_ids:
            raise FrozenArticleViolation(
                "追加文章存在重复稳定键："
                f"index={index}, key={article_key!r}"
            )
        seen_appended_ids.add(article_key)

    return ProtectionResult(
        mode=mode,
        protected_count=manifest.article_count,
        candidate_count=len(normalized_candidates),
        appended_count=len(appended),
        appended_ids=tuple(_display_identity(article) for article in appended),
        manifest_sha256=manifest.manifest_sha256,
    )


def _normalize_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    for index, article in enumerate(candidates):
        if not isinstance(article, Mapping):
            raise ProtectionError(f"候选文章必须是对象：index={index}")
        item = dict(article)
        _article_key(item)
        normalized.append(item)
    return tuple(normalized)


def _article_key(article: Mapping[str, Any]) -> str:
    """返回与冻结清单一致的稳定键，优先 id，其次 url。"""

    for field in ("id", "url"):
        value = str(article.get(field) or "").strip()
        if value:
            return f"{field}:{value}"
    raise ProtectionError("候选文章同时缺少非空 id 和 url")


def _display_identity(article: Mapping[str, Any]) -> str:
    article_id = str(article.get("id") or "").strip()
    if article_id:
        return article_id
    return str(article.get("url") or "").strip()


def _parse_entries(payload: Mapping[str, Any]) -> tuple[FrozenEntry, ...]:
    count = payload.get("protectedArticleCount")
    if count is None and isinstance(payload.get("policy"), dict):
        count = payload["policy"].get("protectedArticleCount")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ManifestError("protectedArticleCount 必须是正整数")

    if isinstance(payload.get("orderedArticleKeys"), list):
        entries = _parse_ordered_keys(payload)
    elif payload.get("articles") is not None:
        entries_value = payload.get("articles")
        entries = _parse_entry_objects(entries_value)
    else:
        entries = _parse_parallel_arrays(payload)

    if len(entries) != count:
        raise ManifestError(
            "冻结清单数量不一致："
            f"protectedArticleCount={count}, entries={len(entries)}"
        )

    keys = [entry.article_key for entry in entries]
    if len(set(keys)) != len(keys):
        raise ManifestError("冻结清单包含重复文章稳定键")
    return tuple(entries)


def _parse_entry_objects(value: Any) -> list[FrozenEntry]:
    if not isinstance(value, list):
        raise ManifestError("manifest.articles 必须是数组")

    entries: list[FrozenEntry] = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            raise ManifestError(f"manifest.articles[{position}] 必须是对象")
        article_key = _key_from_values(
            item.get("id"),
            item.get("url"),
            position,
        )
        digest = _required_hash(
            item.get("articleSha256")
            or item.get("articleHash")
            or item.get("hash"),
            position,
        )
        entries.append(
            FrozenEntry(
                article_key=article_key,
                article_sha256=digest,
                position=position,
            )
        )
    return entries


def _parse_ordered_keys(payload: Mapping[str, Any]) -> list[FrozenEntry]:
    keys = payload.get("orderedArticleKeys")
    hashes = payload.get("articleHashes")
    if not isinstance(keys, list):
        raise ManifestError("orderedArticleKeys 必须是数组")
    if not isinstance(hashes, dict):
        raise ManifestError(
            "使用 orderedArticleKeys 时 articleHashes 必须是按稳定键索引的对象"
        )

    entries: list[FrozenEntry] = []
    for position, value in enumerate(keys):
        key = str(value or "").strip()
        if not key.startswith(("id:", "url:")) or not key[3:]:
            raise ManifestError(
                f"orderedArticleKeys[{position}] 格式无效：{key!r}"
            )
        entries.append(
            FrozenEntry(
                article_key=key,
                article_sha256=_required_hash(hashes.get(key), position),
                position=position,
            )
        )
    return entries


def _parse_parallel_arrays(payload: Mapping[str, Any]) -> list[FrozenEntry]:
    ids = payload.get("orderedIds")
    urls = payload.get("orderedUrls")
    hashes = payload.get("articleHashes")
    if not isinstance(ids, list) or not isinstance(urls, list):
        raise ManifestError(
            "冻结清单缺少 articles，或 orderedIds/orderedUrls 不是数组"
        )
    if len(ids) != len(urls):
        raise ManifestError("orderedIds 与 orderedUrls 长度不一致")

    if isinstance(hashes, list):
        if len(hashes) != len(ids):
            raise ManifestError("articleHashes 与 orderedIds 长度不一致")
        ordered_hashes: Iterable[Any] = hashes
    elif isinstance(hashes, dict):
        ordered_hashes = (hashes.get(str(article_id)) for article_id in ids)
    else:
        raise ManifestError("articleHashes 必须是数组或按文章 ID 索引的对象")

    entries: list[FrozenEntry] = []
    for position, (article_id, url, digest) in enumerate(
        zip(ids, urls, ordered_hashes)
    ):
        entries.append(
            FrozenEntry(
                article_key=_key_from_values(article_id, url, position),
                article_sha256=_required_hash(digest, position),
                position=position,
            )
        )
    return entries


def _required_text(
    item: Mapping[str, Any], key: str, position: int
) -> str:
    return _required_value_text(item.get(key), key, position)


def _required_value_text(value: Any, key: str, position: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ManifestError(
            f"manifest article[{position}].{key} 必须是非空字符串"
        )
    return text


def _key_from_values(article_id: Any, url: Any, position: int) -> str:
    normalized_id = str(article_id or "").strip()
    if normalized_id:
        return f"id:{normalized_id}"
    normalized_url = str(url or "").strip()
    if normalized_url:
        return f"url:{normalized_url}"
    raise ManifestError(
        f"manifest article[{position}] 同时缺少非空 id 和 url"
    )


def _required_hash(value: Any, position: int) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ManifestError(
            f"manifest article[{position}] 的 SHA256 格式无效"
        )
    return digest
