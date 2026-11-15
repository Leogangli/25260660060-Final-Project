"""S4 混合分析：LLM 仅负责命中异常的归因与建议，数字来自确定性引擎。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .llm.client import LLMClient

ANALYSIS_CONTENT = {
    "A_AR": {
        "初步归因": "推测：可能存在客户回款周期延长或期末集中赊销，需以合同和回款明细验证。",
        "建议跟进": "由应收会计逐笔核对应收账龄、期后回款和主要客户信用额度，并形成催收清单。",
    },
    "A_GAP": {
        "初步归因": "推测：应收增长与收入增长不匹配，可能存在信用政策放宽或收入确认后回款滞后。",
        "建议跟进": "由财务经理按客户拆分应收与收入增量，核查信用政策变更及大额未回款订单。",
    },
    "A_INV": {
        "初步归因": "推测：可能存在备货增加、销售周转放缓或呆滞物料积压，需以库存明细验证。",
        "建议跟进": "由仓储和财务联合核对库龄、周转及呆滞明细，制定滞销料处置和采购复核计划。",
    },
    "A_GM": {
        "初步归因": "推测：可能受到产品售价、材料成本或销售结构变化影响，需以订单成本明细验证。",
        "建议跟进": "由财务经理按产品和客户拆解收入及成本，复核降价、材料涨价和成本结转记录。",
    },
    "A_CASH": {
        "初步归因": "推测：经营回款、存货占用及新增短期借款后的资金使用可能共同影响现金余额。",
        "建议跟进": "由资金会计编制滚动资金计划，逐笔核查大额付款、借款到账及资金用途。",
    },
    "A_SELL_EXP": {
        "初步归因": "推测：销售费用增长可能与推广、运输或佣金支出变化有关，需以费用明细验证。",
        "建议跟进": "由销售财务逐项复核大额费用合同、报销及同比预算差异。",
    },
    "A_ADM_EXP": {
        "初步归因": "推测：管理费用增长可能与一次性管理支出或薪酬服务成本变化有关。",
        "建议跟进": "由费用会计按供应商和费用类别核查大额及非常规支出。",
    },
    "A_FIN_EXP": {
        "初步归因": "推测：财务费用增长可能与借款规模或利率变化有关，需以借款合同验证。",
        "建议跟进": "由资金会计核对借款本金、利率、计息天数和利息入账记录。",
    },
}
ANALYSIS_RESPONSES = {
    key: json.dumps(value, ensure_ascii=False) for key, value in ANALYSIS_CONTENT.items()
}

METRIC_SOURCES = {
    "营业收入": "TB 6001", "毛利率": "TB 6001/6401", "应收账款": "TB 1122",
    "存货": "TB 1403", "货币资金": "TB 1001/1002", "短期借款": "TB 2001",
    "销售费用": "TB 6601", "管理费用": "TB 6602", "财务费用": "TB 6603",
    "应收收入背离pp": "TB 1122/6001",
}


def attribute_anomalies(anomalies: list[dict], client: LLMClient, system_prompt: str) -> list[dict]:
    """逐条调用 LLM，并强制校验 JSON 结构和推测标记。"""
    attributed = []
    for anomaly in anomalies:
        evidence_input = {
            "id": anomaly["id"], "指标": anomaly["指标"], "本期值": anomaly["本期值"],
            "阈值": anomaly["阈值"], "偏差": anomaly["偏差"], "证据": anomaly["证据"],
        }
        user = (
            "请仅根据以下数字证据输出 JSON，键为“初步归因”和“建议跟进”。"
            "归因必须以“推测：”开头，不得添加其他数字或事实。\n"
            + json.dumps(evidence_input, ensure_ascii=False)
        )
        response = client.complete(system_prompt, user).strip()
        if response.startswith("```"):
            response = response.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        content = json.loads(response)
        attribution = content.get("初步归因", "")
        follow_up = content.get("建议跟进", "")
        if "推测" not in attribution or not follow_up:
            raise ValueError(f"LLM 归因缺少推测标记或跟进建议：{anomaly['id']}")
        attributed.append({**anomaly, "初步归因": attribution, "建议跟进": follow_up})
    return attributed


def metrics_to_frame(metrics: dict) -> pd.DataFrame:
    rows = []
    for name, values in metrics.items():
        if name in {"period", "prev_period"}:
            continue
        row = {"期间": metrics["period"], "上期期间": metrics["prev_period"], "指标": name}
        if isinstance(values, dict):
            row.update(values)
        else:
            row["本期"] = values
        row["来源"] = METRIC_SOURCES[name]
        rows.append(row)
    columns = ["期间", "上期期间", "指标", "本期", "上期", "环比", "变动pp", "变动额", "新增", "来源"]
    return pd.DataFrame(rows).reindex(columns=columns)


def _format_value(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def write_analysis_outputs(
    metrics: dict, anomalies: list[dict], run_dir: str | Path, period: str
) -> list[Path]:
    """输出指标表和异常清单，数字证据与推测性文字分列。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_csv = run_dir / "metrics.csv"
    metrics_md = run_dir / "metrics.md"
    anomalies_json = run_dir / "anomalies.json"
    anomalies_md = run_dir / "anomalies.md"

    frame = metrics_to_frame(metrics)
    frame.to_csv(metrics_csv, index=False, encoding="utf-8-sig")
    metric_lines = ["# 指标表", "", f"- 期间：{period}", "", "| " + " | ".join(frame.columns) + " |"]
    metric_lines.append("|" + "|".join(["---"] * len(frame.columns)) + "|")
    for row in frame.itertuples(index=False, name=None):
        metric_lines.append("| " + " | ".join(_format_value(value) for value in row) + " |")
    metrics_md.write_text("\n".join(metric_lines) + "\n", encoding="utf-8")

    anomalies_json.write_text(json.dumps(anomalies, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 异常清单", "", f"- 期间：{period}", f"- 异常条数：{len(anomalies)}"]
    for anomaly in anomalies:
        lines.extend([
            "", f"## {anomaly['id']} {anomaly['指标']}", "",
            f"- 本期值：{anomaly['本期值']}", f"- 阈值：{anomaly['阈值']}",
            f"- 偏差：{anomaly['偏差']}",
            f"- 数字证据：{json.dumps(anomaly['证据'], ensure_ascii=False)}",
            f"- 初步归因：{anomaly['初步归因']}", f"- 建议跟进：{anomaly['建议跟进']}",
        ])
    anomalies_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [metrics_csv, metrics_md, anomalies_json, anomalies_md]
