"""S2 关账检查：规则判定与 MockLLM 说明分离，不调整任何数据。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .deterministic.ingest import BALANCE_COLUMN
from .llm.client import LLMClient

CLOSECHECK_RESPONSES = {
    "CC_DEP": "累计折旧变动已由规则核对，需保留折旧计提凭证供复核。",
    "CC_ACCRUAL": "应计项目已按余额和环比规则检查，异常波动需核对计提依据。",
    "CC_RECEIVABLE": "往来科目大额变动需结合客户、供应商及合同明细确认。",
    "CC_PL": "收入、成本和费用完整性已由非零规则检查。",
    "CC_NEW": "新增或新增发生额科目需由会计确认业务背景和入账依据。",
}


def _amount(tb: pd.DataFrame, period: str, code: str) -> float:
    rows = tb[(tb["期间"] == period) & (tb["科目代码"] == code)]
    return float(rows[BALANCE_COLUMN].sum())


def _growth(current: float, previous: float) -> float | None:
    return (current - previous) / abs(previous) if previous else None


def build_closecheck_findings(tb: pd.DataFrame, period: str, previous: str) -> list[dict]:
    findings = []
    dep, dep_prev = -_amount(tb, period, "1602"), -_amount(tb, previous, "1602")
    findings.append({
        "id": "CC_DEP", "检查项": "折旧/摊销",
        "状态": "已确认" if dep > dep_prev else "提示关注",
        "证据": {"本期累计折旧": dep, "上期累计折旧": dep_prev, "变动": dep - dep_prev, "来源": "TB 1602"},
    })

    accrual_evidence = {}
    accrual_alert = False
    for code, name in [("2211", "应付职工薪酬"), ("2221", "应交税费")]:
        current, prior = -_amount(tb, period, code), -_amount(tb, previous, code)
        growth = _growth(current, prior)
        accrual_alert |= current == 0 or growth is not None and abs(growth) > 0.5
        accrual_evidence[name] = {"本期": current, "上期": prior, "环比": growth, "来源": f"TB {code}"}
    findings.append({
        "id": "CC_ACCRUAL", "检查项": "应计项目",
        "状态": "提示关注" if accrual_alert else "已确认", "证据": accrual_evidence,
    })

    ar, ar_prev = _amount(tb, period, "1122"), _amount(tb, previous, "1122")
    ap, ap_prev = -_amount(tb, period, "2202"), -_amount(tb, previous, "2202")
    ar_growth, ap_growth = _growth(ar, ar_prev), _growth(ap, ap_prev)
    receivable_alert = any(value is not None and abs(value) > 0.2 for value in [ar_growth, ap_growth])
    findings.append({
        "id": "CC_RECEIVABLE", "检查项": "往来科目",
        "状态": "提示关注" if receivable_alert else "已确认",
        "证据": {
            "应收账款": {"本期": ar, "上期": ar_prev, "环比": ar_growth, "来源": "TB 1122"},
            "应付账款": {"本期": ap, "上期": ap_prev, "环比": ap_growth, "来源": "TB 2202"},
        },
    })

    revenue, cost = -_amount(tb, period, "6001"), _amount(tb, period, "6401")
    expenses = sum(_amount(tb, period, code) for code in ["6601", "6602", "6603"])
    findings.append({
        "id": "CC_PL", "检查项": "损益完整性",
        "状态": "已确认" if revenue and cost and expenses else "提示关注",
        "证据": {"营业收入": revenue, "营业成本": cost, "期间费用": expenses, "来源": "TB 6001/6401/6601/6602/6603"},
    })

    current_codes = set(tb.loc[tb["期间"] == period, "科目代码"])
    previous_rows = tb[tb["期间"] == previous]
    new_accounts = []
    for code in sorted(current_codes):
        previous_amount = _amount(tb, previous, code)
        current_amount = _amount(tb, period, code)
        if (previous_rows["科目代码"] == code).sum() == 0 or previous_amount == 0 and current_amount != 0:
            new_accounts.append({"科目代码": code, "本期": current_amount, "上期": previous_amount, "来源": f"TB {code}"})
    findings.append({
        "id": "CC_NEW", "检查项": "非常规信号",
        "状态": "需人工确认" if new_accounts else "已确认", "证据": {"新增或新增发生额科目": new_accounts},
    })
    return findings


def write_closecheck_report(
    tb: pd.DataFrame,
    period: str,
    previous: str,
    client: LLMClient,
    system_prompt: str,
    output: str | Path,
) -> Path:
    findings = build_closecheck_findings(tb, period, previous)
    lines = ["# 关账检查清单", "", f"- 期间：{period}", "- 说明：仅提示风险，不做调账或数据修改。"]
    for finding in findings:
        user = finding["id"] + "\n" + json.dumps(finding["证据"], ensure_ascii=False)
        explanation = client.complete(system_prompt, user)
        lines.extend([
            "", f"## {finding['检查项']}", "", f"【{finding['状态']}】",
            f"- TB 数值证据：{json.dumps(finding['证据'], ensure_ascii=False)}",
            f"- 检查说明：{explanation}",
        ])
    output = Path(output)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
