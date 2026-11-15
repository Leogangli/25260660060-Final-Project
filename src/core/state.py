"""运行状态台账与检查点：原子写盘、输入哈希、恢复判定。"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


def sha256_of(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_of_files(paths: list[str]) -> str:
    """按给定顺序计算输入文件集合的 SHA-256；单文件直接使用其内容哈希。"""
    if len(paths) == 1:
        return sha256_of(paths[0])
    h = hashlib.sha256()
    for path in paths:
        h.update(sha256_of(path).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


@dataclass
class StageState:
    status: str = "pending"  # pending/running/done/failed/skipped
    input_hash: str | None = None
    artifacts: list[str] = field(default_factory=list)
    finished_at: float | None = None


@dataclass
class RunState:
    period: str
    run_id: str
    stages: dict[str, StageState] = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)

    def mark_done(self, stage: str, input_hash: str, artifacts: list[str]):
        self.stages[stage] = StageState("done", input_hash, artifacts, time.time())

    def is_fresh(self, stage: str, input_hash: str) -> bool:
        s = self.stages.get(stage)
        return bool(s and s.status in {"done", "skipped"} and s.input_hash == input_hash)

    def invalidate_from(self, stage_ids: list[str], start: str):
        """输入变化时：将 start 及其下游全部置回 pending。"""
        idx = stage_ids.index(start)
        for sid in stage_ids[idx:]:
            self.stages[sid] = StageState()

    def log_decision(self, decision: str, reason: str):
        self.decisions.append({"ts": time.time(), "decision": decision, "reason": reason})


class StateStore:
    """runs/<period>/state.json 的原子读写（先写临时文件再 rename）。"""

    def __init__(self, run_dir: str | Path):
        self.path = Path(run_dir) / "state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> RunState | None:
        if not self.path.exists():
            return None
        d = json.loads(self.path.read_text(encoding="utf-8"))
        st = RunState(period=d["period"], run_id=d["run_id"], decisions=d.get("decisions", []))
        for k, v in d.get("stages", {}).items():
            st.stages[k] = StageState(**v)
        return st

    def save(self, state: RunState):
        tmp = self.path.with_suffix(".tmp")
        payload = {
            "period": state.period, "run_id": state.run_id,
            "stages": {k: vars(v) for k, v in state.stages.items()},
            "decisions": state.decisions,
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # 原子替换，中断不留半写状态
