#!/usr/bin/env python3
"""执行 S1 新闻冻结只读验收。

该脚本只读取生产新闻、RSS、冻结副本和测试代码。它不会修改
news.json、rss.xml 或任何冻结基线文件。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


EXPECTED_PROTECTED_ARTICLE_COUNT = 75
PRODUCTION_FILES = ("news.json", "rss.xml")
WORKFLOW_AUDIT_FILES = (
    Path(".agent-baselines") / "WORKFLOW_FREEZE_REVIEW.md",
    Path("scripts") / "audit-freeze-workflow.py",
    Path("tests") / "test_freeze_workflow_audit.py",
)


class AcceptanceError(RuntimeError):
    """S1 核心冻结验收失败。"""


def sha256_file(path: Path) -> str:
    """计算文件 SHA256。"""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise AcceptanceError(f"无法读取文件 {path}: {exc}") from exc


def load_module(path: Path, module_name: str) -> ModuleType:
    """从指定路径加载项目模块。"""

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AcceptanceError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    # dataclass 在解析注解时会通过 sys.modules 查询所属模块。
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def collect_production_hashes(project_root: Path) -> dict[str, str | None]:
    """记录生产文件当前哈希，不创建或修复缺失文件。"""

    hashes: dict[str, str | None] = {}
    for relative_path in PRODUCTION_FILES:
        path = project_root / relative_path
        hashes[relative_path] = sha256_file(path) if path.exists() else None
    return hashes


def inspect_core_freeze(project_root: Path) -> dict[str, Any]:
    """验证冻结清单、生产新闻和保护器的真实通过路径。"""

    baseline_tool = load_module(
        project_root / "scripts" / "check-news-baseline.py",
        "s1_check_news_baseline",
    )
    protector = load_module(
        project_root / "translation" / "protected_articles.py",
        "s1_protected_articles",
    )

    manifest_path = project_root / ".agent-baselines" / "manifest.json"
    if not manifest_path.exists():
        raise AcceptanceError(f"缺少冻结清单：{manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"冻结清单无法解析：{exc}") from exc
    if not isinstance(manifest, dict):
        raise AcceptanceError("冻结清单顶层必须是 JSON 对象")

    baseline_path_value = str(manifest.get("baselinePath") or "").strip()
    if not baseline_path_value:
        raise AcceptanceError("冻结清单缺少 baselinePath")
    baseline_path = Path(baseline_path_value)
    if not baseline_path.is_absolute():
        baseline_path = manifest_path.parent / baseline_path
    if not baseline_path.exists():
        raise AcceptanceError(f"冻结副本不存在：{baseline_path}")

    manifest_file_sha256 = sha256_file(manifest_path)
    baseline_file_sha256 = sha256_file(baseline_path)
    if baseline_file_sha256 != manifest.get("fileSha256"):
        raise AcceptanceError("冻结副本文件哈希与 manifest 不一致")

    try:
        baseline_report = baseline_tool.compare_with_baseline(
            current_path=project_root / "news.json",
            manifest_path=manifest_path,
            allow_appended_articles=False,
        )
    except Exception as exc:
        raise AcceptanceError(f"冻结基线校验异常：{exc}") from exc

    if baseline_report.get("status") != "unchanged_exact":
        raise AcceptanceError(
            f"生产新闻未保持完全不变：{baseline_report}"
        )
    if baseline_report.get("protectedArticleCount") != (
        EXPECTED_PROTECTED_ARTICLE_COUNT
    ):
        raise AcceptanceError("冻结文章数量不是 75")
    if baseline_report.get("currentArticleCount") != (
        EXPECTED_PROTECTED_ARTICLE_COUNT
    ):
        raise AcceptanceError("当前生产文章数量不是 75")
    if baseline_report.get("issues"):
        raise AcceptanceError(
            f"冻结校验存在差异：{baseline_report['issues']}"
        )

    try:
        baseline_document = json.loads(
            baseline_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"冻结副本无法解析：{exc}") from exc
    articles = baseline_document.get("articles")
    if not isinstance(articles, list):
        raise AcceptanceError("冻结副本缺少 articles 数组")

    try:
        protection_result = protector.validate_candidate_articles(
            articles,
            manifest_path,
            mode="release",
        )
    except Exception as exc:
        raise AcceptanceError(f"真实 75 条保护器验收入口失败：{exc}") from exc
    if protection_result.protected_count != EXPECTED_PROTECTED_ARTICLE_COUNT:
        raise AcceptanceError("保护器读取到的冻结文章数量不是 75")
    if protection_result.appended_count != 0:
        raise AcceptanceError("保护器把冻结文章误判为新增文章")

    ordered_keys = manifest.get("orderedArticleKeys")
    article_hashes = manifest.get("articleHashes")
    if not isinstance(ordered_keys, list) or len(ordered_keys) != (
        EXPECTED_PROTECTED_ARTICLE_COUNT
    ):
        raise AcceptanceError("manifest 冻结顺序键数量不是 75")
    if not isinstance(article_hashes, dict) or len(article_hashes) != (
        EXPECTED_PROTECTED_ARTICLE_COUNT
    ):
        raise AcceptanceError("manifest 逐篇哈希数量不是 75")
    if len(set(ordered_keys)) != EXPECTED_PROTECTED_ARTICLE_COUNT:
        raise AcceptanceError("manifest 冻结顺序键存在重复")

    return {
        "baselineStatus": baseline_report["status"],
        "protectedArticleCount": baseline_report["protectedArticleCount"],
        "currentArticleCount": baseline_report["currentArticleCount"],
        "baselineFileSha256": baseline_report["baselineFileSha256"],
        "currentFileSha256": baseline_report["currentFileSha256"],
        "articlesSha256": baseline_report["baselineArticlesSha256"],
        "currentPrefixArticlesSha256": baseline_report[
            "currentPrefixArticlesSha256"
        ],
        "manifestFileSha256": manifest_file_sha256,
        "protectionMode": protection_result.mode,
        "protectionProtectedCount": protection_result.protected_count,
        "appendedCount": protection_result.appended_count,
        "orderedKeyCount": len(ordered_keys),
        "articleHashCount": len(article_hashes),
    }


def run_required_tests(project_root: Path) -> dict[str, Any]:
    """运行冻结基线与保护器专项测试。"""

    command = [
        sys.executable,
        "-m",
        "unittest",
        "tests.test_news_baseline",
        "tests.test_protected_articles",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": " ".join(command),
        "returnCode": completed.returncode,
        "status": "passed" if completed.returncode == 0 else "failed",
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def workflow_audit_state(project_root: Path) -> dict[str, Any]:
    """报告 3 号调度审计产物的当前状态，不代替审查。"""

    missing = [
        relative_path.as_posix()
        for relative_path in WORKFLOW_AUDIT_FILES
        if not (project_root / relative_path).exists()
    ]
    return {
        "status": "pending_agent_3" if missing else "available_pending_review",
        "missingFiles": missing,
    }


def run_acceptance(
    project_root: Path,
    *,
    run_tests: bool = True,
) -> dict[str, Any]:
    """执行完整只读验收，并证明生产文件在验收前后没有变化。"""

    project_root = project_root.resolve()
    before_hashes = collect_production_hashes(project_root)
    result: dict[str, Any] = {
        "status": "failed",
        "projectRoot": project_root.as_posix(),
        "productionHashesBefore": before_hashes,
        "productionHashesAfter": None,
        "productionFilesUnchanged": False,
        "core": None,
        "tests": {
            "status": "skipped",
            "reason": "调用方通过 --skip-tests 跳过专项测试",
        },
        "workflowAudit": workflow_audit_state(project_root),
    }

    try:
        result["core"] = inspect_core_freeze(project_root)
        if run_tests:
            result["tests"] = run_required_tests(project_root)
    except Exception as exc:
        result["error"] = str(exc)

    after_hashes = collect_production_hashes(project_root)
    result["productionHashesAfter"] = after_hashes
    result["productionFilesUnchanged"] = before_hashes == after_hashes

    core_passed = result["core"] is not None and not result.get("error")
    tests_passed = (
        result["tests"]["status"] in {"passed", "skipped"}
    )
    if core_passed and tests_passed and result["productionFilesUnchanged"]:
        result["status"] = "passed"
    elif result.get("error") is None:
        if not tests_passed:
            result["error"] = "冻结专项测试失败"
        elif not result["productionFilesUnchanged"]:
            result["error"] = "验收期间生产文件发生变化"
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="只执行冻结数据检查，不启动专项 unittest",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """执行验收并输出可审计 JSON。"""

    args = parse_args(argv)
    result = run_acceptance(
        args.project_root,
        run_tests=not args.skip_tests,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
