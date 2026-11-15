"""S5 Report Writer：数字来自结构化产物，MockLLM 负责确定性行文。"""
from __future__ import annotations

import json
from pathlib import Path

from .llm.client import LLMClient


def _amount(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _pct(value: float, signed: bool = False) -> str:
    return f"{value * 100:{'+' if signed else ''}.2f}%"


def build_report(period: str, metrics: dict, anomalies: list[dict]) -> str:
    by_id = {anomaly["id"]: anomaly for anomaly in anomalies}
    ar = metrics["应收账款"]
    revenue = metrics["营业收入"]
    inventory = metrics["存货"]
    cash = metrics["货币资金"]
    margin = metrics["毛利率"]
    loan = metrics["短期借款"]
    lines = [
        "# 管理层月度报告", "",
        f"- 报告期间：{period}（来源：指标表 period）", "",
        "## 本月结论", "",
        f"- 应收账款本期{_amount(ar['本期'])}万元（来源：TB 1122），较上期{_amount(ar['上期'])}万元（来源：TB 1122）增长{_pct(ar['环比'])}（来源：指标表 A_AR）；营业收入同期增长{_pct(revenue['环比'])}（来源：指标表 营业收入）。",
        f"- 存货本期{_amount(inventory['本期'])}万元（来源：TB 1403），环比增长{_pct(inventory['环比'])}（来源：指标表 A_INV），需核查库存周转和呆滞风险。",
        f"- 毛利率由上期{_pct(margin['上期'])}（来源：指标表 毛利率）变为本期{_pct(margin['本期'])}（来源：指标表 毛利率），变动{margin['变动pp']:.2f}pp（来源：指标表 A_GM）。",
        f"- 货币资金由上期{_amount(cash['上期'])}万元（来源：TB 1001+1002）降至本期{_amount(cash['本期'])}万元（来源：TB 1001+1002），环比{_pct(cash['环比'])}（来源：指标表 A_CASH）；短期借款新增{_amount(loan['新增'])}万元（来源：TB 2001）。",
        "", "## 关键指标一览表", "",
        "| 指标 | 本期 | 上期 | 变动 |", "|---|---:|---:|---:|",
        f"| 营业收入 | {_amount(revenue['本期'])}万元（来源：TB 6001） | {_amount(revenue['上期'])}万元（来源：TB 6001） | {_pct(revenue['环比'], signed=True)}（来源：指标表 营业收入） |",
        f"| 应收账款 | {_amount(ar['本期'])}万元（来源：TB 1122） | {_amount(ar['上期'])}万元（来源：TB 1122） | {_pct(ar['环比'], signed=True)}（来源：指标表 A_AR） |",
        f"| 存货 | {_amount(inventory['本期'])}万元（来源：TB 1403） | {_amount(inventory['上期'])}万元（来源：TB 1403） | {_pct(inventory['环比'], signed=True)}（来源：指标表 A_INV） |",
        f"| 毛利率 | {_pct(margin['本期'])}（来源：指标表 毛利率） | {_pct(margin['上期'])}（来源：指标表 毛利率） | {margin['变动pp']:.2f}pp（来源：指标表 A_GM） |",
        f"| 货币资金 | {_amount(cash['本期'])}万元（来源：TB 1001+1002） | {_amount(cash['上期'])}万元（来源：TB 1001+1002） | {_pct(cash['环比'], signed=True)}（来源：指标表 A_CASH） |",
        "", "## 异常与风险",
    ]

    for anomaly in anomalies:
        anomaly_id = anomaly["id"]
        lines.extend(["", f"### {anomaly_id} {anomaly['指标']}", ""])
        if anomaly_id == "A_AR":
            lines.append(f"- 现象：应收账款环比增长{_pct(ar['环比'])}（来源：指标表 A_AR），高于阈值{_pct(anomaly['阈值'])}（来源：指标表 A_AR），偏差{anomaly['偏差']}（来源：指标表 A_AR）。")
            lines.append(f"- 数字证据：本期{_amount(ar['本期'])}万元（来源：TB 1122），上期{_amount(ar['上期'])}万元（来源：TB 1122）。")
        elif anomaly_id == "A_GAP":
            lines.append(f"- 现象：应收增幅与收入增幅之差为{anomaly['本期值']:.2f}pp（来源：指标表 A_GAP），高于阈值{anomaly['阈值']:.2f}pp（来源：指标表 A_GAP），偏差{anomaly['偏差']}（来源：指标表 A_GAP）。")
            lines.append(f"- 数字证据：应收增长{_pct(ar['环比'])}（来源：指标表 A_AR），收入增长{_pct(revenue['环比'])}（来源：指标表 营业收入）。")
        elif anomaly_id == "A_INV":
            lines.append(f"- 现象：存货环比增长{_pct(inventory['环比'])}（来源：指标表 A_INV），高于阈值{_pct(anomaly['阈值'])}（来源：指标表 A_INV），偏差{anomaly['偏差']}（来源：指标表 A_INV）。")
            lines.append(f"- 数字证据：本期{_amount(inventory['本期'])}万元（来源：TB 1403），上期{_amount(inventory['上期'])}万元（来源：TB 1403）。")
        elif anomaly_id == "A_GM":
            lines.append(f"- 现象：毛利率变动{anomaly['本期值']:.2f}pp（来源：指标表 A_GM），低于阈值{anomaly['阈值']:.2f}pp（来源：指标表 A_GM），偏差{anomaly['偏差']}（来源：指标表 A_GM）。")
            lines.append(f"- 数字证据：本期毛利率{_pct(margin['本期'])}（来源：指标表 毛利率），上期毛利率{_pct(margin['上期'])}（来源：指标表 毛利率）。")
        elif anomaly_id == "A_CASH":
            lines.append(f"- 现象：货币资金环比{_pct(cash['环比'])}（来源：指标表 A_CASH），低于阈值{_pct(anomaly['阈值'])}（来源：指标表 A_CASH），偏差{anomaly['偏差']}（来源：指标表 A_CASH）。")
            lines.append(f"- 数字证据：本期{_amount(cash['本期'])}万元（来源：TB 1001+1002），上期{_amount(cash['上期'])}万元（来源：TB 1001+1002），变动额{_amount(cash['变动额'])}万元（来源：指标表 货币资金），短期借款新增{_amount(loan['新增'])}万元（来源：TB 2001）。")
        else:
            lines.append(f"- 现象：{anomaly['指标']}本期值为{anomaly['本期值']}（来源：指标表 {anomaly_id}），阈值为{anomaly['阈值']}（来源：指标表 {anomaly_id}）。")
        lines.extend([
            f"- 可能原因：{anomaly['初步归因']}",
            f"- 建议动作：{anomaly['建议跟进']}",
            "- 责任人建议：由对应业务负责人与财务经理共同复核并留存结果。",
        ])

    lines.extend([
        "", "## 下月关注事项", "",
        "- 跟踪应收对账、期后回款和信用额度复核结果，由应收会计留存催收清单。",
        "- 复核存货库龄、呆滞处置和采购计划，由仓储与财务共同确认。",
        "- 拆解毛利率变动原因并滚动更新资金计划，由财务经理复核。",
        "", "## 数据口径与截止时点", "",
        f"- 数据截止期间：{period}（来源：指标表 period）；仅使用合成样例数据。",
        "- 金额单位为万元；可能原因均为推测，须以业务明细和凭证复核。",
    ])
    return "\n".join(lines) + "\n"


def write_report_candidate(
    run_dir: str | Path,
    round_number: int,
    period: str,
    metrics: dict,
    anomalies: list[dict],
    closecheck_report: str,
    repair_package: list[dict] | None,
    client: LLMClient,
    system_prompt: str,
) -> Path:
    body = build_report(period, metrics, anomalies)
    responses = {f"report_v{version}": body for version in [1, 2, 3]}
    if hasattr(client, "responses"):
        client.responses.update(responses)
    user = json.dumps({
        "任务": f"report_v{round_number}", "修复任务包": repair_package or [],
        "关账检查": closecheck_report, "排版参考": body,
    }, ensure_ascii=False)
    report = client.complete(system_prompt, user)
    output = Path(run_dir) / f"report_v{round_number}.md"
    output.write_text(report, encoding="utf-8")
    return output
