# Concord M1 人类行为契约

本文只声明当前代码能够被测试证实的行为，不用行为名称代替实现证据。每项能力必须同时具备触发条件、可观察输出和失败判据。

Concord 是面向工业落地与秋招展示的产品工程。下列研究只用于约束设计和避免拍脑袋，不将产品包装成已经完成的人类实验。

## 设计依据

- **共同基础（Common Ground）**：共享事实与信念需要被动态维护；技术支持日志中的共同基础错位与任务失败显著相关。参考 [Building Common Ground in Dialogue](https://aclanthology.org/2025.luhme-1.2/) 与 [Ubuntu 技术支持日志中的 Common Ground Misalignment](https://aclanthology.org/2025.acl-long.161/)。
- **选择性澄清**：是否追问必须同时考虑歧义、风险、预期信息收益与用户成本，而不是“信息不全就问”。参考 [Clarify When Necessary](https://aclanthology.org/2025.findings-naacl.306/) 与 [Value of Information: A Framework for Human–Agent Communication](https://aclanthology.org/2026.acl-long.1987/)。
- **动态用户建模**：专业程度、挫败和心理模型影响交互，但它们是当前任务中的可修正状态，不是永久人格标签。参考 [Adaptive Generation Using Dynamic User Modeling](https://aclanthology.org/J14-4006/) 与 [Mental Models in an Adaptive Dialog Agent](https://aclanthology.org/2025.findings-naacl.56/)。
- **真实部署中的挫败检测**：关键词、通用情感模型和 LLM 都不能自动等价于真实用户满意度，状态推断必须保留来源与不确定性。参考 [User Frustration Detection in Task-Oriented Dialog Systems](https://aclanthology.org/2025.coling-industry.23/)。

## 保证等级

- **L1 程序保证**：不依赖 LLM 自觉遵守；状态归并、策略 Harness 或测试可以直接拒绝反例。
- **L2 有来源的结构化判断**：由 LLM 解释语义，但写入持久状态前必须通过原文 quote 校验。
- **L3 策略假设**：语义质量仍由 LLM 决定，只能通过 Episode 或真人实验评价，不能宣称已经稳定具备。

## 行为及其可证伪契约

| 人类行为 | 触发条件 | 当前程序行为 | 等级 | 明确失败判据 |
|---|---|---|---|---|
| 目标对齐 | 目标未知、仅推断或存在歧义 | 只有外部任务结果能成为 operational goal；“继续问/愿意配合”不得覆盖业务目标；明确说出的目标标记 `EXPLICITLY_GROUNDED`，推断目标仍需用户确认 | L1+L2 | 把对话过程当业务目标；未明确目标便 CASE_READY |
| 情境重建 | 没有直接现象或 `situation.what` | 触发 `SITUATION_RECONSTRUCTION`，优先 structure/scaffold/verify | L1+L2 | 只有原因猜测、没有观察仍进入求解 |
| 认识论校准 | 存在 hypothesis | observation/action/hypothesis 分栏；用户高置信措辞仍以 `REPORTED` 保存 | L1+L2 | hypothesis 被自动写成 verified fact |
| 关键证据获取 | 存在 open evidence need | evidence need 跨轮持久保留；只有本轮带原文 quote 的 `resolved_evidence` 才能关闭；有环境/工具 evidence catalog 时只能使用目录中的稳定 key | L1+L2 | 用户没有回答时证据缺口消失；创造同义 key 导致环境无法回答 |
| 澄清价值控制 | 信息缺口可能触发追问 | 每条证据显式记录 `blocking / decision_impact / answerability / user_burden`；deadline 或低耐心时禁止追问非阻塞信息 | L1 约束、L2 语义 | 为补全表格而追问不会改变方案的信息 |
| 无进展重问抑制 | 同一 target 已问过且仍 open | 记录 `attempt_count`；Harness 拒绝原样重复并选择未尝试的高价值低负担证据 | L1 | 连续询问相同 target 且没有新证据 |
| 矛盾修复 | 共享状态存在 contradiction | 产生 `CONTRADICTION_REPAIR`；只有带本轮原文 quote 的 resolution 才能移除 | L1+L2 | 未经用户消解便删除矛盾 |
| 避免重复失败操作 | 用户报告已经执行某动作 | 写入 action-result ledger，并进入 `forbidden_actions`；输出 Harness 检查原提议是否重复 | L1 | 最终回复再次原样要求 forbidden action |
| 恢复用户掌控感 | 高挫败、低控制感或低操作意愿 | 允许 reflect/restore_agency/scaffold，并限制为低负担、单步骤 | L1 约束、L3 效果 | 回复违反负担契约；是否真的恢复掌控感须真人评价 |
| 截止时间优先 | 存在 deadline 或低耐心 | 激活 prioritize/restore_agency；回复进入 `minimal`；只允许一个真正阻塞的问题，非阻塞问题后置 | L1 约束、L3 策略 | 在更高优先级安全/目标需求存在时只做安抚；追问非必要细节；给长流程 |
| 适配专业程度 | 有用户原文支持的知识水平信号 | low 使用 plain/guided，high 允许 technical；每次信号保留 quote 和 turn_id | L2 | 没有原文依据却永久贴用户标签 |
| 控制认知负担 | InteractionContract 已生成 | Harness 校验 question budget、response length、forbidden actions；违规提议替换为可审计的低负担问题 | L1 | 最终输出超过问题预算或保留禁止动作 |
| 优先处理最关键需求 | 同时存在多个 interaction needs | 行为集合由 needs 声明式生成；Harness 要求所选行为覆盖最高优先级 need | L1 | critical goal/contradiction 存在，却只选择低优先级支持行为 |
| 推动 Case 收敛 | 新一轮用户回答到达 | history 记录新增 claim、已解决 evidence、progress、目标证据和行为契约修复 | L1 | 无法从轨迹判断本轮是否增加证据 |
| 信息充分后再求解 | 目标、观察、关键证据达到最低条件 | 只有 `CASE_READY` 才进入 M2 闭环解决 | L1 | 有 open critical/high evidence 仍正常进入解决阶段 |
| M1→M2 不确定性交接 | 达到 `CASE_READY` | `to_resolution_context()` 分别交付 observations、hypotheses、reported actions、scope constraints 和未解决的非阻塞证据 | L1 | M2 收到一段扁平摘要，无法分辨事实、猜测与未知项 |

## 当前能够成立的强表述

Concord M1 **程序化保证**：用户陈述中的观察、动作与假设被分别保存；合作性话语不能覆盖 operational goal；来源不受支持的候选不会写入持久状态；环境证据 key 保持稳定；未解决的阻塞证据不会仅因下一轮缺少模型输出而消失；无进展 target 不会原样重复；急迫状态会压缩回复并取消非阻塞追问；M2 接收到保留不确定性的结构化上下文；每轮证据进展可由轨迹审计。

## 当前不能成立的表述

以下结论必须通过 Episode 与真人评测后才能声明：

- 系统已经达到专业实施工程师水平。
- 系统总能提出信息价值最高的问题。
- 用户看到 `restore_agency` 后实际更有掌控感。
- 用户状态估计总是准确，或能够代表长期人格。
- 达到 CASE_READY 等同于最终解决成功。
- 多智能体一定优于单智能体或专业人类。

将不可验证部分明确排除，是本契约能够经受反驳的必要组成，而不是能力缺陷的掩饰。
