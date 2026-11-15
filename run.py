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

from src.analysis import ANALYSIS_RESPONSES, attribute_anomalies, write_analysis_outputs
from src.core.orchestrator import Escalation, Orchestrator
from src.deterministic.ingest import ingest_trial_balance, load_period_data
from src.deterministic.metrics import compute_metrics, detect_anomalies
from src.deterministic.qc_checks import run_qc, write_qc_report
from src.deterministic.statements import (
    build_balance_sheet,
    build_income_statement,
    write_statement_files,
)
from src.llm.client import get_client

TB_PATH = Path("data/sample/trial_balance_2026Q3.csv")
COA_PATH = Path("data/sample/chart_of_accounts.csv")
CONFIG_PATH = Path("config/workflow.yaml")
ANALYSIS_PROMPT_PATH = Path("prompts/analysis-agent.md")


def run_workflow(
    period: str,
    resume: bool = False,
    run_root: str | Path = "runs",
    tb_path: str | Path = TB_PATH,
) -> Orchestrator:
    """执行已接线的 S0/S1/S3/S4。"""
    tb_path = Path(tb_path)
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    orchestrator = Orchestrator(period, run_root=str(run_root), resume=resume)
    normalized_path = orchestrator.run_dir / "tb_normalized.csv"
    qc_report_path = orchestrator.run_dir / "qc_report.md"

    orchestrator.run_stage(
        "S0_ingest",
        lambda: [ingest_trial_balance(tb_path, orchestrator.run_dir, period)],
        [str(tb_path)],
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

    orchestrator.run_stage("S1_qc", qc_stage, [str(normalized_path), str(COA_PATH)])

    def statement_stage():
        tb = pd.read_csv(normalized_path, dtype={"科目代码": str})
        coa = pd.read_csv(COA_PATH, dtype={"科目代码": str})
        balance_sheet = build_balance_sheet(tb, coa, period)
        income_statement = build_income_statement(tb, coa, period)
        return write_statement_files(
            balance_sheet, orchestrator.run_dir, "bs", "资产负债表简表", period
        ) + write_statement_files(
            income_statement, orchestrator.run_dir, "is", "利润表简表", period
        )

    orchestrator.run_stage(
        "S3_statements", statement_stage, [str(normalized_path), str(COA_PATH)]
    )

    def analysis_stage():
        tb = pd.read_csv(normalized_path, dtype={"科目代码": str})
        year, month = int(period[:4]), int(period[5:7])
        previous = f"{year}-{month - 1:02d}" if month > 1 else f"{year - 1}-12"
        metrics = compute_metrics(tb, period, previous)
        anomalies = detect_anomalies(metrics, cfg["thresholds"])
        client = get_client(ANALYSIS_RESPONSES)
        attributed = attribute_anomalies(
            anomalies, client, ANALYSIS_PROMPT_PATH.read_text(encoding="utf-8")
        )
        artifacts = write_analysis_outputs(metrics, attributed, orchestrator.run_dir, period)
        print(f"\n期间 {period} 检出异常 {len(attributed)} 条：")
        for anomaly in attributed:
            print(
                f"  - {anomaly['id']} {anomaly['指标']}: 本期值={anomaly['本期值']:.4f} "
                f"阈值={anomaly['阈值']} 偏差={anomaly['偏差']}"
            )
        return artifacts

    orchestrator.run_stage(
        "S4_analysis",
        analysis_stage,
        [
            str(normalized_path), str(orchestrator.run_dir / "bs.csv"),
            str(orchestrator.run_dir / "is.csv"), str(CONFIG_PATH), str(ANALYSIS_PROMPT_PATH),
        ],
    )

    # TODO(Kimi Code): 按 docs/03 将 S2/S5/S6/S7/S8 接入 Orchestrator 阶段 DAG
    return orchestrator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True, help="目标期间，如 2026-10")
    parser.add_argument("--resume", action="store_true", help="从检查点恢复")
    parser.add_argument("--tb-path", default=str(TB_PATH), help="TB CSV 路径，默认使用合成样例")
    args = parser.parse_args()
    try:
        run_workflow(args.period, resume=args.resume, tb_path=args.tb_path)
    except Escalation as exc:
        parser.exit(1, f"[escalated] {exc}；详见 runs/{args.period}/escalation.md\n")


if __name__ == "__main__":
    main()
