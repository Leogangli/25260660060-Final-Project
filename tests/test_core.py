"""P1：检查点、输入哈希、阶段恢复与失败升级测试。"""
from pathlib import Path

import pytest

from src.core.orchestrator import Escalation, Orchestrator
from src.core.state import RunState, StateStore, sha256_of, sha256_of_files


def test_state_store_round_trip_and_atomic_replace(tmp_path: Path):
    source = tmp_path / "data" / "tb.csv"
    source.parent.mkdir()
    source.write_text("期间,余额\n2026-10,100\n", encoding="utf-8")
    store = StateStore(tmp_path / "runs" / "2026-10")
    state = RunState(period="2026-10", run_id="测试运行")
    state.mark_done("S0_ingest", sha256_of_files([str(source)]), ["tb_normalized.csv"])
    state.log_decision("执行", "输入尚未处理")

    store.save(state)
    loaded = store.load()

    assert loaded is not None
    assert loaded.period == "2026-10"
    assert loaded.run_id == "测试运行"
    assert loaded.stages["S0_ingest"].input_hash == sha256_of(source)
    assert loaded.stages["S0_ingest"].artifacts == ["tb_normalized.csv"]
    assert loaded.decisions[0]["decision"] == "执行"
    assert not store.path.with_suffix(".tmp").exists()


def test_resume_skips_done_and_previously_skipped_stage(tmp_path: Path):
    source = tmp_path / "data.csv"
    source.write_text("原始输入", encoding="utf-8")
    calls = []

    def stage():
        calls.append("执行")
        return ["normalized.csv"]

    first = Orchestrator("2026-10", run_root=str(tmp_path / "runs"))
    assert first.run_stage("S0_ingest", stage, [str(source)]) is True

    resumed = Orchestrator("2026-10", run_root=str(tmp_path / "runs"), resume=True)
    assert resumed.run_stage("S0_ingest", stage, [str(source)]) is False
    assert resumed.state.stages["S0_ingest"].status == "skipped"

    resumed_again = Orchestrator("2026-10", run_root=str(tmp_path / "runs"), resume=True)
    assert resumed_again.run_stage("S0_ingest", stage, [str(source)]) is False
    assert calls == ["执行"]


def test_changed_input_invalidates_stage_and_downstream(tmp_path: Path):
    source = tmp_path / "data.csv"
    source.write_text("第一版", encoding="utf-8")
    calls = []
    run_root = str(tmp_path / "runs")

    first = Orchestrator("2026-10", run_root=run_root)
    first.run_stage("S0_ingest", lambda: calls.append("S0"), [str(source)])
    first.run_stage("S1_qc", lambda: calls.append("S1"), [str(source)])

    source.write_text("修正版", encoding="utf-8")
    resumed = Orchestrator("2026-10", run_root=run_root, resume=True)
    resumed.run_stage("S0_ingest", lambda: calls.append("S0"), [str(source)])

    assert resumed.state.stages["S1_qc"].status == "pending"
    resumed.run_stage("S1_qc", lambda: calls.append("S1"), [str(source)])
    assert calls == ["S0", "S1", "S0", "S1"]


def test_interrupted_stage_invalidates_downstream(tmp_path: Path):
    source = tmp_path / "data.csv"
    source.write_text("输入", encoding="utf-8")
    run_root = str(tmp_path / "runs")
    first = Orchestrator("2026-10", run_root=run_root)
    first.run_stage("S0_ingest", lambda: None, [str(source)])
    first.run_stage("S1_qc", lambda: None, [str(source)])
    first.state.stages["S0_ingest"].status = "running"
    first.store.save(first.state)

    resumed = Orchestrator("2026-10", run_root=run_root, resume=True)
    resumed.run_stage("S0_ingest", lambda: None, [str(source)])

    assert resumed.state.stages["S1_qc"].status == "pending"


def test_retry_and_hard_gate_escalation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "data.csv"
    source.write_text("输入", encoding="utf-8")
    sleeps = []
    monkeypatch.setattr("src.core.orchestrator.time.sleep", sleeps.append)
    attempts = []

    def flaky():
        attempts.append("尝试")
        if len(attempts) == 1:
            raise RuntimeError("临时故障")
        return ["ok.csv"]

    orchestrator = Orchestrator("2026-10", run_root=str(tmp_path / "runs"))
    assert orchestrator.run_stage("S0_ingest", flaky, [str(source)]) is True
    assert attempts == ["尝试", "尝试"]
    assert sleeps == [1]

    hard_gate = Orchestrator("2026-11", run_root=str(tmp_path / "runs"))

    def fail_qc():
        raise ValueError("借贷不平衡")

    with pytest.raises(Escalation, match="S1_qc"):
        hard_gate.run_stage("S1_qc", fail_qc, [str(source)])
    assert hard_gate.state.stages["S1_qc"].status == "failed"
    assert (hard_gate.run_dir / "escalation.md").exists()
