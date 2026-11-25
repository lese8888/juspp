#!/usr/bin/env python3
"""只读审计工作流中可能绕过新闻冻结的生产写入链路。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


RULES = (
    ("schedule", re.compile(r"^\s*schedule\s*:", re.MULTILINE), "HIGH"),
    ("cron", re.compile(r"^\s*-\s*cron\s*:", re.MULTILINE), "HIGH"),
    ("manual_dispatch", re.compile(r"^\s*workflow_dispatch\s*:", re.MULTILINE), "MEDIUM"),
    ("collect", re.compile(r"\bnpm run collect\b"), "HIGH"),
    ("news_add", re.compile(r"\bgit add\b.*\bnews\.json\b"), "HIGH"),
    ("git_commit", re.compile(r"\bgit commit\b"), "HIGH"),
    ("git_push", re.compile(r"\bgit push\b"), "HIGH"),
    ("pages_trigger", re.compile(r"\bgh workflow run pages\.yml\b"), "HIGH"),
    ("pages_copy", re.compile(r"\bcp\b.*\bnews\.json\b.*\brss\.xml\b"), "HIGH"),
    ("pages_deploy", re.compile(r"\bdeploy-pages@"), "HIGH"),
    ("email", re.compile(r"\bSEND_EMAIL\b|\bsendmail\b|\bnodemailer\b"), "MEDIUM"),
)


def scan_workflow(path: Path, project_root: Path) -> dict:
    """扫描单个工作流，返回带行号的风险命中。"""

    text = path.read_text(encoding="utf-8")
    hits = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern, severity in RULES:
            if pattern.search(line):
                hits.append(
                    {
                        "file": path.relative_to(project_root).as_posix(),
                        "line": line_number,
                        "rule": rule,
                        "severity": severity,
                        "text": line.strip(),
                    }
                )
    return {"file": path.relative_to(project_root).as_posix(), "hits": hits}


def audit(project_root: Path) -> dict:
    """审计全部 YAML 工作流，并给出冻结期安全结论。"""

    workflow_dir = project_root / ".github" / "workflows"
    workflows = [
        scan_workflow(path, project_root)
        for path in sorted(workflow_dir.glob("*.yml"))
    ]
    hits = [hit for workflow in workflows for hit in workflow["hits"]]
    high_hits = [hit for hit in hits if hit["severity"] == "HIGH"]
    production_files = {
        hit["file"]
        for hit in hits
        if hit["rule"] in {"collect", "news_add", "pages_copy", "pages_deploy"}
    }
    scheduled_files = {
        hit["file"] for hit in hits if hit["rule"] == "schedule"
    }
    bypass_files = sorted(production_files & scheduled_files)
    return {
        "status": "unsafe" if bypass_files else "passed",
        "workflowCount": len(workflows),
        "highRiskCount": len(high_hits),
        "scheduledProductionBypassFiles": bypass_files,
        "workflows": workflows,
        "recommendation": (
            "冻结期暂停 game-intel.yml 的 schedule，并在提交与 Pages "
            "发布前接入冻结校验。"
            if bypass_files
            else "未发现定时工作流直接写入生产文件的路径。"
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """输出只读审计报告。"""

    args = parse_args(argv)
    result = audit(args.project_root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "unsafe" else 0


if __name__ == "__main__":
    sys.exit(main())
