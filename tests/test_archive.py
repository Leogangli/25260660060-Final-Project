"""P6：人工批准门、不可覆盖归档、基线与下期触发测试。"""
import json
from pathlib import Path

from run import run_workflow
from src.archive import archive_run


def _approval(approved: bool = True):
    return lambda: {"approved": approved, "批准人": "测试CFO", "意见": "测试审批"}


def test_pending_approval_blocks_archive_until_resume(tmp_path: Path):
    run_root = tmp_path / "runs"
    pending = run_workflow("2026-10", run_root=run_root, approval_handler=lambda: None)

    assert pending.state.stages["S7_approval"].status == "pending"
    assert "S8_archive" not in pending.state.stages
    assert not (run_root / "2026-10" / "archive").exists()

    approved = run_workflow(
        "2026-10", resume=True, run_root=run_root, approval_handler=_approval()
    )
    assert approved.state.stages["S7_approval"].status == "done"
    assert approved.state.stages["S8_archive"].status == "done"
    assert (run_root / "2026-10" / "archive" / "v1" / "manifest.json").exists()


def test_rejected_approval_pends_before_archive(tmp_path: Path):
    run_root = tmp_path / "runs"
    rejected = run_workflow("2026-10", run_root=run_root, approval_handler=_approval(False))

    assert rejected.state.stages["S7_approval"].status == "pending"
    assert "S8_archive" not in rejected.state.stages
    assert not (run_root / "2026-10" / "archive").exists()
    record = json.loads((run_root / "2026-10" / "approval.json").read_text(encoding="utf-8"))
    assert record["approved"] is False
    assert record["批准人"] == "测试CFO"
    assert record["批准时间"]


def test_archive_versions_are_immutable_and_baseline_updated(tmp_path: Path):
    run_root = tmp_path / "runs"
    run_dir = run_root / "2026-10"
    run_workflow("2026-10", run_root=run_root, approval_handler=_approval())
    manifest_v1 = run_dir / "archive" / "v1" / "manifest.json"
    original = manifest_v1.read_bytes()
    (run_dir / "report_v99.md").write_text("过期草稿", encoding="utf-8")
    (run_dir / "verify_report_r99.md").write_text("总体结论：REJECT", encoding="utf-8")

    archive_run(run_dir, "2026-10", "config/workflow.yaml")

    assert manifest_v1.read_bytes() == original
    manifest_v2 = run_dir / "archive" / "v2" / "manifest.json"
    assert json.loads(manifest_v2.read_text(encoding="utf-8"))["version"] == 2
    assert json.loads(manifest_v2.read_text(encoding="utf-8"))["final_report"] == "report_v1.md"
    assert not (run_dir / "archive" / "v2" / "report_v99.md").exists()
    baseline = json.loads((run_dir / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["period"] == "2026-10"
    assert baseline["metrics"]
    assert baseline["source_archive"].endswith("v2")
    trigger = json.loads((run_dir / "next_trigger.json").read_text(encoding="utf-8"))
    assert trigger["period"] == "2026-11"
    assert trigger["cron"] == "0 6 1 * *"



def test_new_run_does_not_reuse_previous_run_approval(tmp_path: Path):
    run_root = tmp_path / "runs"
    first = run_workflow("2026-10", run_root=run_root, approval_handler=_approval())
    assert (run_root / "2026-10" / "archive" / "v1").exists()

    second = run_workflow("2026-10", run_root=run_root, approval_handler=lambda: None)

    assert second.state.run_id != first.state.run_id
    assert second.state.stages["S7_approval"].status == "pending"
    assert "S8_archive" not in second.state.stages
    assert not (run_root / "2026-10" / "archive" / "v2").exists()
