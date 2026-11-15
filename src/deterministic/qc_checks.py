"""S1 数据质检：全部为确定性检查，不经过 LLM。

对应 prompts/ingest-qc-agent.md 的 5 项硬门禁检查。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .ingest import BALANCE_COLUMN, TB_COLUMNS

BALANCE_TOLERANCE = 0.005  # 万元


@dataclass
class QCResult:
    period: str
    passed: bool = True
    findings: list[dict] = field(default_factory=list)

    def fail(self, check: str, evidence: dict):
        self.passed = False
        self.findings.append({"check": check, "result": "FAIL", "evidence": evidence})

    def ok(self, check: str, evidence: dict | None = None):
        self.findings.append({"check": check, "result": "PASS", "evidence": evidence or {}})


def write_qc_report(result: QCResult, path: str | Path) -> Path:
    """将确定性检查结果写成可审计的 Markdown 报告。"""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 数据质检报告",
        "",
        f"- 期间：{result.period}",
        f"- 总体结论：{'PASS' if result.passed else 'FAIL'}",
        "",
        "| 检查项 | 结果 | 数值证据 |",
        "|---|---|---|",
    ]
    for finding in result.findings:
        evidence = json.dumps(finding["evidence"], ensure_ascii=False, default=str)
        evidence = evidence.replace("|", "\\|")
        lines.append(f"| {finding['check']} | {finding['result']} | {evidence} |")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def run_qc(tb: pd.DataFrame, coa: pd.DataFrame, period: str, period_end: str) -> QCResult:
    """对目标期间执行全部质检，任何 FAIL 均阻止下游阶段。"""
    r = QCResult(period=period)
    target_period_valid = bool(re.fullmatch(r"\d{4}-\d{2}", period))

    missing_columns = [column for column in TB_COLUMNS if column not in tb]
    if missing_columns:
        if target_period_valid:
            r.ok("期间格式")
        else:
            r.fail("期间格式", {"period": period, "期望": "YYYY-MM"})
        r.fail("必填字段", {"缺失列": missing_columns})
        return r

    checked = tb.copy()
    checked[BALANCE_COLUMN] = pd.to_numeric(checked[BALANCE_COLUMN], errors="coerce")
    periods = checked["期间"].astype("string").str.strip()
    valid_periods = periods.str.fullmatch(r"\d{4}-\d{2}")
    bad_periods = periods[periods.isna() | ~valid_periods]
    period_errors = {}
    if not target_period_valid:
        period_errors.update({"period": period, "期望": "YYYY-MM"})
    if len(bad_periods):
        period_errors["非法期间"] = bad_periods.fillna("<缺失>").unique().tolist()[:10]
    if period_errors:
        r.fail("期间格式", period_errors)
    else:
        r.ok("期间格式")

    problems = []
    for column in ["期间", "科目代码", BALANCE_COLUMN]:
        values = checked[column]
        missing = values.isna()
        if column != BALANCE_COLUMN:
            missing |= values.astype("string").str.strip().eq("").fillna(False)
        for index in checked.index[missing][:10]:
            problems.append({"行索引": str(index), "字段": column})
    cur = checked[periods == period]
    if cur.empty:
        problems.append({"问题": "目标期间无数据", "period": period})
    if problems:
        r.fail("必填字段", {"问题": problems, "金额列同时检查数值可解析性": True})
    else:
        r.ok("必填字段")

    future = checked[valid_periods.fillna(False) & (periods > period_end)]
    if len(future):
        r.fail("期间锁定", {"未来期间行数": len(future), "示例": future["期间"].unique().tolist()[:3]})
    else:
        r.ok("期间锁定")

    total = float(cur[BALANCE_COLUMN].sum())
    if abs(total) > BALANCE_TOLERANCE:
        r.fail("借贷平衡", {"代数和": total, "容差": BALANCE_TOLERANCE})
    else:
        r.ok("借贷平衡", {"代数和": total})

    known = set(coa["科目代码"].dropna().astype(str).str.strip())
    codes = cur["科目代码"].dropna().astype(str).str.strip()
    unknown = sorted(set(codes) - known)
    if unknown:
        r.fail("未知科目", {"科目代码": unknown})
    else:
        r.ok("未知科目")

    current = cur.copy()
    current["科目代码"] = current["科目代码"].astype("string").str.strip()
    mapping = coa[["科目代码", "正常余额方向"]].copy()
    mapping["科目代码"] = mapping["科目代码"].astype("string").str.strip()
    merged = current.merge(mapping, on="科目代码")
    bad = merged[
        ((merged["正常余额方向"] == "借") & (merged[BALANCE_COLUMN] < 0))
        | ((merged["正常余额方向"] == "贷") & (merged[BALANCE_COLUMN] > 0))
    ]
    if len(bad):
        r.fail("余额方向", {"异常科目": bad["科目代码"].astype(str).tolist()})
    else:
        r.ok("余额方向")

    return r
