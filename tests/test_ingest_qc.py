"""P2：S0 摄取、S1 五项硬门禁及 Orchestrator 接线测试。"""
from pathlib import Path

import pandas as pd
import pytest

from run import run_workflow
from src.deterministic.ingest import BALANCE_COLUMN, ingest_trial_balance
from src.deterministic.qc_checks import run_qc, write_qc_report

TB_PATH = Path("data/sample/trial_balance_2026Q3.csv")
COA_PATH = Path("data/sample/chart_of_accounts.csv")


@pytest.fixture
def data():
    tb = pd.read_csv(TB_PATH, dtype={"科目代码": str})
    tb = tb[tb["期间"] == "2026-10"].copy().reset_index(drop=True)
    tb[BALANCE_COLUMN] = tb[BALANCE_COLUMN].astype(object)
    coa = pd.read_csv(COA_PATH, dtype={"科目代码": str})
    return tb, coa


def test_ingest_locks_future_and_writes_normalized_csv(tmp_path: Path):
    output = ingest_trial_balance(TB_PATH, tmp_path / "runs" / "2026-09", "2026-09")
    normalized = pd.read_csv(output, dtype={"科目代码": str})

    assert output.name == "tb_normalized.csv"
    assert set(normalized["期间"]) == {"2026-08", "2026-09"}
    assert "2026-10" not in set(normalized["期间"])
    assert pd.api.types.is_numeric_dtype(normalized[BALANCE_COLUMN])


def test_qc_rejects_unbalanced_tb(data):
    tb, coa = data
    tb.loc[tb.index[0], BALANCE_COLUMN] += 1
    result = run_qc(tb, coa, "2026-10", "2026-10")
    assert not result.passed
    assert any(f["check"] == "借贷平衡" and f["result"] == "FAIL" for f in result.findings)


def test_qc_rejects_unknown_account(data):
    tb, coa = data
    tb.loc[tb.index[0], "科目代码"] = "9999"
    result = run_qc(tb, coa, "2026-10", "2026-10")
    assert not result.passed
    assert any(f["check"] == "未知科目" and f["result"] == "FAIL" for f in result.findings)


def test_qc_rejects_wrong_balance_direction(data):
    tb, coa = data
    mask = (tb["期间"] == "2026-10") & (tb["科目代码"] == "1122")
    tb.loc[mask, BALANCE_COLUMN] *= -1
    result = run_qc(tb, coa, "2026-10", "2026-10")
    assert not result.passed
    assert any(f["check"] == "余额方向" and f["result"] == "FAIL" for f in result.findings)


def test_qc_rejects_missing_code_and_non_numeric_amount(data):
    tb, coa = data
    tb.loc[tb.index[0], "科目代码"] = None
    tb.loc[tb.index[1], BALANCE_COLUMN] = "非数字"
    result = run_qc(tb, coa, "2026-10", "2026-10")
    assert not result.passed
    assert any(f["check"] == "必填字段" and f["result"] == "FAIL" for f in result.findings)


def test_qc_rejects_invalid_row_period(data):
    tb, coa = data
    tb.loc[tb.index[0], "期间"] = "2026-1"
    result = run_qc(tb, coa, "2026-10", "2026-10")
    assert not result.passed
    assert any(f["check"] == "期间格式" and f["result"] == "FAIL" for f in result.findings)


def test_qc_report_contains_every_finding(data, tmp_path: Path):
    tb, coa = data
    result = run_qc(tb, coa, "2026-10", "2026-10")
    output = write_qc_report(result, tmp_path / "runs" / "2026-10" / "qc_report.md")
    report = output.read_text(encoding="utf-8")

    assert result.passed
    assert "总体结论：PASS" in report
    for finding in result.findings:
        assert finding["check"] in report


def test_run_workflow_s0_s1_and_resume(tmp_path: Path, capsys: pytest.CaptureFixture):
    run_root = tmp_path / "runs"
    first = run_workflow("2026-10", run_root=run_root)
    assert first.state.stages["S0_ingest"].status == "done"
    assert first.state.stages["S1_qc"].status == "done"
    assert (run_root / "2026-10" / "tb_normalized.csv").exists()
    assert "总体结论：PASS" in (run_root / "2026-10" / "qc_report.md").read_text(encoding="utf-8")

    capsys.readouterr()
    resumed = run_workflow("2026-10", resume=True, run_root=run_root)
    output = capsys.readouterr().out
    assert resumed.state.stages["S0_ingest"].status == "skipped"
    assert resumed.state.stages["S1_qc"].status == "skipped"
    assert output.count("[skipped]") == 2
