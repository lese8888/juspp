import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / ".github" / "workflows"
FREEZE_COMMAND = "baseline:check -- --allow-appended-articles"


def workflow_lines(name: str) -> list[str]:
    """读取工作流文本，便于按步骤顺序验证门禁位置。"""

    return (WORKFLOW_DIR / name).read_text(encoding="utf-8").splitlines()


def first_line_containing(lines: list[str], needle: str) -> int:
    """返回首个命中行号；缺失时让测试明确失败。"""

    for index, line in enumerate(lines):
        if needle in line:
            return index
    raise AssertionError(f"工作流缺少必要命令：{needle}")


class ReleaseWorkflowGateTests(unittest.TestCase):
    def test_collect_checks_freeze_before_commit(self):
        lines = workflow_lines("game-intel.yml")
        branch_index = first_line_containing(lines, 'GITHUB_REF" != "refs/heads/main')
        freeze_index = first_line_containing(lines, FREEZE_COMMAND)
        commit_index = first_line_containing(lines, "git add news.json rss.xml")
        self.assertLess(branch_index, freeze_index)
        self.assertLess(freeze_index, commit_index)

    def test_pages_checks_freeze_before_copy_and_deploy(self):
        lines = workflow_lines("pages.yml")
        branch_index = first_line_containing(lines, 'GITHUB_REF" != "refs/heads/main')
        freeze_index = first_line_containing(
            lines,
            "scripts/check-news-baseline.py check --allow-appended-articles",
        )
        copy_index = first_line_containing(
            lines,
            "cp index.html news.json rss.xml _site/",
        )
        deploy_index = first_line_containing(lines, "deploy-pages@")
        self.assertLess(branch_index, freeze_index)
        self.assertLess(freeze_index, copy_index)
        self.assertLess(freeze_index, deploy_index)

    def test_collection_remains_manual_and_defaults_to_argos(self):
        text = "\n".join(workflow_lines("game-intel.yml"))
        self.assertNotIn("\n  schedule:", text)
        self.assertIn("default: argos", text)
        self.assertIn(
            "TRANSLATION_ENGINE: ${{ inputs.translation_engine || 'argos' }}",
            text,
        )

    def test_manual_collection_never_sends_email(self):
        text = "\n".join(workflow_lines("game-intel.yml"))
        self.assertIn(
            "SEND_EMAIL: ${{ github.event_name == 'schedule' && 'true' || 'false' }}",
            text,
        )
        self.assertNotIn("SEND_EMAIL: 'true'", text)


if __name__ == "__main__":
    unittest.main()
