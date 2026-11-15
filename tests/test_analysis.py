"""P4：指标阈值、MockLLM 归因、异常产物与期间边界测试。"""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.analysis import (
    ANALYSIS_RESPONSES,
    attribute_anomalies,
    metrics_to_frame,
    write_analysis_outputs,
)
from src.deterministic.metrics import compute_metrics, detect_anomalies
from src.llm.client import MockLLM

TB_PATH = Path("data/sample/trial_balance_2026Q3.csv")
CONFIG_PATH = Path("config/workflow.yaml")


@pytest.fixture
def data():
    tb = pd.read_csv(TB_PATH, dtype={"科目代码": str})
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    metrics = compute_metrics(tb, "2026-10", "2026-09")
    return tb, cfg, metrics


def test_all_planted_anomalies_have_required_fields(data):
    _, cfg, metrics = data
    anomalies = detect_anomalies(metrics, cfg["thresholds"])
    by_id = {anomaly["id"]: anomaly for anomaly in anomalies}

    assert set(by_id) == {"A_AR", "A_GAP", "A_INV", "A_GM", "A_CASH"}
    for anomaly in anomalies:
        assert {"本期值", "阈值", "偏差", "证据"} <= set(anomaly)
    assert by_id["A_AR"]["偏差"] == "+22.31pp"
    assert by_id["A_GAP"]["偏差"] == "+22.01pp"
    assert by_id["A_INV"]["偏差"] == "+33.82pp"
    assert by_id["A_GM"]["偏差"] == "低于阈值1.00pp"
    assert by_id["A_GM"]["证据"]["本期毛利率"] == pytest.approx(0.27)
    assert by_id["A_GM"]["证据"]["上期毛利率"] == pytest.approx(0.30)
    assert by_id["A_CASH"]["证据"]["变动额"] == -1775
    assert by_id["A_CASH"]["证据"]["短期借款新增"] == 600


def test_expense_threshold_is_strict_and_has_deviation(data):
    _, cfg, metrics = data
    assert metrics["销售费用"]["环比"] == pytest.approx(0.15)
    assert "A_SELL_EXP" not in {a["id"] for a in detect_anomalies(metrics, cfg["thresholds"])}

    changed = copy.deepcopy(metrics)
    changed["销售费用"]["环比"] = 0.16
    anomalies = detect_anomalies(changed, cfg["thresholds"])
    expense = next(a for a in anomalies if a["id"] == "A_SELL_EXP")
    assert expense["偏差"] == "+1.00pp"
    assert expense["证据"]["来源"] == "TB 6601"


def test_mock_llm_attribution_is_marked_as_assumption(data):
    _, cfg, metrics = data
    anomalies = detect_anomalies(metrics, cfg["thresholds"])
    attributed = attribute_anomalies(anomalies, MockLLM(ANALYSIS_RESPONSES), "分析提示词")

    assert len(attributed) == 5
    for anomaly in attributed:
        assert anomaly["初步归因"].startswith("推测：")
        assert anomaly["建议跟进"]
        assert anomaly["证据"] == next(a["证据"] for a in anomalies if a["id"] == anomaly["id"])


def test_llm_attribution_rejects_missing_assumption_marker(data):
    _, cfg, metrics = data
    anomaly = detect_anomalies(metrics, cfg["thresholds"])[0]
    client = MockLLM({
        "A_AR": json.dumps({"初步归因": "确定是回款放缓", "建议跟进": "核查"}, ensure_ascii=False)
    })

    with pytest.raises(ValueError, match="推测标记"):
        attribute_anomalies([anomaly], client, "分析提示词")


def test_analysis_outputs_include_metrics_and_anomalies(data, tmp_path: Path):
    _, cfg, metrics = data
    anomalies = attribute_anomalies(
        detect_anomalies(metrics, cfg["thresholds"]), MockLLM(ANALYSIS_RESPONSES), "分析提示词"
    )
    artifacts = write_analysis_outputs(metrics, anomalies, tmp_path, "2026-10")

    assert [path.name for path in artifacts] == ["metrics.csv", "metrics.md", "anomalies.json", "anomalies.md"]
    loaded = json.loads((tmp_path / "anomalies.json").read_text(encoding="utf-8"))
    assert len(loaded) == 5
    frame = pd.read_csv(tmp_path / "metrics.csv")
    cash = frame[frame["指标"] == "货币资金"].iloc[0]
    assert cash["变动额"] == -1775
    assert cash["来源"] == "TB 1001/1002"
    assert metrics_to_frame(metrics)["指标"].nunique() == len(metrics_to_frame(metrics))
    markdown = (tmp_path / "anomalies.md").read_text(encoding="utf-8")
    assert "初步归因：推测：" in markdown
    assert "建议跟进：" in markdown


def test_analysis_prompt_has_no_future_period_rows():
    tb = pd.read_csv(TB_PATH, dtype={"科目代码": str})
    visible = tb[tb["期间"] <= "2026-09"]
    metrics = compute_metrics(visible, "2026-09", "2026-08")
    thresholds = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["thresholds"]
    thresholds["ar_growth_qoq_pct"] = 0
    thresholds["inventory_growth_qoq_pct"] = 0
    anomalies = detect_anomalies(metrics, thresholds)

    class RecordingLLM:
        def __init__(self):
            self.prompts = []

        def complete(self, system: str, user: str) -> str:
            self.prompts.append(user)
            return json.dumps({"初步归因": "推测：需核实", "建议跟进": "核查明细"}, ensure_ascii=False)

    client = RecordingLLM()
    attribute_anomalies(anomalies, client, "分析提示词")
    assert client.prompts
    assert all("2026-10" not in prompt for prompt in client.prompts)
