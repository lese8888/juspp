import json
import tempfile
import unittest
from pathlib import Path

from translation.protected_articles import (
    FrozenArticleViolation,
    ManifestError,
    article_sha256,
    validate_candidate_articles,
)


def make_article(article_id: str, title: str) -> dict:
    return {
        "id": article_id,
        "url": f"https://example.com/{article_id}",
        "title": title,
        "originalContent": f"正文 {article_id}",
    }


class ProtectedArticlesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.manifest_path = Path(self.temp_dir.name) / "manifest.json"
        self.frozen = [
            make_article("frozen-1", "冻结文章一"),
            make_article("frozen-2", "冻结文章二"),
        ]
        self._write_manifest(self.frozen)

    def _write_manifest(self, articles):
        keys = [
            (
                f"id:{article['id']}"
                if article.get("id")
                else f"url:{article['url']}"
            )
            for article in articles
        ]
        payload = {
            "policy": {
                "protectedArticleCount": len(articles),
            },
            "orderedArticleKeys": keys,
            "articleHashes": {
                key: article_sha256(article)
                for key, article in zip(keys, articles)
            },
        }
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_original_articles_pass_in_release_mode(self):
        result = validate_candidate_articles(
            self.frozen,
            self.manifest_path,
            mode="release",
        )
        self.assertEqual(result.protected_count, 2)
        self.assertEqual(result.appended_count, 0)

    def test_field_change_is_rejected(self):
        changed = [dict(article) for article in self.frozen]
        changed[0]["title"] = "被改写的标题"
        with self.assertRaisesRegex(
            FrozenArticleViolation, "冻结文章内容发生变化"
        ):
            validate_candidate_articles(
                changed,
                self.manifest_path,
                mode="production",
            )

    def test_reordered_articles_are_rejected(self):
        with self.assertRaisesRegex(
            FrozenArticleViolation, "顺序或身份发生变化"
        ):
            validate_candidate_articles(
                list(reversed(self.frozen)),
                self.manifest_path,
                mode="production",
            )

    def test_deletion_is_rejected(self):
        with self.assertRaisesRegex(
            FrozenArticleViolation, "缺少冻结文章"
        ):
            validate_candidate_articles(
                self.frozen[:1],
                self.manifest_path,
                mode="production",
            )

    def test_new_article_can_only_be_appended(self):
        new_article = make_article("new-1", "新增文章")
        result = validate_candidate_articles(
            [*self.frozen, new_article],
            self.manifest_path,
            mode="production",
        )
        self.assertEqual(result.appended_count, 1)
        self.assertEqual(result.appended_ids, ("new-1",))

    def test_inserting_new_article_before_frozen_article_is_rejected(self):
        new_article = make_article("new-1", "新增文章")
        with self.assertRaisesRegex(
            FrozenArticleViolation, "顺序或身份发生变化"
        ):
            validate_candidate_articles(
                [new_article, *self.frozen],
                self.manifest_path,
                mode="release",
            )

    def test_read_only_modes_cannot_write_frozen_articles(self):
        for mode in ("development", "shadow"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(
                    FrozenArticleViolation, "只读阶段不能"
                ):
                    validate_candidate_articles(
                        self.frozen,
                        self.manifest_path,
                        mode=mode,
                    )

    def test_missing_manifest_is_rejected(self):
        missing = Path(self.temp_dir.name) / "missing.json"
        with self.assertRaises(ManifestError):
            validate_candidate_articles(
                self.frozen,
                missing,
                mode="production",
            )

    def test_corrupt_manifest_is_rejected(self):
        self.manifest_path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ManifestError, "有效 UTF-8 JSON"):
            validate_candidate_articles(
                self.frozen,
                self.manifest_path,
                mode="production",
            )

    def test_manifest_count_mismatch_is_rejected(self):
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        payload["policy"]["protectedArticleCount"] = 3
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ManifestError, "数量不一致"):
            validate_candidate_articles(
                self.frozen,
                self.manifest_path,
                mode="production",
            )

    def test_url_only_articles_match_production_manifest_shape(self):
        url_only = [
            {
                "url": "https://example.com/frozen-a",
                "title": "只有 URL 的冻结文章",
                "originalContent": "正文 A",
            }
        ]
        self._write_manifest(url_only)
        result = validate_candidate_articles(
            url_only,
            self.manifest_path,
            mode="production",
        )
        self.assertEqual(result.protected_count, 1)
        self.assertEqual(result.appended_ids, ())

    def test_url_only_new_article_can_be_appended(self):
        frozen = [
            {
                "url": "https://example.com/frozen-a",
                "title": "只有 URL 的冻结文章",
                "originalContent": "正文 A",
            }
        ]
        new_article = {
            "url": "https://example.com/new-a",
            "title": "只有 URL 的新文章",
            "originalContent": "正文 B",
        }
        self._write_manifest(frozen)
        result = validate_candidate_articles(
            [*frozen, new_article],
            self.manifest_path,
            mode="release",
        )
        self.assertEqual(result.appended_count, 1)
        self.assertEqual(
            result.appended_ids,
            ("https://example.com/new-a",),
        )

    def test_real_frozen_baseline_passes_in_release_mode(self):
        project_root = Path(__file__).resolve().parents[1]
        manifest_path = project_root / ".agent-baselines" / "manifest.json"
        if not manifest_path.exists():
            self.skipTest("真实冻结基线尚未生成")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        baseline_path = Path(manifest["baselinePath"])
        if not baseline_path.is_absolute():
            baseline_path = manifest_path.parent / baseline_path
        if not baseline_path.exists():
            self.skipTest("真实冻结副本尚未生成")

        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        articles = baseline["articles"]
        result = validate_candidate_articles(
            articles,
            manifest_path,
            mode="release",
        )
        self.assertEqual(result.protected_count, 75)
        self.assertEqual(result.appended_count, 0)


if __name__ == "__main__":
    unittest.main()
