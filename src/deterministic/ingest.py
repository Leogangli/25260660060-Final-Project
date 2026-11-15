"""S0 摄取：期间锁定、字段标准化与标准化 TB 落盘。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BALANCE_COLUMN = "期末余额(借正贷负,万元)"
TB_COLUMNS = ["期间", "科目代码", "科目名称", BALANCE_COLUMN]


def normalize_trial_balance(tb: pd.DataFrame) -> pd.DataFrame:
    """统一字段顺序与类型；非法金额保留为空值，交由 S1 判定失败。"""
    normalized = tb.copy()
    for column in TB_COLUMNS:
        if column not in normalized:
            normalized[column] = pd.NA
    for column in ["期间", "科目代码", "科目名称"]:
        normalized[column] = normalized[column].astype("string").str.strip()
    normalized[BALANCE_COLUMN] = pd.to_numeric(normalized[BALANCE_COLUMN], errors="coerce")
    return normalized[TB_COLUMNS]


def load_period_data(tb_path: str | Path, period_end: str) -> pd.DataFrame:
    """只放行不晚于 period_end 的有效期间行；非法期间留给 QC 报告。"""
    tb = normalize_trial_balance(pd.read_csv(tb_path, dtype=str))
    periods = tb["期间"]
    valid_format = periods.str.fullmatch(r"\d{4}-\d{2}")
    visible = periods.isna() | ~valid_format | (valid_format & (periods <= period_end))
    return tb[visible].copy()


def ingest_trial_balance(
    tb_path: str | Path, run_dir: str | Path, period_end: str
) -> Path:
    """生成 runs/<period>/tb_normalized.csv，不修改原始 TB。"""
    output = Path(run_dir) / "tb_normalized.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    load_period_data(tb_path, period_end).to_csv(output, index=False, encoding="utf-8-sig")
    return output
