"""Orchestrator：阶段 DAG 调度、检查点恢复、重试与升级（骨架）。

角色边界：只做分派、状态与路由，不做业务判断，不拥有人工批准权。
对应 prompts/orchestrator.md 与 docs/02-架构设计.md 第 2.3 节。
"""
from __future__ import annotations

import time
from pathlib import Path

from .state import RunState, StageState, StateStore, sha256_of_files

STAGE_ORDER = [
    "S0_ingest", "S1_qc", "S2_closecheck", "S3_statements",
    "S4_analysis", "S5_report", "S6_verify", "S7_approval", "S8_archive",
]
HARD_GATES = {"S1_qc"}           # 失败即停止+升级
MAX_REVISION_ROUNDS = 2          # S5/S6 有界修订循环
BACKOFF_SECONDS = [1, 4, 16]


class Escalation(Exception):
    """达到升级条件：停止运行，写 escalation.md 后由人工处理。"""


class Orchestrator:
    def __init__(self, period: str, run_root: str = "runs", resume: bool = False):
        self.period = period
        self.run_dir = Path(run_root) / period
        self.store = StateStore(self.run_dir)
        prior = self.store.load() if resume else None
        self.state = prior or RunState(period=period, run_id=f"{period}-{int(time.time())}")

    # ---- 核心调度 ----
    def run_stage(self, stage_id: str, fn, input_files: list[str]) -> bool:
        """执行一个 DAG 阶段；返回是否实际执行，恢复跳过时返回 False。

        fn 可返回产物路径列表、单个路径或 None。每次状态变化均立即原子写盘。
        """
        if stage_id not in STAGE_ORDER:
            raise ValueError(f"未知阶段：{stage_id}")

        input_hash = sha256_of_files(input_files)
        previous = self.state.stages.get(stage_id)
        if previous and previous.input_hash is not None and previous.input_hash != input_hash:
            self.state.invalidate_from(STAGE_ORDER, stage_id)
            self.state.log_decision("失效", f"{stage_id} 输入哈希变化，本阶段及下游置为 pending")
            self.store.save(self.state)
        elif previous and previous.status in {"running", "failed"}:
            self.state.invalidate_from(STAGE_ORDER, stage_id)
            self.state.log_decision("失效", f"{stage_id} 上次未完成，本阶段及下游置为 pending")
            self.store.save(self.state)

        if self.state.is_fresh(stage_id, input_hash):
            self.state.stages[stage_id].status = "skipped"
            self.state.log_decision("跳过", f"{stage_id} 已完成且输入哈希未变")
            self.store.save(self.state)
            print(f"[skipped] {stage_id}: 输入哈希未变")
            return False

        for attempt in range(1, 4):
            self.state.stages[stage_id] = StageState(status="running", input_hash=input_hash)
            self.store.save(self.state)
            try:
                result = fn()
                if result is None:
                    artifacts = []
                elif isinstance(result, (str, Path)):
                    artifacts = [str(result)]
                else:
                    artifacts = [str(path) for path in result]
                self.state.mark_done(stage_id, input_hash, artifacts)
                self.state.log_decision("完成", f"{stage_id} 第 {attempt} 次尝试成功")
                self.store.save(self.state)
                print(f"[done] {stage_id}")
                return True
            except Exception as exc:
                self.state.stages[stage_id] = StageState(status="failed", input_hash=input_hash)
                should_escalate = stage_id in HARD_GATES or attempt == 3
                decision = "升级" if should_escalate else "重试"
                self.state.log_decision(decision, f"{stage_id} 第 {attempt} 次尝试失败：{exc}")
                self.store.save(self.state)
                if should_escalate:
                    self.escalate(
                        f"{stage_id} 执行失败",
                        {"阶段": stage_id, "尝试次数": attempt, "错误": str(exc)},
                    )
                delay = BACKOFF_SECONDS[attempt - 1]
                print(f"[retry] {stage_id}: 第 {attempt} 次失败，{delay} 秒后重试")
                time.sleep(delay)

        raise AssertionError("阶段执行循环不可达")

    def revision_loop(self, write_fn, verify_fn):
        """S5/S6 有界修订循环：REJECT → 修复任务包路由回 Writer，≤ MAX_REVISION_ROUNDS。"""
        raise NotImplementedError("TODO(Kimi Code): 见 docs/03 P5")

    def human_approval_gate(self) -> bool:
        """S7：挂起等待人工批准（CLI 确认或 approval.json 落盘）。Orchestrator 无权自行放行。"""
        raise NotImplementedError("TODO(Kimi Code): 见 docs/03 P6")

    def escalate(self, reason: str, context: dict):
        (self.run_dir / "escalation.md").write_text(
            f"# 升级上报\n\n- 期间：{self.period}\n- 原因：{reason}\n- 上下文：{context}\n",
            encoding="utf-8",
        )
        raise Escalation(reason)
