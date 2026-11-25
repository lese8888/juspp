import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "check-news-baseline.py"
)
SPEC = importlib.util.spec_from_file_location("check_news_baseline", SCRIPT_PATH)
baseline = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(baseline)


def fixture_document() -> dict:
    return {
        "generatedAt": "2026-11-24T00:00:00.000Z",
        "schemaVersion": 2,
        "translationEngine": "fixture",
        "articles": [
            {
                "url": "https://example.com/1",
                "originalTitle": "第一篇",
                "originalContent": "正文一",
                "publishedAt": "2026-11-24T00:00:00Z",
            },
            {
                "url": "https://example.com/2",
                "originalTitle": "第二篇",
                "originalContent": "正文二",
                "publishedAt": "2026-11-24T01:00:00Z",
            },
            {
                "url": "https://example.com/3",
                "originalTitle": "第三篇",
                "originalContent": "正文三",
                "publishedAt": "2026-11-24T02:00:00Z",
            },
        ],
    }


class NewsBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source_path = self.root / "news.json"
        self.baseline_dir = self.root / ".agent-baselines"
        self.write_document(fixture_document())
        _, self.manifest_path, self.manifest = baseline.create_baseline(
            source_path=self.source_path,
            baseline_dir=self.baseline_dir,
            expected_article_count=3,
            created_at="2026-11-24T03:00:00+00:00",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_document(self, document: dict) -> None:
        self.source_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def check(self, document: dict, *, allow_appended: bool = False) -> dict:
        self.write_document(document)
        return baseline.compare_with_baseline(
            current_path=self.source_path,
            manifest_path=self.manifest_path,
            allow_appended_articles=allow_appended,
        )

    def test_manifest_records_75_article_policy_contract(self):
        self.assertEqual(
            self.manifest["policy"]["protectedArticleCount"],
            3,
        )
        self.assertTrue(self.manifest["policy"]["allowAppendedArticles"])
        self.assertEqual(len(self.manifest["orderedArticleKeys"]), 3)
        self.assertEqual(len(self.manifest["articleHashes"]), 3)

    def test_unchanged_document_passes(self):
        report = self.check(fixture_document())
        self.assertEqual(report["status"], "unchanged_exact")
        self.assertEqual(report["issues"], [])

    def test_field_change_fails_and_identifies_article(self):
        changed = fixture_document()
        changed["articles"][1]["originalTitle"] = "被修改"
        report = self.check(changed)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(
                "字段发生变化" in issue
                and "https://example.com/2" in issue
                for issue in report["issues"]
            )
        )

    def test_order_change_fails(self):
        changed = fixture_document()
        changed["articles"][0], changed["articles"][1] = (
            changed["articles"][1],
            changed["articles"][0],
        )
        report = self.check(changed)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any("顺序或身份变化" in issue for issue in report["issues"])
        )

    def test_deleted_article_fails(self):
        changed = fixture_document()
        changed["articles"].pop()
        report = self.check(changed)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any("文章数量少于冻结数量" in issue for issue in report["issues"])
        )

    def test_new_article_appended_preserves_original_articles(self):
        changed = fixture_document()
        changed["articles"].append(
            {
                "url": "https://example.com/new",
                "originalTitle": "新增文章",
                "originalContent": "新增正文",
            }
        )
        strict_report = self.check(changed)
        self.assertEqual(strict_report["status"], "failed")
        self.assertTrue(
            any("默认校验不允许追加" in issue for issue in strict_report["issues"])
        )

        allowed_report = self.check(changed, allow_appended=True)
        self.assertEqual(
            allowed_report["status"],
            "unchanged_with_appended_articles",
        )
        self.assertEqual(allowed_report["appendedArticleCount"], 1)
        self.assertEqual(allowed_report["issues"], [])

    def test_inserting_new_article_before_original_fails(self):
        changed = copy.deepcopy(fixture_document())
        changed["articles"].insert(
            0,
            {
                "url": "https://example.com/new",
                "originalTitle": "插队文章",
                "originalContent": "新增正文",
            },
        )
        report = self.check(changed, allow_appended=True)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any("顺序或身份变化" in issue for issue in report["issues"])
        )

    def test_tampered_baseline_copy_fails(self):
        baseline_path = Path(self.manifest["baselinePath"])
        if not baseline_path.is_absolute():
            baseline_path = self.manifest_path.parent / baseline_path
        baseline_path.write_text(
            baseline_path.read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            baseline.BaselineError,
            "冻结副本文件哈希与 manifest 不一致",
        ):
            self.check(fixture_document())


class RepositoryBaselineTests(unittest.TestCase):
    def test_repository_has_protected_75_article_baseline(self):
        project_root = Path(__file__).resolve().parents[1]
        manifest_path = project_root / ".agent-baselines" / "manifest.json"
        if not manifest_path.exists():
            self.fail("缺少 .agent-baselines/manifest.json")
        report = baseline.compare_with_baseline(
            current_path=project_root / "news.json",
            manifest_path=manifest_path,
            allow_appended_articles=False,
        )
        self.assertEqual(report["status"], "unchanged_exact", report)
        self.assertEqual(report["protectedArticleCount"], 75)
        self.assertEqual(report["currentArticleCount"], 75)


if __name__ == "__main__":
    unittest.main()
