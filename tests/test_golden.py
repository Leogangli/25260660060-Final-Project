"""黄金回归测试：固定样例数据 + 固定期望结果。
任何提示词/代码改动必须通过本测试方可采纳（见 docs/02 第 2.4 节）。
"""
import pandas as pd
import pytest
import yaml

from src.deterministic.metrics import compute_metrics, detect_anomalies
from src.deterministic.qc_checks import run_qc

TB_PATH = "data/sample/trial_balance_2026Q3.csv"
COA_PATH = "data/sample/chart_of_accounts.csv"


@pytest.fixture(scope="module")
def data():
    tb = pd.read_csv(TB_PATH, dtype={"科目代码": str})
    coa = pd.read_csv(COA_PATH, dtype={"科目代码": str})
    cfg = yaml.safe_load(open("config/workflow.yaml", encoding="utf-8"))
    return tb, coa, cfg


def test_qc_all_periods_pass(data):
    tb, coa, _ = data
    for period in ["2026-08", "2026-09", "2026-10"]:
        visible = tb[tb["期间"] <= period]  # 模拟数据访问层的期间锁定
        assert run_qc(visible, coa, period, period_end=period).passed, period


def test_qc_rejects_future_leakage(data):
    """防未来信息泄露：period_end 之前的数据访问层若混入未来期间，QC 必须 FAIL。"""
    tb, coa, _ = data
    r = run_qc(tb, coa, "2026-09", period_end="2026-09")  # tb 未过滤，含 2026-10 行
    assert not r.passed
    assert any(f["check"] == "期间锁定" for f in r.findings)


def test_planted_anomalies_all_detected(data):
    """2026-10 预埋的 4 类异常必须全部检出。"""
    tb, coa, cfg = data
    tb = tb[tb["期间"] <= "2026-10"]
    metrics = compute_metrics(tb, "2026-10", "2026-09")
    ids = {a["id"] for a in detect_anomalies(metrics, cfg["thresholds"])}
    assert {"A_AR", "A_GAP", "A_INV", "A_GM", "A_CASH"} <= ids


def test_metrics_numbers_match_tb(data):
    tb, _, _ = data
    m = compute_metrics(tb, "2026-10", "2026-09")
    assert m["应收账款"]["本期"] == 3700
    assert m["货币资金"]["本期"] == 15 + 125
    assert abs(m["毛利率"]["本期"] - (15000 - 10950) / 15000) < 1e-9


# TODO(Kimi Code): 追加
# - 中断恢复测试：S4 后 kill，resume 时断言 S0–S3 状态为 skipped（docs/03 验收清单第 4 条）
