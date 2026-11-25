import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from translation.cache import (
    CacheEligibility,
    CacheError,
    CacheIdentity,
    CacheWriteRejected,
    TranslationCache,
)


def identity(**changes):
    value = CacheIdentity(
        article_id="article-1",
        source_hash="source-hash-1",
        language="zh-CN",
        model="deepseek-flash",
        prompt_version="full-v1",
        glossary_version="g1",
        content_policy="full_l10n",
        content_routing_version="mixed-language-5000-2000-v1",
        full_text_segment_version="segment-v1",
    )
    return replace(value, **changes)


def eligible():
    return CacheEligibility(gate_passed=True)


class CacheIdentityTests(unittest.TestCase):
    def test_key_is_deterministic_and_path_safe(self):
        first = identity(article_id="../../escape")
        second = identity(article_id="../../escape")
        self.assertEqual(first.key, second.key)
        self.assertEqual(len(first.key), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in first.key))

    def test_digest_requires_digest_chars(self):
        with self.assertRaisesRegex(ValueError, "digest_chars"):
            CacheIdentity(
                article_id="a",
                source_hash="s",
                language="zh-CN",
                model="m",
                prompt_version="p",
                glossary_version="g",
                content_policy="digest",
                content_routing_version="r",
            )

    def test_full_requires_segment_version(self):
        with self.assertRaisesRegex(ValueError, "full_text_segment_version"):
            CacheIdentity(
                article_id="a",
                source_hash="s",
                language="zh-CN",
                model="m",
                prompt_version="p",
                glossary_version="g",
                content_policy="full_l10n",
                content_routing_version="r",
            )


class TranslationCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.directory = Path(self.temp_dir.name) / "cache"
        self.cache = TranslationCache(self.directory)

    def test_same_identity_hits_and_returns_result(self):
        current = identity()
        self.cache.put(
            current,
            {"title": "标题", "content": "完整正文"},
            eligibility=eligible(),
            usage={"inputTokens": 10, "outputTokens": 20},
        )
        result = self.cache.get(current)
        self.assertEqual(result.status, "hit")
        self.assertEqual(result.result["title"], "标题")
        self.assertEqual(self.cache.stats()["hits"], 1)

    def test_all_version_fields_invalidate_cache(self):
        current = identity()
        self.cache.put(current, {"title": "标题"}, eligibility=eligible())
        variants = [
            identity(article_id="article-2"),
            identity(source_hash="source-hash-2"),
            identity(language="en"),
            identity(model="other-model"),
            identity(prompt_version="full-v2"),
            identity(glossary_version="g2"),
            identity(content_routing_version="route-v2"),
            identity(full_text_segment_version="segment-v2"),
        ]
        for changed in variants:
            with self.subTest(changed=changed):
                self.assertEqual(self.cache.get(changed).status, "miss")

    def test_digest_and_full_do_not_share_cache(self):
        digest_identity = identity(
            content_policy="digest",
            digest_chars=500,
            full_text_segment_version=None,
        )
        self.cache.put(
            digest_identity,
            {"content": "摘要"},
            eligibility=eligible(),
        )
        self.assertEqual(self.cache.get(identity()).status, "miss")
        self.assertEqual(self.cache.get(digest_identity).status, "hit")

    def test_unpassed_gate_is_rejected(self):
        with self.assertRaisesRegex(CacheWriteRejected, "gate_not_passed"):
            self.cache.put(
                identity(),
                {"title": "标题"},
                eligibility=CacheEligibility(gate_passed=False),
            )
        self.assertEqual(self.cache.stats()["rejected_writes"], 1)
        self.assertFalse(self.cache.path_for(identity()).exists())

    def test_hard_failures_warnings_and_truncation_are_rejected(self):
        cases = [
            CacheEligibility(gate_passed=True, hard_failures=("number_changed",)),
            CacheEligibility(
                gate_passed=True,
                unresolved_warnings=("content_over_limit",),
            ),
            CacheEligibility(gate_passed=True, content_truncated=True),
        ]
        for eligibility_value in cases:
            with self.subTest(eligibility=eligibility_value):
                with self.assertRaises(CacheWriteRejected):
                    self.cache.put(
                        identity(),
                        {"title": "标题"},
                        eligibility=eligibility_value,
                    )

    def test_broken_file_is_invalid_and_not_repaired(self):
        path = self.cache.path_for(identity())
        path.write_text("{broken", encoding="utf-8")
        original = path.read_bytes()
        result = self.cache.get(identity())
        self.assertEqual(result.status, "invalid")
        self.assertIn("无法解析", result.error)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.cache.stats()["invalid"], 1)

    def test_identity_tampering_is_invalid(self):
        current = identity()
        path = self.cache.path_for(current)
        path.write_text(
            json.dumps(
                {
                    "cacheVersion": 1,
                    "key": current.key,
                    "identity": current.as_dict(),
                    "result": {"title": "旧结果"},
                    "usage": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        changed = identity(source_hash="source-hash-2")
        # 改成当前请求的路径后，文件内容中的身份仍与请求不一致。
        changed_path = self.cache.path_for(changed)
        changed_path.write_bytes(path.read_bytes())
        result = self.cache.get(changed)
        self.assertEqual(result.status, "invalid")
        self.assertIn("缓存键", result.error)

    def test_result_tampering_is_invalid(self):
        current = identity()
        self.cache.put(current, {"title": "原结果"}, eligibility=eligible())
        path = self.cache.path_for(current)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["result"]["title"] = "被篡改"
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        result = self.cache.get(current)
        self.assertEqual(result.status, "invalid")
        self.assertIn("完整性校验失败", result.error)

    def test_atomic_write_failure_cleans_temporary_file(self):
        with mock.patch(
            "translation.cache.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaisesRegex(CacheError, "原子写入失败"):
                self.cache.put(
                    identity(),
                    {"title": "标题"},
                    eligibility=eligible(),
                )
        temporary_files = list(self.directory.glob("*.tmp"))
        self.assertEqual(temporary_files, [])
        self.assertFalse(self.cache.path_for(identity()).exists())

    def test_stats_distinguish_hit_miss_invalid_and_writes(self):
        current = identity()
        self.cache.put(current, {"title": "标题"}, eligibility=eligible())
        self.assertEqual(self.cache.get(current).status, "hit")
        self.assertEqual(self.cache.get(identity(article_id="new")).status, "miss")
        corrupt_path = self.cache.path_for(identity(article_id="broken"))
        corrupt_path.write_text("{broken", encoding="utf-8")
        self.assertEqual(
            self.cache.get(identity(article_id="broken")).status,
            "invalid",
        )
        self.assertEqual(
            self.cache.stats(),
            {
                "hits": 1,
                "misses": 2,
                "invalid": 1,
                "writes": 1,
                "rejected_writes": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
