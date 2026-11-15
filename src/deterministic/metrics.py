"""S4 指标计算与阈值规则：全部为确定性代码。"""
from __future__ import annotations

import pandas as pd

BAL = "期末余额(借正贷负,万元)"


def _get(tb: pd.DataFrame, period: str, code: str) -> float:
    hit = tb[(tb["期间"] == period) & (tb["科目代码"] == code)]
    return float(hit[BAL].iloc[0]) if len(hit) else 0.0


def compute_metrics(tb: pd.DataFrame, period: str, prev_period: str) -> dict:
    """返回本期关键指标与环比变动。金额单位：万元。

    约定：资产/费用类借正；负债/权益/收入类贷负（取负号转为正数表达）。
    """
    g = lambda code, p=period: _get(tb, p, code)

    rev, rev_prev = -g("6001"), -g("6001", prev_period)
    cogs, cogs_prev = g("6401"), g("6401", prev_period)
    ar, ar_prev = g("1122"), g("1122", prev_period)
    inv, inv_prev = g("1403"), g("1403", prev_period)
    cash = g("1001") + g("1002")
    cash_prev = g("1001", prev_period) + g("1002", prev_period)
    st_loan, st_loan_prev = -g("2001"), -g("2001", prev_period)
    sell, sell_prev = g("6601"), g("6601", prev_period)
    adm, adm_prev = g("6602"), g("6602", prev_period)
    fin, fin_prev = g("6603"), g("6603", prev_period)

    def growth(cur: float, prev: float) -> float | None:
        return (cur - prev) / abs(prev) if prev else None

    gm = (rev - cogs) / rev if rev else None
    gm_prev = (rev_prev - cogs_prev) / rev_prev if rev_prev else None

    return {
        "period": period,
        "prev_period": prev_period,
        "营业收入": {"本期": rev, "上期": rev_prev, "环比": growth(rev, rev_prev)},
        "毛利率": {"本期": gm, "上期": gm_prev,
                  "变动pp": (gm - gm_prev) * 100 if gm is not None and gm_prev is not None else None},
        "应收账款": {"本期": ar, "上期": ar_prev, "环比": growth(ar, ar_prev)},
        "存货": {"本期": inv, "上期": inv_prev, "环比": growth(inv, inv_prev)},
        "货币资金": {"本期": cash, "上期": cash_prev, "环比": growth(cash, cash_prev), "变动额": cash - cash_prev},
        "短期借款": {"本期": st_loan, "上期": st_loan_prev, "新增": st_loan - st_loan_prev},
        "销售费用": {"本期": sell, "上期": sell_prev, "环比": growth(sell, sell_prev)},
        "管理费用": {"本期": adm, "上期": adm_prev, "环比": growth(adm, adm_prev)},
        "财务费用": {"本期": fin, "上期": fin_prev, "环比": growth(fin, fin_prev)},
        "应收收入背离pp": (
            (growth(ar, ar_prev) - growth(rev, rev_prev)) * 100
            if growth(ar, ar_prev) is not None and growth(rev, rev_prev) is not None else None
        ),
    }


def _higher_than(
    id: str, name: str, value: float, threshold: float, evidence: dict, pp_input: bool = False
) -> dict:
    difference = value - threshold if pp_input else (value - threshold) * 100
    return {
        "id": id, "指标": name, "本期值": value, "阈值": threshold,
        "偏差": f"{difference:+.2f}pp", "证据": evidence,
    }


def _lower_than(
    id: str, name: str, value: float, threshold: float, evidence: dict, pp_input: bool = False
) -> dict:
    difference = threshold - value if pp_input else (threshold - value) * 100
    return {
        "id": id, "指标": name, "本期值": value, "阈值": threshold,
        "偏差": f"低于阈值{difference:.2f}pp", "证据": evidence,
    }


def detect_anomalies(metrics: dict, thresholds: dict) -> list[dict]:
    """按 config/workflow.yaml 的 thresholds 判定异常，只输出命中项。"""
    out = []
    ar_g = metrics["应收账款"]["环比"]
    if ar_g is not None and ar_g > thresholds["ar_growth_qoq_pct"] / 100:
        out.append(_higher_than(
            "A_AR", "应收账款环比增幅", ar_g, thresholds["ar_growth_qoq_pct"] / 100,
            {"本期": metrics["应收账款"]["本期"], "上期": metrics["应收账款"]["上期"], "来源": "TB 1122"},
        ))

    gap = metrics["应收收入背离pp"]
    if gap is not None and gap > thresholds["ar_vs_revenue_gap_pp"]:
        out.append(_higher_than(
            "A_GAP", "应收增幅-收入增幅", gap, thresholds["ar_vs_revenue_gap_pp"],
            {
                "应收本期": metrics["应收账款"]["本期"], "应收上期": metrics["应收账款"]["上期"],
                "收入本期": metrics["营业收入"]["本期"], "收入上期": metrics["营业收入"]["上期"],
                "来源": "TB 1122/6001",
            }, pp_input=True,
        ))

    inv_g = metrics["存货"]["环比"]
    if inv_g is not None and inv_g > thresholds["inventory_growth_qoq_pct"] / 100:
        out.append(_higher_than(
            "A_INV", "存货环比增幅", inv_g, thresholds["inventory_growth_qoq_pct"] / 100,
            {"本期": metrics["存货"]["本期"], "上期": metrics["存货"]["上期"], "来源": "TB 1403"},
        ))

    gm_drop = metrics["毛利率"]["变动pp"]
    if gm_drop is not None and gm_drop < -thresholds["gross_margin_drop_pp"]:
        out.append(_lower_than(
            "A_GM", "毛利率变动(pp)", gm_drop, -thresholds["gross_margin_drop_pp"],
            {"本期毛利率": metrics["毛利率"]["本期"], "上期毛利率": metrics["毛利率"]["上期"], "来源": "TB 6001/6401"},
            pp_input=True,
        ))

    cash_g = metrics["货币资金"]["环比"]
    if cash_g is not None and cash_g < -thresholds["cash_drop_pct"] / 100:
        out.append(_lower_than(
            "A_CASH", "货币资金环比降幅", cash_g, -thresholds["cash_drop_pct"] / 100,
            {
                "本期": metrics["货币资金"]["本期"], "上期": metrics["货币资金"]["上期"],
                "变动额": metrics["货币资金"]["变动额"], "来源": "TB 1001/1002",
                "短期借款新增": metrics["短期借款"]["新增"],
            },
        ))

    expense_rules = [
        ("销售费用", "A_SELL_EXP", "TB 6601"),
        ("管理费用", "A_ADM_EXP", "TB 6602"),
        ("财务费用", "A_FIN_EXP", "TB 6603"),
    ]
    expense_threshold = thresholds["expense_growth_qoq_pct"] / 100
    for name, anomaly_id, source in expense_rules:
        growth = metrics[name]["环比"]
        if growth is not None and growth > expense_threshold:
            out.append(_higher_than(
                anomaly_id, f"{name}环比增幅", growth, expense_threshold,
                {"本期": metrics[name]["本期"], "上期": metrics[name]["上期"], "来源": source},
            ))
    return out
