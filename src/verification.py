"""S6 独立核验：确定性重算、逐数引用、异常覆盖与期间边界检查。"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .deterministic.ingest import BALANCE_COLUMN
from .deterministic.metrics import detect_anomalies
from .llm.client import LLMClient

CITATION_RE = re.compile(r"（来源：[^）]+）")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])(?:\d{4}-\d{2}|[-+]?\d[\d,]*(?:\.\d+)?)")
AFTER_NUMBER_RE = re.compile(r"(?:万元|pp|%)?（来源：([^）]+)）")


def _add_metric_values(values: list[float], metric) -> None:
    if not isinstance(metric, dict):
        if metric is not None:
            values.append(float(metric))
        return
    for key, value in metric.items():
        if value is None:
            continue
        number = float(value)
        values.append(number)
        if key == "环比" or "毛利率" in key:
            values.append(number * 100)


def _source_values(citation: str, tb: pd.DataFrame, metrics: dict, anomalies: list[dict]) -> list[float]:
    values = []
    period, previous = metrics["period"], metrics["prev_period"]
    tb_match = re.search(r"TB ([0-9]+(?:\+[0-9]+)*)", citation)
    if tb_match:
        codes = tb_match.group(1).split("+")
        for current_period in [period, previous]:
            rows = tb[(tb["期间"] == current_period) & (tb["科目代码"].isin(codes))]
            amounts = [abs(float(value)) for value in rows[BALANCE_COLUMN]]
            values.extend(amounts)
            if amounts:
                values.append(abs(float(rows[BALANCE_COLUMN].sum())))
    for name, metric in metrics.items():
        if name not in {"period", "prev_period"} and f"指标表 {name}" in citation:
            _add_metric_values(values, metric)
            if name == "毛利率":
                values.extend([metric["本期"] * 100, metric["上期"] * 100])
    for anomaly in anomalies:
        if f"指标表 {anomaly['id']}" not in citation:
            continue
        value, threshold = float(anomaly["本期值"]), float(anomaly["阈值"])
        values.extend([value, threshold])
        if abs(value) <= 1 and abs(threshold) <= 1:
            values.extend([value * 100, threshold * 100])
        deviation = re.search(r"[-+]?\d+(?:\.\d+)?", anomaly["偏差"])
        if deviation:
            values.append(float(deviation.group()))
        for key, evidence in anomaly["证据"].items():
            if isinstance(evidence, (int, float)):
                number = float(evidence)
                values.append(number)
                if "毛利率" in key:
                    values.append(number * 100)
    return values


def _matches(actual: float, expected_values: list[float]) -> bool:
    return any(round(actual, 2) == round(expected, 2) for expected in expected_values)


def _issue(issues: list[dict], dimension: str, evidence: str, suggestion: str) -> None:
    issues.append({
        "问题ID": f"V{len(issues) + 1:03d}", "严重级": "阻塞", "维度": dimension,
        "证据": evidence, "修复建议": suggestion,
    })


def verify_report(
    report: str,
    tb: pd.DataFrame,
    metrics: dict,
    anomalies: list[dict],
    thresholds: dict,
) -> list[dict]:
    issues = []
    expected = detect_anomalies(metrics, thresholds)
    expected_ids = {anomaly["id"] for anomaly in expected}
    actual_ids = {anomaly["id"] for anomaly in anomalies}
    if expected_ids != actual_ids:
        _issue(issues, "数字正确性", f"重算异常 {sorted(expected_ids)} 与输入异常 {sorted(actual_ids)} 不一致", "重新执行 S4 并更新报告")

    masked = CITATION_RE.sub(lambda match: " " * len(match.group()), report)
    for match in NUMBER_RE.finditer(masked):
        token = match.group()
        citation_match = AFTER_NUMBER_RE.match(report[match.end():])
        if not citation_match:
            _issue(issues, "引用溯源", f"数字 {token} 缺少紧跟的来源标注", "为该数字补充真实来源")
            continue
        citation = citation_match.group(1)
        if re.fullmatch(r"20\d{2}-\d{2}", token):
            if token == metrics["period"] and "指标表 period" not in citation:
                _issue(issues, "引用溯源", f"期间 {token} 的来源错配为 {citation}", "改用指标表 period")
            continue
        actual = float(token.replace(",", ""))
        expected_values = _source_values(citation, tb, metrics, anomalies)
        if not expected_values:
            _issue(issues, "引用溯源", f"数字 {token} 的来源无法识别：{citation}", "改用 TB、BS/IS 或指标表有效行")
        elif not _matches(actual, expected_values):
            _issue(issues, "数字正确性", f"报告值 {token} 与来源 {citation} 的重算值不一致", "按确定性重算结果修正数字")

    for anomaly in anomalies:
        heading = f"### {anomaly['id']}"
        if heading not in report:
            _issue(issues, "覆盖检查", f"报告遗漏异常 {anomaly['id']}", "补充该异常的现象、证据、推测和建议")
            continue
        section = report.split(heading, 1)[1].split("### ", 1)[0]
        if "推测" not in section:
            _issue(issues, "口径与边界", f"{anomaly['id']} 的归因缺少推测标记", "将可能原因明确标注为推测")

    for future in set(re.findall(r"20\d{2}-\d{2}", masked)):
        if future > metrics["period"]:
            _issue(issues, "口径与边界", f"报告含未来期间 {future}", "删除晚于 period_end 的信息")
    return issues


def write_verification_report(
    report_path: str | Path,
    round_number: int,
    tb: pd.DataFrame,
    metrics: dict,
    anomalies: list[dict],
    thresholds: dict,
    client: LLMClient,
    system_prompt: str,
) -> dict:
    report = Path(report_path).read_text(encoding="utf-8")
    issues = verify_report(report, tb, metrics, anomalies, thresholds)
    summary = client.complete(system_prompt, f"VERIFY_SUMMARY\n问题数：{len(issues)}")
    output = Path(report_path).parent / f"verify_report_r{round_number}.md"
    lines = [
        "# 独立核验意见", "", f"- 总体结论：{'PASS' if not issues else 'REJECT'}",
        "- 核验方式：确定性重算、逐数引用检查、异常覆盖检查、期间边界检查。",
        f"- 独立核验摘要：{summary}", "", "## 问题清单",
    ]
    if not issues:
        lines.extend(["", "无阻塞问题。"])
    for issue in issues:
        lines.extend([
            "", f"### {issue['问题ID']}", "",
            f"- 严重级：{issue['严重级']}", f"- 维度：{issue['维度']}",
            f"- 证据：{issue['证据']}", f"- 修复建议：{issue['修复建议']}",
        ])
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"passed": not issues, "issues": issues, "artifacts": [str(output)]}
