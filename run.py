#!/usr/bin/env python3
"""FinClose 月度关账工作流入口。

用法：
    python3 run.py --period 2026-10            # 新运行
    python3 run.py --period 2026-10 --resume   # 从检查点恢复（已完成且输入未变的阶段不重复执行）
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from src.core.orchestrator import Orchestrator
from src.deterministic.ingest import ingest_trial_balance, load_period_data
from src.deterministic.metrics import compute_metrics, detect_anomalies
from src.deterministic.qc_checks import run_qc, write_qc_report

TB_PATH = Path("data/sample/trial_balance_2026Q3.csv")
COA_PATH = Path("data/sample/chart_of_accounts.csv")
CONFIG_PATH = Path("config/workflow.yaml")


def run_workflow(period: str, resume: bool = False, run_root: str | Path = "runs") -> Orchestrator:
    """执行已接线的 S0/S1，并保留既有 S4 确定性演示输出。"""
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    orchestrator = Orchestrator(period, run_root=str(run_root), resume=resume)
    normalized_path = orchestrator.run_dir / "tb_normalized.csv"
    qc_report_path = orchestrator.run_dir / "qc_report.md"

    orchestrator.run_stage(
        "S0_ingest",
        lambda: [ingest_trial_balance(TB_PATH, orchestrator.run_dir, period)],
        [str(TB_PATH)],
    )

    def qc_stage():
        tb = pd.read_csv(normalized_path, dtype={"科目代码": str})
        coa = pd.read_csv(COA_PATH, dtype={"科目代码": str})
        result = run_qc(tb, coa, period=period, period_end=period)
        write_qc_report(result, qc_report_path)
        for finding in result.findings:
            print(f"[{finding['result']}] {finding['check']}: {finding['evidence']}")
        if not result.passed:
            raise ValueError("QC 硬门禁失败，停止进入下游")
        return [qc_report_path]

    orchestrator.run_stage(
        "S1_qc", qc_stage, [str(normalized_path), str(COA_PATH)]
    )

    tb = pd.read_csv(normalized_path, dtype={"科目代码": str})
    year, month = int(period[:4]), int(period[5:7])
    previous = f"{year}-{month - 1:02d}" if month > 1 else f"{year - 1}-12"
    metrics = compute_metrics(tb, period, previous)
    anomalies = detect_anomalies(metrics, cfg["thresholds"])
    print(f"\n期间 {period} 检出异常 {len(anomalies)} 条：")
    for anomaly in anomalies:
        print(
            f"  - {anomaly['id']} {anomaly['指标']}: 本期值={anomaly['本期值']:.4f} "
            f"阈值={anomaly['阈值']} 证据={anomaly['证据']}"
        )

    # TODO(Kimi Code): 按 docs/03 将 S2/S3/S5/S6/S7/S8 接入 Orchestrator 阶段 DAG
    return orchestrator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True, help="目标期间，如 2026-10")
    parser.add_argument("--resume", action="store_true", help="从检查点恢复")
    args = parser.parse_args()
    run_workflow(args.period, resume=args.resume)


if __name__ == "__main__":
    main()
