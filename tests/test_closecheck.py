"""S2 关账检查规则、证据与 MockLLM 报告测试。"""
from pathlib import Path

import pandas as pd

from src.closecheck import (
    CLOSECHECK_RESPONSES,
    build_closecheck_findings,
    write_closecheck_report,
)
from src.llm.client import MockLLM

TB_PATH = Path("data/sample/trial_balance_2026Q3.csv")


def test_closecheck_rules_and_evidence():
    tb = pd.read_csv(TB_PATH, dtype={"科目代码": str})
    findings = {item["id"]: item for item in build_closecheck_findings(tb, "2026-10", "2026-09")}

    assert findings["CC_DEP"]["状态"] == "已确认"
    assert findings["CC_DEP"]["证据"]["变动"] == 50
    assert findings["CC_ACCRUAL"]["状态"] == "已确认"
    assert findings["CC_RECEIVABLE"]["状态"] == "提示关注"
    assert findings["CC_RECEIVABLE"]["证据"]["应收账款"]["环比"] > 0.4
    assert findings["CC_PL"]["状态"] == "已确认"
    assert findings["CC_NEW"]["状态"] == "需人工确认"
    assert findings["CC_NEW"]["证据"]["新增或新增发生额科目"][0]["科目代码"] == "2001"


def test_closecheck_report_contains_mock_explanations(tmp_path: Path):
    tb = pd.read_csv(TB_PATH, dtype={"科目代码": str})
    output = write_closecheck_report(
        tb, "2026-10", "2026-09", MockLLM(CLOSECHECK_RESPONSES),
        "关账检查提示词", tmp_path / "closecheck_report.md",
    )
    report = output.read_text(encoding="utf-8")

    assert "【提示关注】" in report
    assert "【需人工确认】" in report
    assert "TB 数值证据" in report
    assert "新增或新增发生额科目需由会计确认业务背景和入账依据" in report
