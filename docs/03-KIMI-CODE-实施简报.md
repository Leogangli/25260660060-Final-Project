# 03 给 Kimi Code 的实施简报

> 使用方式：在本仓库根目录打开 Kimi Code，把本文件全文贴入（或让它先读 `docs/01`、`docs/02`、`prompts/`、`config/`，再读本文件），然后**按第 3 节的阶段顺序实施，每个阶段一个 commit**，不要一次性全部写完再提交。

## 1. 目标

实现 docs/01、docs/02 定义的 FinClose 月度关账长程智能体工作流。仓库中已有：任务定义、架构设计、6 份智能体提示词、工作流配置、三个月合成样例数据（2026-08/09/10，10 月预埋 4 个异常）、核心代码骨架（`src/`，含已实现的确定性 QC 与指标计算及部分 TODO）。

## 2. 技术约束

- Python ≥ 3.11；只允许依赖 pandas、pyyaml（LLM SDK 为可选，默认不装）。
- LLM 调用一律走 `src/llm/client.py` 的 `LLMClient` 接口；默认实现 `MockLLM`（确定性、可复现、无需密钥），真实模型通过环境变量接入，**任何密钥不得写入仓库**。
- 所有文件读写限定：只读 `data/`、`archive/`、`config/`、`prompts/`，只写 `runs/<period>/`。
- 全程中文注释与文档；产物文件用中文命名。

## 3. 分阶段实施计划（每阶段一个 commit）

| 阶段 | 内容 | commit message 示例 |
|------|------|---------------------|
| P1 | 完成 `src/core/state.py` 检查点存取（含输入 SHA-256、原子写入）与 `src/core/orchestrator.py` 的阶段 DAG 执行、跳过/失效/恢复逻辑 | `feat(core): 检查点与阶段恢复逻辑` |
| P2 | 跑通 S0–S1：摄取 + QC（QC 代码骨架已有，补全并与 orchestrator 接线）；为 QC 写单测 | `feat(qc): 摄取与数据质检接线及单测` |
| P3 | 实现 S3 报表生成器（`src/deterministic/statements.py` 的 TODO）：按映射表汇总出 BS/IS 简表（CSV+Markdown），数字必须与 TB 重算一致 | `feat(statements): 三表生成器` |
| P4 | 实现 S4：`src/deterministic/metrics.py` 补全指标计算（环比、毛利率、现金变动等）+ 阈值规则引擎（阈值读 config/workflow.yaml）+ MockLLM 归因输出异常清单 | `feat(analysis): 指标引擎与异常检测` |
| P5 | 实现 S5/S6：Report Writer 与 Verifier 接入 prompts/ 下提示词，Verifier 做确定性重算核对 + 引用溯源检查；实现 ≤2 轮修订循环与升级 | `feat(verify): 报告撰写、独立核验与修订循环` |
| P6 | 实现 S7/S8：人工批准门（CLI 确认或 `approval.json` 落盘）、归档、基线更新；补调度说明（crontab 示例写入 README 或 docs） | `feat(archive): 人工批准门与归档` |
| P7 | 测试与证据：`tests/` 下完成 golden 回归、中断恢复测试（S4 后 kill，验证 S0–S3 不重跑）、未来信息泄露测试；把一次完整演示运行与恢复运行的日志存入 `runs/demo/`（该目录加入 git 白名单，作为"核验与恢复的证据"提交） | `test: 黄金回归/恢复/泄露测试与演示证据` |
| P8 | 收尾：README 校对（姓名、运行说明）、CHANGELOG.md（记录改动与采纳/拒绝决策） | `docs: 收尾与变更记录` |

## 4. 验收清单（全部通过才算完成）

1. `python3 run.py --period 2026-10` 端到端跑通，产出 QC 报告、BS/IS 简表、指标表、异常清单、月度报告、核验意见、归档目录。
2. 异常清单必须检出样例数据 2026-10 的全部 4 个预埋异常：应收账款环比 +42%（收入仅 +10%）、存货环比 +59%、毛利率 30%→27%（-3pp）、货币资金 1,895→125 万元且新增短期借款 600 万。
3. `python3 -m pytest tests/ -v` 全绿，其中：报表数字与 TB 重算一致率 100%；报告中数字引用溯源抽检零错误。
4. 恢复测试：在 S4 完成后 kill 进程，`--resume` 重跑时 S0–S3 的日志显示 skipped 而非 re-run。
5. 泄露测试通过：Agent 输入中不出现晚于 period_end 的数据行。
6. 仓库内无凭据、无真实公司数据；`runs/`（除 `runs/demo/`）已被 gitignore。

## 5. 非目标（不要做）

- 不做真实 ERP 对接、不做网络数据抓取；
- 不追求完整会计准则口径的三表（简表即可，重点是工作流而非会计完备性）；
- 不引入 Web 界面、数据库服务、Docker 等重基建；
- 不为了"好看"而绕过 QC 硬门禁、人工批准门或修订轮数上限。
