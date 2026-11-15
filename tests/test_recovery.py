"""P7：真实进程中断恢复与下游产物未来信息泄露测试。"""
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from run import run_workflow
from src.core.state import StateStore


def test_kill_after_s4_and_resume_skips_s0_to_s4(tmp_path: Path, capsys: pytest.CaptureFixture):
    run_root = tmp_path / "runs"
    script = (
        "import os\n"
        "from run import run_workflow\n"
        f"run_workflow('2026-10', run_root={str(run_root)!r}, stop_after='S4_analysis')\n"
        "print('[interrupted] S4 完成后模拟 kill（退出码 137）', flush=True)\n"
        "os._exit(137)\n"
    )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    killed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        encoding="utf-8", env=env, check=False,
    )

    assert killed.returncode == 137, killed.stdout + killed.stderr
    assert "[done] S4_analysis" in killed.stdout
    assert "S5_report" not in killed.stdout
    state = StateStore(run_root / "2026-10").load()
    assert state is not None
    for stage_id in ["S0_ingest", "S1_qc", "S2_closecheck", "S3_statements", "S4_analysis"]:
        assert state.stages[stage_id].status == "done"
    assert "S5_report" not in state.stages

    capsys.readouterr()
    approval = lambda: {"approved": True, "批准人": "恢复测试CFO", "意见": "同意"}
    resumed = run_workflow(
        "2026-10", resume=True, run_root=run_root, approval_handler=approval
    )
    output = capsys.readouterr().out
    for stage_id in ["S0_ingest", "S1_qc", "S2_closecheck", "S3_statements", "S4_analysis"]:
        assert resumed.state.stages[stage_id].status == "skipped"
        assert f"[skipped] {stage_id}" in output
    assert resumed.state.stages["S8_archive"].status == "done"


def test_future_period_never_reaches_downstream_artifacts(tmp_path: Path):
    run_root = tmp_path / "runs"
    run_workflow("2026-09", run_root=run_root, stop_after="S4_analysis")
    run_dir = run_root / "2026-09"
    normalized = pd.read_csv(run_dir / "tb_normalized.csv")

    assert set(normalized["期间"]) <= {"2026-08", "2026-09"}
    for name in ["closecheck_report.md", "metrics.csv", "metrics.md", "anomalies.json", "anomalies.md"]:
        assert "2026-10" not in (run_dir / name).read_text(encoding="utf-8")
