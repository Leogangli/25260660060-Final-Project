"""S1 数据质检：全部为确定性检查，不经过 LLM。

对应 prompts/ingest-qc-agent.md 的 5 项硬门禁检查。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

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


def run_qc(tb: pd.DataFrame, coa: pd.DataFrame, period: str, period_end: str) -> QCResult:
    """对单个期间的 TB 执行全部质检。tb 需已按 period 过滤。"""
    r = QCResult(period=period)
    bal_col = "期末余额(借正贷负,万元)"

    # 4. 必填与格式
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        r.fail("期间格式", {"period": period, "期望": "YYYY-MM"})
    else:
        r.ok("期间格式")
    if tb[["期间", "科目代码", bal_col]].isna().any().any():
        r.fail("必填字段", {"问题": "存在缺失值"})
    else:
        r.ok("必填字段")

    # 5. 期间锁定（防未来信息泄露）
    future = tb[tb["期间"] > period_end]
    if len(future):
        r.fail("期间锁定", {"未来期间行数": len(future), "示例": future["期间"].unique().tolist()[:3]})
    else:
        r.ok("期间锁定")

    cur = tb[tb["期间"] == period]

    # 1. 借贷平衡
    total = float(cur[bal_col].sum())
    if abs(total) > BALANCE_TOLERANCE:
        r.fail("借贷平衡", {"代数和": total, "容差": BALANCE_TOLERANCE})
    else:
        r.ok("借贷平衡", {"代数和": total})

    # 2. 未知科目
    known = set(coa["科目代码"].astype(str))
    unknown = sorted(set(cur["科目代码"].astype(str)) - known)
    if unknown:
        r.fail("未知科目", {"科目代码": unknown})
    else:
        r.ok("未知科目")

    # 3. 余额方向
    merged = cur.merge(coa[["科目代码", "正常余额方向"]], on="科目代码")
    merged["科目代码"] = merged["科目代码"].astype(str)
    bad = merged[
        ((merged["正常余额方向"] == "借") & (merged[bal_col] < 0))
        | ((merged["正常余额方向"] == "贷") & (merged[bal_col] > 0))
    ]
    if len(bad):
        r.fail("余额方向", {"异常科目": bad["科目代码"].tolist()})
    else:
        r.ok("余额方向")

    return r
