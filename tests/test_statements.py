"""P3：BS/IS 数字、溯源、平衡不变量与双格式输出测试。"""
from pathlib import Path

import pandas as pd
import pytest

from src.deterministic.ingest import BALANCE_COLUMN
from src.deterministic.statements import (
    build_balance_sheet,
    build_income_statement,
    write_statement_files,
)

TB_PATH = Path("data/sample/trial_balance_2026Q3.csv")
COA_PATH = Path("data/sample/chart_of_accounts.csv")


@pytest.fixture
def data():
    tb = pd.read_csv(TB_PATH, dtype={"科目代码": str})
    coa = pd.read_csv(COA_PATH, dtype={"科目代码": str})
    return tb, coa


def _amount(tb: pd.DataFrame, code: str) -> float:
    rows = tb[(tb["期间"] == "2026-10") & (tb["科目代码"] == code)]
    return float(rows[BALANCE_COLUMN].sum())


def test_income_statement_matches_tb_recalculation(data):
    tb, coa = data
    statement = build_income_statement(tb, coa, "2026-10").set_index("报表项目")
    revenue = -_amount(tb, "6001")
    cost = _amount(tb, "6401")
    expenses = sum(_amount(tb, code) for code in ["6601", "6602", "6603"])

    assert statement.loc["营业收入", "金额(万元)"] == revenue
    assert statement.loc["营业成本", "金额(万元)"] == cost
    assert statement.loc["毛利", "金额(万元)"] == revenue - cost
    assert statement.loc["销售费用", "金额(万元)"] == _amount(tb, "6601")
    assert statement.loc["管理费用", "金额(万元)"] == _amount(tb, "6602")
    assert statement.loc["财务费用", "金额(万元)"] == _amount(tb, "6603")
    assert statement.loc["营业利润", "金额(万元)"] == revenue - cost - expenses


def test_balance_sheet_matches_tb_recalculation(data):
    tb, coa = data
    statement = build_balance_sheet(tb, coa, "2026-10").set_index("报表项目")
    current = tb[tb["期间"] == "2026-10"].merge(coa, on="科目代码")
    assets = current[current["类别"] == "资产"]
    liabilities = current[current["类别"] == "负债"]
    equities = current[current["类别"] == "权益"]
    profit = 15000 - 10950 - 1150 - 1400 - 280

    assert statement.loc["货币资金", "金额(万元)"] == _amount(tb, "1001") + _amount(tb, "1002")
    assert statement.loc["减:累计折旧", "金额(万元)"] == -_amount(tb, "1602")
    assert statement.loc["资产合计", "金额(万元)"] == float(assets[BALANCE_COLUMN].sum())
    assert statement.loc["负债合计", "金额(万元)"] == -float(liabilities[BALANCE_COLUMN].sum())
    assert statement.loc["本年利润", "金额(万元)"] == profit
    assert statement.loc["权益合计", "金额(万元)"] == -float(equities[BALANCE_COLUMN].sum()) + profit
    assert statement.loc["资产合计", "金额(万元)"] == statement.loc["负债和权益合计", "金额(万元)"]


def test_every_statement_row_has_traceable_source_codes(data):
    tb, coa = data
    statements = [build_balance_sheet(tb, coa, "2026-10"), build_income_statement(tb, coa, "2026-10")]
    known_codes = set(tb["科目代码"])

    for statement in statements:
        assert statement["组成科目"].str.len().gt(0).all()
        for sources in statement["组成科目"]:
            assert set(sources.split(",")) <= known_codes


def test_unbalanced_tb_raises_instead_of_silent_output(data):
    tb, coa = data
    mask = (tb["期间"] == "2026-10") & (tb["科目代码"] == "1002")
    tb.loc[mask, BALANCE_COLUMN] += 100

    with pytest.raises(ValueError, match="资产不等于负债及权益"):
        build_balance_sheet(tb, coa, "2026-10")


def test_csv_and_markdown_outputs(data, tmp_path: Path):
    tb, coa = data
    statement = build_income_statement(tb, coa, "2026-10")
    csv_path, md_path = write_statement_files(statement, tmp_path, "is", "利润表简表", "2026-10")
    loaded = pd.read_csv(csv_path)

    assert list(loaded.columns) == ["报表项目", "金额(万元)", "组成科目"]
    pd.testing.assert_frame_equal(loaded, statement)
    markdown = md_path.read_text(encoding="utf-8")
    assert "利润表简表" in markdown
    assert "营业利润 | 1220 | 6001,6401,6601,6602,6603" in markdown
