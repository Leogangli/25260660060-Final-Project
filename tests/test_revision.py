"""P5：S5/S6 有界修订、恢复跳过与不收敛升级测试。"""
from pathlib import Path

import pytest

from src.core.orchestrator import Escalation, Orchestrator


def _input(tmp_path: Path) -> str:
    path = tmp_path / "data.csv"
    path.write_text("输入", encoding="utf-8")
    return str(path)


def test_revision_loop_converges_and_resume_skips(tmp_path: Path):
    source = _input(tmp_path)
    run_root = str(tmp_path / "runs")
    writes = []
    repairs = []

    def write(round_number, repair_package):
        writes.append(round_number)
        repairs.append(repair_package)
        path = Path(run_root) / "2026-10" / f"report_v{round_number}.md"
        path.write_text(f"版本 {round_number}", encoding="utf-8")
        return path

    def verify(round_number, report_path):
        passed = round_number == 2
        return {
            "passed": passed,
            "issues": [] if passed else [{"问题ID": "V001", "修复建议": "修复引用"}],
            "artifacts": [f"verify_report_r{round_number}.md"],
        }

    first = Orchestrator("2026-10", run_root=run_root)
    assert first.revision_loop(write, verify, [source]) is True
    assert writes == [1, 2]
    assert repairs[0] == []
    assert repairs[1][0]["问题ID"] == "V001"
    assert first.state.stages["S5_report"].status == "done"
    assert first.state.stages["S6_verify"].status == "done"

    resumed = Orchestrator("2026-10", run_root=run_root, resume=True)
    assert resumed.revision_loop(write, verify, [source]) is False
    assert writes == [1, 2]
    assert resumed.state.stages["S5_report"].status == "skipped"
    assert resumed.state.stages["S6_verify"].status == "skipped"


def test_revision_loop_escalates_after_two_revisions(tmp_path: Path):
    source = _input(tmp_path)
    writes = []

    def write(round_number, repair_package):
        writes.append(round_number)
        return f"report_v{round_number}.md"

    def verify(round_number, report_path):
        return {"passed": False, "issues": [{"问题ID": "V001"}], "artifacts": []}

    orchestrator = Orchestrator("2026-10", run_root=str(tmp_path / "runs"))
    with pytest.raises(Escalation, match="修订 2 轮仍不收敛"):
        orchestrator.revision_loop(write, verify, [source])

    assert writes == [1, 2, 3]
    assert orchestrator.state.stages["S6_verify"].status == "failed"
    assert (orchestrator.run_dir / "escalation.md").exists()
