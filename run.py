#!/usr/bin/env python3
"""FinClose 月度关账工作流入口。

用法：
    python3 run.py --period 2026-10            # 新运行
    python3 run.py --period 2026-10 --resume   # 从检查点恢复（已完成且输入未变的阶段不重复执行）
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import yaml

from src.analysis import ANALYSIS_RESPONSES, attribute_anomalies, write_analysis_outputs
from src.archive import archive_run
from src.closecheck import CLOSECHECK_RESPONSES, write_closecheck_report
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
from src.reporting import write_report_candidate
from src.verification import write_verification_report

TB_PATH = Path("data/sample/trial_balance_2026Q3.csv")
COA_PATH = Path("data/sample/chart_of_accounts.csv")
CONFIG_PATH = Path("config/workflow.yaml")
ANALYSIS_PROMPT_PATH = Path("prompts/analysis-agent.md")
CLOSECHECK_PROMPT_PATH = Path("prompts/closecheck-agent.md")
REPORT_PROMPT_PATH = Path("prompts/report-writer-agent.md")
VERIFIER_PROMPT_PATH = Path("prompts/verifier-critic-agent.md")


def _previous_period(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return f"{year}-{month - 1:02d}" if month > 1 else f"{year - 1}-12"


def run_workflow(
    period: str,
    resume: bool = False,
    run_root: str | Path = "runs",
    tb_path: str | Path = TB_PATH,
    approval_handler=None,
) -> Orchestrator:
    """执行 S0 至 S8；未获人工批准时在 S7 挂起且不归档。"""
    tb_path = Path(tb_path)
    previous = _previous_period(period)
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    orchestrator = Orchestrator(period, run_root=str(run_root), resume=resume)
    normalized_path = orchestrator.run_dir / "tb_normalized.csv"
    qc_report_path = orchestrator.run_dir / "qc_report.md"
    closecheck_path = orchestrator.run_dir / "closecheck_report.md"

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

    def closecheck_stage():
        tb = pd.read_csv(normalized_path, dtype={"科目代码": str})
        client = get_client(CLOSECHECK_RESPONSES)
        return [write_closecheck_report(
            tb, period, previous, client,
            CLOSECHECK_PROMPT_PATH.read_text(encoding="utf-8"), closecheck_path,
        )]

    orchestrator.run_stage(
        "S2_closecheck", closecheck_stage,
        [str(normalized_path), str(qc_report_path), str(CLOSECHECK_PROMPT_PATH)],
    )

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

    def write_stage(round_number: int, repair_package: list[dict]):
        tb = pd.read_csv(normalized_path, dtype={"科目代码": str})
        metrics = compute_metrics(tb, period, previous)
        anomalies = json.loads((orchestrator.run_dir / "anomalies.json").read_text(encoding="utf-8"))
        return write_report_candidate(
            orchestrator.run_dir, round_number, period, metrics, anomalies,
            closecheck_path.read_text(encoding="utf-8"), repair_package,
            get_client(), REPORT_PROMPT_PATH.read_text(encoding="utf-8"),
        )

    def verify_stage(round_number: int, report_path: str | Path):
        tb = pd.read_csv(normalized_path, dtype={"科目代码": str})
        metrics = compute_metrics(tb, period, previous)
        anomalies = json.loads((orchestrator.run_dir / "anomalies.json").read_text(encoding="utf-8"))
        client = get_client({"VERIFY_SUMMARY": "已独立重算并检查引用、覆盖和期间边界。"})
        return write_verification_report(
            report_path, round_number, tb, metrics, anomalies, cfg["thresholds"],
            client, VERIFIER_PROMPT_PATH.read_text(encoding="utf-8"),
        )

    orchestrator.revision_loop(
        write_stage,
        verify_stage,
        [
            str(normalized_path), str(closecheck_path), str(orchestrator.run_dir / "bs.csv"),
            str(orchestrator.run_dir / "is.csv"), str(orchestrator.run_dir / "metrics.csv"),
            str(orchestrator.run_dir / "anomalies.json"), str(CONFIG_PATH),
            str(REPORT_PROMPT_PATH), str(VERIFIER_PROMPT_PATH),
        ],
    )

    report_path = Path(orchestrator.state.stages["S5_report"].artifacts[-1])
    verify_path = Path(orchestrator.state.stages["S6_verify"].artifacts[-1])
    approved = orchestrator.human_approval_gate(
        [str(report_path), str(verify_path)], confirm_fn=approval_handler
    )
    if not approved:
        return orchestrator

    orchestrator.run_stage(
        "S8_archive",
        lambda: archive_run(orchestrator.run_dir, period, CONFIG_PATH),
        [
            str(orchestrator.run_dir / "approval.json"), str(report_path), str(verify_path),
            str(orchestrator.run_dir / "metrics.csv"), str(CONFIG_PATH),
        ],
    )
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
