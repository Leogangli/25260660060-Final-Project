"""Orchestrator：阶段 DAG 调度、检查点恢复、重试与升级（骨架）。

角色边界：只做分派、状态与路由，不做业务判断，不拥有人工批准权。
对应 prompts/orchestrator.md 与 docs/02-架构设计.md 第 2.3 节。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
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
        self.state = prior or RunState(period=period, run_id=f"{period}-{time.time_ns()}")

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

    def revision_loop(self, write_fn, verify_fn, input_files: list[str] | None = None) -> bool:
        """初稿加最多两轮修订；核验问题作为修复任务包路由回 Writer。"""
        input_hash = sha256_of_files(input_files or [])
        fresh = self.state.is_fresh("S5_report", input_hash) and self.state.is_fresh("S6_verify", input_hash)
        if fresh:
            for stage_id in ["S5_report", "S6_verify"]:
                self.state.stages[stage_id].status = "skipped"
                print(f"[skipped] {stage_id}: 输入哈希未变")
            self.state.log_decision("跳过", "S5/S6 已完成且输入哈希未变")
            self.store.save(self.state)
            return False

        if any(stage in self.state.stages for stage in ["S5_report", "S6_verify"]):
            self.state.invalidate_from(STAGE_ORDER, "S5_report")
            self.state.log_decision("失效", "S5/S6 未完成或输入哈希变化，修订循环重新执行")
            self.store.save(self.state)

        reports = []
        verifications = []
        repair_package = []
        for round_number in range(1, MAX_REVISION_ROUNDS + 2):
            self.state.stages["S5_report"] = StageState(status="running", input_hash=input_hash)
            self.store.save(self.state)
            report_path = write_fn(round_number, repair_package)
            reports.append(str(report_path))
            self.state.mark_done("S5_report", input_hash, reports)
            self.store.save(self.state)
            print(f"[done] S5_report: report_v{round_number}.md")

            self.state.stages["S6_verify"] = StageState(status="running", input_hash=input_hash)
            self.store.save(self.state)
            result = verify_fn(round_number, report_path)
            verifications.extend(result.get("artifacts", []))
            passed = bool(result["passed"])
            if passed:
                self.state.mark_done("S6_verify", input_hash, verifications)
                self.state.log_decision("通过", f"S6 第 {round_number} 轮核验 PASS")
                self.store.save(self.state)
                print("[done] S6_verify: PASS")
                return True

            self.state.stages["S6_verify"] = StageState(
                status="failed", input_hash=input_hash, artifacts=verifications
            )
            repair_package = result.get("issues", [])
            self.state.log_decision("修订", f"S6 第 {round_number} 轮核验 REJECT，问题 {len(repair_package)} 项")
            self.store.save(self.state)
            if round_number > MAX_REVISION_ROUNDS:
                self.escalate(
                    "S5/S6 修订 2 轮仍不收敛",
                    {"轮次": round_number, "问题": repair_package},
                )
            print(f"[retry] S5_report: 根据核验问题执行第 {round_number}/{MAX_REVISION_ROUNDS} 轮修订")

        raise AssertionError("修订循环不可达")

    def human_approval_gate(
        self, input_files: list[str] | None = None, confirm_fn=None
    ) -> bool:
        """仅本 run 的有效批准可放行；无人输入或驳回均挂起且不归档。"""
        input_hash = sha256_of_files(input_files or [])
        if self.state.is_fresh("S7_approval", input_hash):
            self.state.stages["S7_approval"].status = "skipped"
            self.state.log_decision("跳过", "人工批准记录仍有效")
            self.store.save(self.state)
            print("[skipped] S7_approval: 人工批准记录仍有效")
            return True

        approval_path = self.run_dir / "approval.json"
        record = None
        if approval_path.exists():
            candidate = json.loads(approval_path.read_text(encoding="utf-8"))
            if candidate.get("run_id") == self.state.run_id and candidate.get("approved"):
                if candidate.get("输入哈希") != input_hash:
                    self.state.stages["S7_approval"] = StageState(status="failed", input_hash=input_hash)
                    self.store.save(self.state)
                    self.escalate("批准记录与当前终稿哈希不一致", {"批准文件": str(approval_path)})
                record = candidate

        if record is None:
            if confirm_fn is None:
                def confirm_fn():
                    try:
                        approver = input("请输入 CFO 批准人：").strip()
                        answer = input("是否批准归档？[y/N]：").strip().lower()
                        comment = input("批准意见（可留空）：").strip()
                    except EOFError:
                        return None
                    return {"approved": answer == "y", "批准人": approver, "意见": comment}

            decision = confirm_fn()
            if decision is None:
                self.state.stages["S7_approval"] = StageState(status="pending", input_hash=input_hash)
                self.state.log_decision("挂起", "等待人工批准信号 approval.json")
                self.store.save(self.state)
                print(f"[pending] S7_approval: 等待人工批准，未执行归档；批准文件={approval_path}")
                return False
            record = {
                **decision,
                "period": self.period,
                "run_id": self.state.run_id,
                "输入哈希": input_hash,
                "批准时间": datetime.now(timezone.utc).isoformat(),
            }
            approval_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if not record.get("approved") or not str(record.get("批准人", "")).strip():
            self.state.stages["S7_approval"] = StageState(status="pending", input_hash=input_hash)
            self.state.log_decision("驳回", "CFO 未批准或批准人为空，等待返回 S5 修订")
            self.store.save(self.state)
            print("[pending] S7_approval: 已驳回，等待返回 S5 修订；未执行归档")
            return False

        self.state.mark_done("S7_approval", input_hash, [str(approval_path)])
        self.state.log_decision("批准", f"CFO {record['批准人']} 批准归档")
        self.store.save(self.state)
        print(f"[done] S7_approval: {record['批准人']} 已批准")
        return True

    def escalate(self, reason: str, context: dict):
        (self.run_dir / "escalation.md").write_text(
            f"# 升级上报\n\n- 期间：{self.period}\n- 原因：{reason}\n- 上下文：{context}\n",
            encoding="utf-8",
        )
        raise Escalation(reason)
