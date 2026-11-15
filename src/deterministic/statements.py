"""S3 报表生成器：以确定性代码生成 BS/IS 简表及溯源信息。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .ingest import BALANCE_COLUMN

BALANCE_TOLERANCE = 0.005
STATEMENT_COLUMNS = ["报表项目", "金额(万元)", "组成科目"]


def _current_rows(tb: pd.DataFrame, coa: pd.DataFrame, period: str) -> pd.DataFrame:
    current = tb[tb["期间"] == period].copy()
    if current.empty:
        raise ValueError(f"目标期间无 TB 数据：{period}")
    current["科目代码"] = current["科目代码"].astype("string").str.strip()
    mapping = coa.copy()
    mapping["科目代码"] = mapping["科目代码"].astype("string").str.strip()
    merged = current.merge(mapping, on="科目代码", how="left", validate="many_to_one")
    if merged["类别"].isna().any():
        unknown = merged.loc[merged["类别"].isna(), "科目代码"].astype(str).tolist()
        raise ValueError(f"存在未映射科目：{unknown}")
    return merged


def _codes(rows: pd.DataFrame) -> str:
    return ",".join(rows["科目代码"].drop_duplicates().astype(str))


def _normal_amount(rows: pd.DataFrame) -> float:
    """按正常余额方向转为正数；减项的报表标签保留其扣减含义。"""
    if rows.empty:
        return 0.0
    directions = set(rows["正常余额方向"].dropna())
    if len(directions) != 1:
        raise ValueError(f"同一报表项目的余额方向不一致：{directions}")
    total = float(rows[BALANCE_COLUMN].sum())
    return total if directions == {"借"} else -total


def _mapped_rows(current: pd.DataFrame, coa: pd.DataFrame, category: str) -> list[dict]:
    rows = []
    mappings = coa.loc[coa["类别"] == category, "报表映射"].drop_duplicates()
    for item in mappings:
        members = current[(current["类别"] == category) & (current["报表映射"] == item)]
        rows.append({
            "报表项目": item,
            "金额(万元)": _normal_amount(members),
            "组成科目": _codes(members),
        })
    return rows


def build_income_statement(tb: pd.DataFrame, coa: pd.DataFrame, period: str) -> pd.DataFrame:
    """返回本年累计口径利润表，金额均为正常展示方向。"""
    current = _current_rows(tb, coa, period)
    items = {}
    sources = {}
    for item in ["营业收入", "营业成本", "销售费用", "管理费用", "财务费用"]:
        members = current[(current["类别"] == "损益") & (current["报表映射"] == item)]
        items[item] = _normal_amount(members)
        sources[item] = _codes(members)

    gross_profit = items["营业收入"] - items["营业成本"]
    operating_profit = gross_profit - sum(items[item] for item in ["销售费用", "管理费用", "财务费用"])
    pl_codes = _codes(current[current["类别"] == "损益"])
    rows = [
        {"报表项目": "营业收入", "金额(万元)": items["营业收入"], "组成科目": sources["营业收入"]},
        {"报表项目": "营业成本", "金额(万元)": items["营业成本"], "组成科目": sources["营业成本"]},
        {
            "报表项目": "毛利",
            "金额(万元)": gross_profit,
            "组成科目": ",".join(filter(None, [sources["营业收入"], sources["营业成本"]])),
        },
    ]
    rows.extend(
        {"报表项目": item, "金额(万元)": items[item], "组成科目": sources[item]}
        for item in ["销售费用", "管理费用", "财务费用"]
    )
    rows.append({"报表项目": "营业利润", "金额(万元)": operating_profit, "组成科目": pl_codes})
    return pd.DataFrame(rows, columns=STATEMENT_COLUMNS)


def build_balance_sheet(tb: pd.DataFrame, coa: pd.DataFrame, period: str) -> pd.DataFrame:
    """返回资产负债表简表，并强制校验资产等于负债及权益。"""
    current = _current_rows(tb, coa, period)
    asset_rows = current[current["类别"] == "资产"]
    liability_rows = current[current["类别"] == "负债"]
    equity_rows = current[current["类别"] == "权益"]
    pl_rows = current[current["类别"] == "损益"]

    assets = _mapped_rows(current, coa, "资产")
    liabilities = _mapped_rows(current, coa, "负债")
    equities = _mapped_rows(current, coa, "权益")
    asset_total = float(asset_rows[BALANCE_COLUMN].sum())
    liability_total = -float(liability_rows[BALANCE_COLUMN].sum())
    equity_before_profit = -float(equity_rows[BALANCE_COLUMN].sum())
    income = build_income_statement(tb, coa, period)
    current_profit = float(income.loc[income["报表项目"] == "营业利润", "金额(万元)"].iloc[0])
    equity_total = equity_before_profit + current_profit
    liabilities_and_equity = liability_total + equity_total
    difference = asset_total - liabilities_and_equity
    if abs(difference) > BALANCE_TOLERANCE:
        raise ValueError(f"资产不等于负债及权益，差额：{difference} 万元")

    pl_codes = _codes(pl_rows)
    rows = assets
    rows.append({"报表项目": "资产合计", "金额(万元)": asset_total, "组成科目": _codes(asset_rows)})
    rows.extend(liabilities)
    rows.append({"报表项目": "负债合计", "金额(万元)": liability_total, "组成科目": _codes(liability_rows)})
    rows.extend(equities)
    rows.append({"报表项目": "本年利润", "金额(万元)": current_profit, "组成科目": pl_codes})
    rows.append({
        "报表项目": "权益合计",
        "金额(万元)": equity_total,
        "组成科目": ",".join(filter(None, [_codes(equity_rows), pl_codes])),
    })
    rows.append({
        "报表项目": "负债和权益合计",
        "金额(万元)": liabilities_and_equity,
        "组成科目": ",".join(filter(None, [_codes(liability_rows), _codes(equity_rows), pl_codes])),
    })
    return pd.DataFrame(rows, columns=STATEMENT_COLUMNS)


def write_statement_files(
    statement: pd.DataFrame, run_dir: str | Path, stem: str, title: str, period: str
) -> list[Path]:
    """同时输出机器可读 CSV 与人读 Markdown，不依赖额外表格库。"""
    csv_path = Path(run_dir) / f"{stem}.csv"
    md_path = Path(run_dir) / f"{stem}.md"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    statement.to_csv(csv_path, index=False, encoding="utf-8-sig")
    lines = [f"# {title}", "", f"- 期间：{period}", "", "| 报表项目 | 金额(万元) | 组成科目 |", "|---|---:|---|"]
    for row in statement.itertuples(index=False):
        amount = f"{row[1]:.2f}".rstrip("0").rstrip(".")
        lines.append(f"| {row[0]} | {amount} | {row[2]} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [csv_path, md_path]


def build_cash_flow_simple(tb: pd.DataFrame, coa: pd.DataFrame, period: str, prev_period: str) -> pd.DataFrame:
    """由两期 BS 差额推导现金流量近似简表，须注明为近似口径。"""
    raise NotImplementedError("TODO(Kimi Code): 现金流量近似简表不在 docs/03 P3 范围")
