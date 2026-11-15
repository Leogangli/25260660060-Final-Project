# 期末项目：月度财务关账与管理报告长程智能体工作流

- **姓名**：李刚
- **学号**：25260660060
- **课程**：金融人工智能编程（2026 年秋季）
- **代码仓库**：https://github.com/Leogangli/25260660060-Final-Project

## 项目概述

本项目为「月度财务关账 → BS/IS 简表生成 → 财务异常检测 → 管理层月度报告」这一典型的财务总监（CFO）周期性任务，设计并实现一个**长程智能体工作流（FinClose Agent Workflow）**。工作流串联 S0–S8 九个阶段，具备持久化状态与检查点、定时/事件触发、中断恢复、独立核验、人工批准门与有界修订循环等能力，而非一次性脚本。

目标用户为中小型制造/贸易企业的财务总监与财务经理（与本人实际工作场景一致）。所有样例数据均为**合成数据**，不含任何真实客户或公司敏感信息。

## 目录结构

```
README.md                      本文件
docs/
  01-任务定义.md                作业第 1 条：任务、流程、阶段、触发、验收标准
  02-架构设计.md                作业第 2 条：带注释工作流图 + 设计说明
  03-KIMI-CODE-实施简报.md      面向 Kimi Code 的分阶段实施简报与验收清单
prompts/                       各智能体提示词（角色、工具、输入/输出、约束）
config/workflow.yaml           工作流配置（阶段 DAG、阈值、重试、限额、调度）
src/
  core/                        Orchestrator、状态与检查点
  deterministic/               确定性计算（QC 检查、报表、指标——不经过 LLM）
  llm/                         可插拔 LLM 客户端（默认 Mock，便于无密钥运行）
data/sample/                   三个月合成试算平衡表 + 科目映射表
tests/                         黄金回归、中断恢复、未来信息泄露及门禁测试
runs/                          运行产物（除 runs/demo/ 外已 gitignore）
runs/demo/                     核验、投毒、中断恢复及完整运行证据
```

## 环境与运行

- Python ≥ 3.11；运行依赖见 `requirements.txt`（仅 pandas、pyyaml）。
- 安装运行依赖：`python -m pip install -r requirements.txt`。
- 安装测试依赖：`python -m pip install -r requirements-dev.txt`。
- 完整演示：`python run.py --period 2026-10`，S6 通过后按提示输入 CFO 批准信息。
- 中断恢复：`python run.py --period 2026-10 --resume`。
- 完整测试：`python -m pytest tests/ -v`。
- Unix/Linux 可将上述命令中的 `python` 替换为 `python3`。
- 默认使用 Mock LLM，确定性、可复现且无需密钥；真实 LLM 客户端为预留接口，当前未启用，任何密钥均不得写入仓库。

主要产物位于 `runs/<period>/`：QC 报告、关账检查、BS/IS 简表、指标表、异常清单、管理层报告、独立核验意见、批准记录及 `archive/v<n>/` 版本归档。

## 人工批准与调度

- S6 通过后，CLI 会要求输入 CFO 批准人、批准决定和意见；批准前不会执行 S8 归档。
- 无人输入或明确驳回时 S7 挂起，不会自动批准或归档；获得有效批准后执行 `python run.py --period <YYYY-MM> --resume` 续跑。
- 批准记录同时绑定 `run_id` 和终稿哈希，旧运行或旧终稿的批准不会被复用。
- 归档保存在 `runs/<period>/archive/v<n>/`，版本不可覆盖；同时生成 `baseline.json` 和 `next_trigger.json`。
- crontab 示例（每月 1 日 06:00，替换项目路径和期间）：`0 6 1 * * cd /path/to/project && python3 run.py --period 2026-10 --resume >> runs/cron.log 2>&1`

## 测试与演示证据

- `runs/demo/P7_完整运行.log`：S0–S8 完整运行。
- `runs/demo/P7_S4后中断.log`、`runs/demo/P7_S4后恢复.log`：S4 后中断及恢复，S0–S4 不重跑。
- `runs/demo/投毒修订演示.log`：错误数字被拦截、修复任务包路由及 v2 核验通过。
- `runs/demo/有界循环演示.log`：初稿加两轮修订仍不收敛后升级，不生成 v4。

## 本人贡献说明

- 任务选型、业务流程与验收标准来自本人 20 余年财务工作及现任财务总监岗位的实际实践；
- 工作流架构（角色分工、检查点与恢复、核验与人工批准门）由本人设计，见 docs/01、docs/02；
- 代码实现借助 AI 编程助手（Kimi Code）完成，本人负责需求澄清、逐阶段评审、测试验收与全部提交的最终确认（提交历史可见迭代过程）；
- 样例数据为本人构造的合成数据，异常场景（应收激增、存货积压、毛利率下滑、现金流背离）取自本人实际工作中遇到的典型风险模式。
