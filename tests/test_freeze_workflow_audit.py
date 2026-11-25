import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "audit-freeze-workflow.py"
)
SPEC = importlib.util.spec_from_file_location("audit_freeze_workflow", SCRIPT_PATH)
audit_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(audit_module)


class WorkflowFreezeAuditTests(unittest.TestCase):
    def test_real_workflow_returns_a_valid_audit_result(self):
        project_root = Path(__file__).resolve().parents[1]
        result = audit_module.audit(project_root)
        self.assertIn(result["status"], {"passed", "unsafe"})
        self.assertGreater(result["workflowCount"], 0)
        rules = {
            hit["rule"]
            for workflow in result["workflows"]
            for hit in workflow["hits"]
        }
        self.assertTrue(
            {
                "schedule",
                "collect",
                "news_add",
                "git_commit",
                "git_push",
                "pages_trigger",
            }.issubset(rules)
        )

    def test_unsafe_fixture_is_detected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "collect.yml").write_text(
                "\n".join(
                    [
                        "on:",
                        "  schedule:",
                        "    - cron: '0 0 * * *'",
                        "jobs:",
                        "  collect:",
                        "    steps:",
                        "      - run: npm run collect",
                        "      - run: git add news.json rss.xml",
                        "      - run: git commit -m update",
                        "      - run: git push origin main",
                    ]
                ),
                encoding="utf-8",
            )
            result = audit_module.audit(root)
            self.assertEqual(result["status"], "unsafe")
            self.assertEqual(
                result["scheduledProductionBypassFiles"],
                [".github/workflows/collect.yml"],
            )

    def test_audit_is_read_only(self):
        project_root = Path(__file__).resolve().parents[1]
        before = {
            path: path.read_bytes()
            for path in (project_root / ".github" / "workflows").glob("*.yml")
        }
        audit_module.audit(project_root)
        after = {
            path: path.read_bytes()
            for path in (project_root / ".github" / "workflows").glob("*.yml")
        }
        self.assertEqual(before, after)

    def test_safe_fixture_has_no_scheduled_bypass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "shadow.yml").write_text(
                "name: 影子\non:\n  workflow_dispatch:\n",
                encoding="utf-8",
            )
            result = audit_module.audit(root)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["scheduledProductionBypassFiles"], [])


if __name__ == "__main__":
    unittest.main()
