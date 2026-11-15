#!/usr/bin/env python3
"""FinClose 月度关账工作流入口。

用法：
    python3 run.py --period 2026-10            # 新运行
    python3 run.py --period 2026-10 --resume   # 从检查点恢复（已完成且输入未变的阶段不重复执行）
"""
from __future__ import annotations

import argparse

import pandas as pd
import yaml

from src.deterministic.metrics import compute_metrics, detect_anomalies
from src.deterministic.qc_checks import run_qc


def load_period_data(tb_path: str, period_end: str) -> pd.DataFrame:
    """数据访问层：只返回 <= period_end 的行（防未来信息泄露）。"""
    tb = pd.read_csv(tb_path, dtype={"科目代码": str})
    return tb[tb["期间"] <= period_end].copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, help="目标期间，如 2026-10")
    ap.add_argument("--resume", action="store_true", help="从检查点恢复")
    args = ap.parse_args()

    cfg = yaml.safe_load(open("config/workflow.yaml", encoding="utf-8"))
    tb = load_period_data("data/sample/trial_balance_2026Q3.csv", period_end=args.period)
    coa = pd.read_csv("data/sample/chart_of_accounts.csv", dtype={"科目代码": str})

    # ---- S1 QC（已实现，可直接演示）----
    qc = run_qc(tb, coa, period=args.period, period_end=args.period)
    for f in qc.findings:
        print(f"[{f['result']}] {f['check']}: {f['evidence']}")
    if not qc.passed:
        raise SystemExit("QC 硬门禁失败：停止运行并升级（见 runs/<period>/escalation.md）")

    # ---- S4 指标与异常（已实现，可直接演示）----
    y, m = int(args.period[:4]), int(args.period[5:7])
    prev = f"{y}-{m-1:02d}" if m > 1 else f"{y-1}-12"
    metrics = compute_metrics(tb, args.period, prev)
    anomalies = detect_anomalies(metrics, cfg["thresholds"])
    print(f"\n期间 {args.period} 检出异常 {len(anomalies)} 条：")
    for a in anomalies:
        print(f"  - {a['id']} {a['指标']}: 本期值={a['本期值']:.4f} 阈值={a['阈值']} 证据={a['证据']}")

    # TODO(Kimi Code): 按 docs/03 将 S0/S2/S3/S5/S6/S7/S8 接入 Orchestrator 阶段 DAG


if __name__ == "__main__":
    main()
