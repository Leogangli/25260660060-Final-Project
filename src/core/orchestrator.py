"""Orchestrator：阶段 DAG 调度、检查点恢复、重试与升级（骨架）。

角色边界：只做分派、状态与路由，不做业务判断，不拥有人工批准权。
对应 prompts/orchestrator.md 与 docs/02-架构设计.md 第 2.3 节。
"""
from __future__ import annotations

import time
from pathlib import Path

from .state import RunState, StateStore, sha256_of

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
    def run_stage(self, stage_id: str, fn, input_files: list[str]):
        """带检查点的阶段执行包装器：
        - 输入哈希未变且已完成 → 跳过（中断恢复的关键）
        - 失败 → 指数退避重试；硬门禁失败或重试耗尽 → 升级
        TODO(Kimi Code): 实现函数体（骨架见 docs/03 P1）。
        """
        raise NotImplementedError("TODO(Kimi Code): 见 docs/03-KIMI-CODE-实施简报.md P1")

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
