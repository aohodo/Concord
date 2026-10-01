# M4 End-to-End Integration and Evidence

## Research and product question

M1、M2、M3 分别通过模块实验，不等于 Concord 已经作为一个完整系统成立。M4 检查：

> 一个从混乱自然语言开始的 Case，能否在不绕过任何上游阶段的情况下，经 M1 表征、M2 行动、必要的 M3 协作和 M2 验证后收敛。

## Entry boundary

正式 M4 Episode 只允许使用产品 HTTP API：

```text
POST /runtime/cases
POST /chat
GET  /cases/{case_id}
GET  /runtime/cases/{case_id}
GET  /trace/tool/{case_id}
```

评测器禁止直接构造 `ResolutionContext`、`CollaborationHandoff` 或调用子 Agent。固定的 `case_id` 只用于在创建模拟环境前建立关联，不携带根因或隐藏状态。

## Catalog

扩展版包含 `10 个规则驱动环境 × 11 种纵向用户条件 + 1 条协作垂直链路 = 111 Episode`：

- `fragmented_novice`：先给模糊表象，再补观察和目标；
- `deadline_result_first`：明确时间/阅读预算并优先恢复；
- `self_correction`：先混入高置信猜测，随后撤回并补直接观察；
- `expert_precise`：给出清晰现象、可观察状态和可验证完成标准；
- `half_expert_hypothesis`：术语基本正确，但把经验归因说成确定事实；
- `procedural_only`：做过照抄步骤但不理解机制，并要求避免盲目重复；
- `cross_domain_transfer`：把其他领域的熟悉经验错误迁移到当前问题；
- `frustrated_repeat`：连续尝试失败，要求保留失败记忆并展示进展；
- `low_control`：担心误操作，优先授权只读、低风险检查；
- `low_patience_stream`：用连续短消息逐步补充，要求结果优先、短回复；
- `multi_issue_dump`：一次倾倒事实、传闻和弱相关问题，再明确当前主线。

10 个环境来自 M2 已验证模拟场景，但用户输入和入口全部重建，避免拿结构化 M2 Case 冒充全链路实验。当前覆盖 7 个领域，并包含权限边界、响应丢失、延迟生效、矛盾来源和重复失败操作。

额外垂直 Episode 注入连续、可审计的暂态工具失败，要求 M2 在局部方向持续无进展后触发 M3，M3 返回带来源建议，最终仍由 M2 执行并用环境状态验证。它不是通过评测器直接构造 handoff，也不把协作建议直接当成成功。

## Identity and observation

每个 Case 使用统一关系：

```text
conversation → case_id → m2_thread_id → m3_thread_id / collaboration_id
```

`GET /cases/{case_id}` 返回不包含 chain-of-thought 的生命周期视图，记录：

- M1 是否形成 Case；
- M2 是否开始、等待、解决或要求协作；
- M3 是否启动、完成或被判为陈旧；
- M3 结果何时返回 M2；
- 当前最终回复、阶段、状态、revision 和 Trace ID。

`case_revision` 只随新的用户输入/Case 更新推进，不再复用 M2 内部 cycle。M3 handoff 固定携带启动时的 Case revision；用户补充到来后，旧任务会被取消，或其返回结果被记录为 stale 而不得进入 M2。

M4 使用进程内 registry 建立契约；持久化、重启恢复和 durable job 属于 M5。

## Ground truth

主成功依据是模拟环境的可观察最终状态和工具审计，不是回复措辞或 Agent 自评：

- `resolved` 但成功条件未满足 → `UNVERIFIED_SUCCESS_CLAIM`；
- 没有从 M1 生命周期事件开始 → `M1_BYPASSED_OR_UNOBSERVABLE`；
- 需要 M2 但未进入 → `M2_NOT_REACHED`；
- Case ID 在阶段间改变 → `CASE_ID_DRIFT`；
- 为覆盖 M3 而设计的 Episode 若由 M2 独立解决并完成环境验证，仍判成功，同时记录非失败备注 `M3_COVERAGE_NOT_REACHED`。

“M2 可以选择协作”不等于“该 Case 必须协作”。例如写入已经提交但响应丢失时，M2 若能通过幂等查询自行确认结果，继续强制招募 Agent 属于额外协调成本，不能标记为失败。

LLM-as-Judge 可以补充目标理解、认识论分离、可理解性和交互负担，但不能覆盖环境失败。

## Comparison design

全量 111 Episode 首先运行 `v2_full`，用于回答跨领域链路是否稳定。机制消融使用固定的
20 Episode 分层配对子集，而不是从成功或失败结果中事后挑选：

- 覆盖全部 7 个领域；
- 覆盖全部 11 种用户条件，并对碎片表达、deadline、自我纠正等关键纵向条件保留重复样本；
- 覆盖普通恢复、权限边界、延迟生效、写入响应丢失、冲突来源、重复失败以及
  M3 专家恢复；
- 同一 Episode 的完整版本和消融版本共享环境、用户输入和成功判据，输出逐 Case
  配对差值。

子集分别运行：

- `v2_m2_only`：保留 M1/M2，禁止进入 M3；
- `v2_fixed_three`：M3 使用实际执行的固定三 Agent；
- `v2_no_user_state`：去掉当前用户状态控制；
- `v2_no_epistemic_separation`：去掉事实/假设分离；
- `v2_no_failure_memory`：保留环境和规划能力，但关闭相同失败动作的重复保护。

对照的目的不是证明完整系统在每项指标都更高，而是识别每个机制解决的具体失败类别及其成本。
M3 是动态可选机制：M2 已验证解决时，不触发协作是资源理性的正确结果；M3 到达率只作为协作覆盖与成本指标，不作为通用成功门槛。

每项主张使用与机制相邻的指标，禁止只用最终成功率替代：

| 设计主张 | 主要指标 | 不允许的过度解释 |
|---|---|---|
| 跨领域闭环 | 环境成功、终态、M1/M2/M3 事件、工具审计 | 7 个模拟领域等于真实行业能力 |
| 用户状态控制 | 问题数、用户动作数、回复长度、进度更新、终态 | 只因识别出画像就宣称有效 |
| 认识论分离 | 用户假设在调用链中的类型与下游行动差异 | 流畅回复等于没有把猜测当事实 |
| 失败记忆 | 重复失败调用、受控重试、最终验证 | 单条 3/4 差异等于总体提升 25% |
| 动态 M3 | M3 到达率、Agent/模型/工具成本、成功终态 | 普通 Case 未触发 M3 等于 M3 无效 |

报告同时生成：

- `stratified_results.csv`：领域与用户状态切片；
- `paired_results.csv`：同 Case 的成功、时延、模型调用、工具调用和回复负担差值；
- `comparison_summary.csv/json`：版本级汇总；
- 原始 JSONL：保留每个结论的可追溯来源。

服务端首次可见的 `user_turn_received`/心跳与最终闭环时延分开记录。前者衡量用户是否
长期面对空白等待，后者衡量系统完成 Case 的实际成本。跨主机睡眠、客户端中断或模型
队列超时的样本保留为基础设施无效证据，但不得混入 Agent 成功率和时延均值。

## Reproduction

启动服务时开放本地评测变体，并显式禁用代理：

```powershell
$env:HTTP_PROXY=''
$env:HTTPS_PROXY=''
$env:ALL_PROXY=''
$env:NO_PROXY='*'
$env:CONCORD_ENABLE_EVALUATION_VARIANTS='true'
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

运行全量 Episode：

```powershell
python -m evaluation.m4_end_to_end `
  --base-url http://127.0.0.1:8000 `
  --concurrency 4 `
  --batch-size 4 `
  --resume `
  --output-dir evaluation/m4_end_to_end/results/latest
```

每个小批次完成后都会原子式重写检查点；主机休眠或进程中断后，用同一命令即可跳过
已经完成的 Episode。原始 `raw_runs.jsonl` 是事实来源；Markdown 报告、JSON 摘要和 CSV
矩阵均由它派生。

运行配对消融：

```powershell
python -m evaluation.m4_end_to_end.comparison `
  --base-url http://127.0.0.1:8000 `
  --full-runs evaluation/m4_end_to_end/results/latest/raw_runs.jsonl `
  --output-dir evaluation/m4_end_to_end/results/comparison `
  --concurrency 4 `
  --batch-size 4 `
  --resume
```

消融同样按变体、按小批次保存；`--resume` 只跳过已有 Episode，不会把不完整的变体
误当成完整结果。

若主机休眠污染了已落盘样本，先把原始 bundle 移入 `invalid_infrastructure` 留档，再用
`--rerun-episode-id <id>` 显式替换受影响行；不得直接删除异常证据或把休眠时间计入模型时延。
