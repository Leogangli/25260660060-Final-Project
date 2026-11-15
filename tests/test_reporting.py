"""P5：报告数字引用、独立核验覆盖和期间边界测试。"""
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.analysis import ANALYSIS_RESPONSES, attribute_anomalies
from src.deterministic.metrics import compute_metrics, detect_anomalies
from src.llm.client import MockLLM
from src.reporting import build_report
from src.verification import verify_report

TB_PATH = Path("data/sample/trial_balance_2026Q3.csv")
CONFIG_PATH = Path("config/workflow.yaml")


@pytest.fixture
def data():
    tb = pd.read_csv(TB_PATH, dtype={"科目代码": str})
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    metrics = compute_metrics(tb, "2026-10", "2026-09")
    anomalies = attribute_anomalies(
        detect_anomalies(metrics, cfg["thresholds"]), MockLLM(ANALYSIS_RESPONSES), "分析提示词"
    )
    report = build_report("2026-10", metrics, anomalies)
    return report, tb, metrics, anomalies, cfg["thresholds"]


def test_valid_report_passes_deterministic_verification(data):
    report, tb, metrics, anomalies, thresholds = data
    assert "30.00%（来源：指标表 毛利率）" in report
    assert "27.00%（来源：指标表 毛利率）" in report
    assert verify_report(report, tb, metrics, anomalies, thresholds) == []


def test_verifier_rejects_wrong_number_and_missing_citation(data):
    report, tb, metrics, anomalies, thresholds = data
    report = report.replace("3,700万元（来源：TB 1122）", "9,999万元（来源：TB 1122）", 1)
    report = report.replace("2,600万元（来源：TB 1122）", "2,600万元", 1)
    issues = verify_report(report, tb, metrics, anomalies, thresholds)

    assert any(issue["维度"] == "数字正确性" and "9,999" in issue["证据"] for issue in issues)
    assert any(issue["维度"] == "引用溯源" and "2,600" in issue["证据"] for issue in issues)
    assert all(issue["严重级"] == "阻塞" for issue in issues)


def test_verifier_rejects_missing_anomaly_and_future_period(data):
    report, tb, metrics, anomalies, thresholds = data
    start = report.index("### A_INV")
    end = report.index("### A_GM")
    report = report[:start] + report[end:]
    report += "\n未来期间 2026-11（来源：指标表 period）。\n"
    issues = verify_report(report, tb, metrics, anomalies, thresholds)

    assert any(issue["维度"] == "覆盖检查" and "A_INV" in issue["证据"] for issue in issues)
    assert any(issue["维度"] == "口径与边界" and "2026-11" in issue["证据"] for issue in issues)



def test_verifier_shows_report_recalculation_and_source(data):
    report, tb, metrics, anomalies, thresholds = data
    report = report.replace(
        "| 货币资金 | 140万元（来源：TB 1001+1002）",
        "| 货币资金 | 1,400万元（来源：TB 1001+1002）",
        1,
    )
    issues = verify_report(report, tb, metrics, anomalies, thresholds)

    mismatch = next(issue for issue in issues if "1,400" in issue["证据"])
    assert mismatch["严重级"] == "阻塞"
    assert "报告原文 1,400" in mismatch["证据"]
    assert "重算值 140" in mismatch["证据"]
    assert "来源 TB 1001+1002" in mismatch["证据"]
