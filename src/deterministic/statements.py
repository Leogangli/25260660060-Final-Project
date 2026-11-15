"""S3 报表生成器：纯确定性代码（TODO：由 Kimi Code 按本文件契约实现）。

契约（对应 prompts/statement-builder-agent.md）：
- 输入：标准化 TB（QC 已通过）、科目映射表（含「报表映射」列）
- 输出：bs.csv / is.csv / cf.csv（+ 同名 .md），每个报表行带组成科目代码清单（溯源）
- 不变量：资产合计 == 负债合计 + 权益合计（含本年利润）；不等则 raise，不得静默通过
- 验收：tests/test_golden.py 用样例数据重算，报表数字与 TB 一致率 100%
"""
from __future__ import annotations

import pandas as pd


def build_balance_sheet(tb: pd.DataFrame, coa: pd.DataFrame, period: str) -> pd.DataFrame:
    """返回资产负债表简表 DataFrame：列 = [报表项目, 金额(万元), 组成科目]。"""
    raise NotImplementedError("TODO(Kimi Code): 按 prompts/statement-builder-agent.md 实现")


def build_income_statement(tb: pd.DataFrame, coa: pd.DataFrame, period: str) -> pd.DataFrame:
    """返回利润表简表（本年累计口径）：列 = [报表项目, 金额(万元), 组成科目]。"""
    raise NotImplementedError("TODO(Kimi Code)")


def build_cash_flow_simple(tb: pd.DataFrame, coa: pd.DataFrame, period: str, prev_period: str) -> pd.DataFrame:
    """由两期 BS 差额推导现金流量近似简表，须注明为近似口径。"""
    raise NotImplementedError("TODO(Kimi Code)")
