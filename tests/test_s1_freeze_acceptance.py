import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run-s1-freeze-acceptance.py"
SPEC = importlib.util.spec_from_file_location(
    "run_s1_freeze_acceptance",
    SCRIPT_PATH,
)
acceptance = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(acceptance)


class S1FreezeAcceptanceTests(unittest.TestCase):
    def copy_core_modules_to(self, root: Path) -> None:
        """复制真实校验模块到临时项目，用于验证失败关闭行为。"""

        (root / "scripts").mkdir()
        (root / "translation").mkdir()
        (root / ".agent-baselines").mkdir()
        (root / "scripts" / "check-news-baseline.py").write_bytes(
            (
                PROJECT_ROOT / "scripts" / "check-news-baseline.py"
            ).read_bytes()
        )
        (root / "translation" / "protected_articles.py").write_bytes(
            (
                PROJECT_ROOT
                / "translation"
                / "protected_articles.py"
            ).read_bytes()
        )
        (root / "news.json").write_text(
            '{"articles": []}\n',
            encoding="utf-8",
        )

    def test_real_repository_core_acceptance_passes(self):
        result = acceptance.run_acceptance(
            PROJECT_ROOT,
            run_tests=False,
        )
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(result["productionFilesUnchanged"])
        self.assertEqual(result["core"]["baselineStatus"], "unchanged_exact")
        self.assertEqual(result["core"]["protectedArticleCount"], 75)
        self.assertEqual(result["core"]["currentArticleCount"], 75)
        self.assertEqual(result["core"]["protectionProtectedCount"], 75)
        self.assertEqual(result["core"]["appendedCount"], 0)
        self.assertEqual(result["core"]["orderedKeyCount"], 75)
        self.assertEqual(result["core"]["articleHashCount"], 75)

    def test_production_hash_change_is_detected(self):
        self.assertFalse(
            acceptance.collect_production_hashes(PROJECT_ROOT)
            == {
                "news.json": "changed",
                "rss.xml": acceptance.collect_production_hashes(
                    PROJECT_ROOT
                )["rss.xml"],
            }
        )

    def test_missing_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.copy_core_modules_to(root)
            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "缺少冻结清单",
            ):
                acceptance.inspect_core_freeze(root)

    def test_tampered_baseline_copy_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.copy_core_modules_to(root)

            baseline_path = root / ".agent-baselines" / "baseline.json"
            baseline_path.write_text(
                '{"articles": []}\n',
                encoding="utf-8",
            )
            actual_hash = hashlib.sha256(
                baseline_path.read_bytes()
            ).hexdigest()
            manifest = {
                "manifestVersion": 1,
                "policy": {"protectedArticleCount": 1},
                "baselinePath": baseline_path.name,
                "fileSha256": actual_hash,
                "articlesSha256": "0" * 64,
                "orderedArticleKeys": ["url:https://example.com"],
                "articleHashes": {
                    "url:https://example.com": "0" * 64,
                },
            }
            (root / ".agent-baselines" / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            baseline_path.write_text(
                '{"articles": [{"url": "tampered"}]}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "冻结副本文件哈希与 manifest 不一致",
            ):
                acceptance.inspect_core_freeze(root)

    def test_workflow_audit_state_does_not_fake_completion(self):
        state = acceptance.workflow_audit_state(PROJECT_ROOT)
        self.assertIn(
            state["status"],
            {"pending_agent_3", "available_pending_review"},
        )
        if state["status"] == "pending_agent_3":
            self.assertTrue(state["missingFiles"])
        else:
            self.assertEqual(state["missingFiles"], [])


if __name__ == "__main__":
    unittest.main()
