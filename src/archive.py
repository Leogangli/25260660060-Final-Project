"""S8 归档：不可覆盖的版本目录、环比基线与下期触发记录。"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from .core.state import sha256_of


def _next_period(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return f"{year + 1}-01" if month == 12 else f"{year}-{month + 1:02d}"


def _code_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def archive_run(run_dir: str | Path, period: str, config_path: str | Path) -> list[Path]:
    """归档本期核心产物；已有版本永不覆盖。"""
    run_dir = Path(run_dir)
    config_path = Path(config_path)
    archive_root = run_dir / "archive"
    versions = [int(path.name[1:]) for path in archive_root.glob("v*") if path.is_dir() and path.name[1:].isdigit()]
    version = max(versions, default=0) + 1
    archive_dir = archive_root / f"v{version}"
    archive_dir.mkdir(parents=True, exist_ok=False)

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report_path = Path(state["stages"]["S5_report"]["artifacts"][-1])
    verify_path = Path(state["stages"]["S6_verify"]["artifacts"][-1])
    if not report_path.exists():
        raise ValueError("归档失败：缺少经核验的报告")
    approval = json.loads((run_dir / "approval.json").read_text(encoding="utf-8"))
    if not approval.get("approved"):
        raise ValueError("归档失败：人工批准记录未通过")
    if "总体结论：PASS" not in verify_path.read_text(encoding="utf-8"):
        raise ValueError("归档失败：最终核验未通过")
    core_names = [
        "tb_normalized.csv", "qc_report.md", "closecheck_report.md", "bs.csv", "bs.md",
        "is.csv", "is.md", "metrics.csv", "metrics.md", "anomalies.json", "anomalies.md", "approval.json",
    ]
    sources = [run_dir / name for name in core_names] + [verify_path, report_path]
    copied = []
    for source in sources:
        if not source.exists():
            raise ValueError(f"归档失败：缺少产物 {source}")
        target = archive_dir / source.name
        shutil.copy2(source, target)
        copied.append(target)
    shutil.copy2(config_path, archive_dir / "workflow.yaml")

    now = datetime.now(timezone.utc).isoformat()
    manifest = archive_dir / "manifest.json"
    _atomic_json(manifest, {
        "period": period, "version": version, "archived_at": now,
        "code_commit": _code_commit(), "workflow_sha256": sha256_of(config_path),
        "final_report": report_path.name,
        "artifacts": [path.name for path in copied] + ["workflow.yaml"],
    })
    baseline = run_dir / "baseline.json"
    metrics_records = json.loads(pd.read_csv(run_dir / "metrics.csv").to_json(orient="records", force_ascii=False))
    _atomic_json(baseline, {
        "period": period, "source_archive": str(archive_dir), "updated_at": now,
        "metrics": metrics_records,
    })
    cron = yaml.safe_load(config_path.read_text(encoding="utf-8"))["schedule"]["cron"]
    next_trigger = run_dir / "next_trigger.json"
    _atomic_json(next_trigger, {"period": _next_period(period), "cron": cron, "created_at": now})
    return [manifest, baseline, next_trigger]
