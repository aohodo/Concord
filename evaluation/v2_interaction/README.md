# Concord 多轮交互评测

这里把评测单位从单条 `Case` 改为有隐藏真相、动态用户状态和停止条件的 `Episode`。

评测回答“系统采取某个交互行为后，是否获得新证据、降低不确定性并推动 Case 收敛”。
单轮分类准确率不能替代该多轮结果指标。

## 正式实验范围

正式数据是 `benchmark_30.jsonl`：

- 企业 IT、SaaS 接入、部署交付三个领域，每个领域 10 个 Case。
- 每条 Case 包含隐藏环境、可观察事实、安全/不安全动作、期望终态、动态用户状态和负向主观压力。
- `normative_reference` 只表示公开规范与封闭环境政策，不冒充专家标注。
- 每条规则包含来源、适用条件、严重级别和注册表中的可执行检查。

扩展压力集 `benchmark_240.jsonl` 将同一批 30 个真实问题分别置于 8 种交互条件：专业清晰、新手表达、碎片表达、半专家高置信、已重复失败、低控制感、低耐心 deadline、主动纠错。它们是实验控制条件，不是人口学用户画像。这样可以区分“某个领域知识不足”和“所有领域遇到同一种交互困难都会失败”。

`episodes.seed.jsonl` 是早期 12 领域探索集，只保留作设计历史，不参与默认正式实验。

## 运行

先在 `backend/.env` 临时设置并启动最新后端：

```env
CONCORD_ENABLE_EVALUATION_VARIANTS=true
```

正常运行必须保持 `false`，避免外部请求绕过问题表征。先查看实验规模，不发请求：

```powershell
D:\anaconda3\envs\concord\python.exe -m evaluation.v2_interaction.run_episode_eval --plan
```

执行完整系统与两个消融变体，共 3 变体 × 30 Episode × 3 次重复：

```powershell
D:\anaconda3\envs\concord\python.exe -m evaluation.v2_interaction.run_episode_eval --base-url http://127.0.0.1:8000 --concurrency 4
```

执行扩展集时显式指定数据；默认不自动消耗 240 条的模型额度：

```powershell
D:\anaconda3\envs\concord\python.exe -m evaluation.v2_interaction.run_episode_eval --specs evaluation/v2_interaction/benchmark_240.jsonl --variants v2_full --repetitions 1 --concurrency 4 --skip-judge
```

长实验每完成一个 Episode 就增量写入。中断后使用 `--resume`；确认丢弃旧结果时才使用 `--overwrite`，两者不能同时出现。

正式运行前可做一条 Case 的低成本链路烟测：

```powershell
D:\anaconda3\envs\concord\python.exe -m evaluation.v2_interaction.run_episode_eval --episode-limit 1 --repetitions 1 --skip-judge --overwrite
```

默认输出到 `evaluation/v2_interaction/results/`：

- `raw_episodes.jsonl`：完整多轮原始轨迹。
- `episode_metrics.csv`：每个 Episode 的过程指标。
- `variant_comparison.csv`：完整系统和两个消融版本的汇总。
- `pass_k.json`：每个变体的 `pass^1 ... pass^k` 稳定性。
- `EPISODE_REPORT.md`：只基于实测结果生成的摘要。

默认 HTTP 客户端使用 `trust_env=False`，不会继承代理环境变量。

## 核心指标

- `turns_to_ready`：达到 CASE_READY 的轮数。
- `premature_ready`：关键隐藏事实仍未披露却已经 ready。
- `critical_fact_recall`：交互实际取得的关键事实比例。
- `evidence_gain_per_turn`：每轮带来的新证据。
- `no_progress_turns` / `unnecessary_questions`：无进展和无效追问。
- `hypothesis_promoted_to_fact`：错误提升用户假设的次数。
- `m1_model_calls`：M1 实际模型调用数；普通业务轮次目标为 1，元对话为 0，复杂矛盾允许 2。
- `failure_retention_pass`：已报告失败是否进入注意与经验复用先验。
- `response_characters`：用于比较交互条件下的阅读负担。
- `contract_violation_turns`：LLM 提议被程序约束修复的轮次。
- `explicit_goal_captured` / `mutual_goal_grounding`：捕获目标与真正形成共同目标分开计算。
- `correction_recovery`：受控纠错后，旧声明是否变为 contested 且新声明被保留。
- `user_burden_score` / `user_abandoned`：累计问题、无进展、策略违规和用户退出。
- 用户 frustration / patience 的变化。
- 首个行为是否落在专业参考允许集合中。

当前 `/chat` 没有暴露 token usage，指标明确记为 `unavailable`。M1 的终点只是可靠 Case，`resolution_success` 固定记为 `unavailable`；`m1_boundary_pass` 只衡量共同目标、关键证据、认识论边界、纠错和 CASE_READY 是否合格，不要求 M2 的环境终态。

客服/实施支持的产品总评不能止于 `m1_boundary_pass`。用户可能不知道如何描述问题，首轮碎片化甚至主要依赖截图都不应直接判失败。进入 M2 后必须另外报告最终任务解决率、首个有效动作时间、总耗时、用户操作/阅读负担、失败动作重复率和结果确认；M1 的事实忠实度是必要边界，不是产品成功的替代指标。

## 多模态补充验证

`qwen3.8-flash` 支持图片输入、文本输出。适配层和一次真实合成故障截图烟测见 `MULTIMODAL_SMOKE_REPORT.md`。当前只证明模型与客户端链路可用；图片解析结果尚未接入 M1 的证据来源模型，不能把视觉模型转写直接冒充已验证事实。

## LLM-as-Judge 负向压力测试

假期期间不依赖同学或专家。LLM Judge 只评价可理解性、交互负担、专业观感和事实保真，不能覆盖隐藏环境、规则检查和最终状态。

单 Judge 默认复用 `backend/.env` 的 OpenAI-compatible 配置。多模型面板通过环境变量配置：

```env
CONCORD_JUDGES_JSON=[{"judge_id":"judge-a","model":"model-a","base_url":"https://example/v1","api_key_env":"JUDGE_A_KEY"},{"judge_id":"judge-b","model":"model-b","base_url":"https://example/v1","api_key_env":"JUDGE_B_KEY"}]
```

三个变体以匿名、随机顺序交给 Judge。不同 Judge 结论不一致时记录 `judge_disagreement=true`，不强行投票。无 Judge 配置或 Judge 请求失败不会影响客观实验结果。使用 `--skip-judge` 可完全关闭。

每个正式 Case 还会在对话中注入一种没有证据的主观压力：高置信归因、伪权威背书、情绪催促、要求跳过查证或跨领域经验迁移。系统不需要证明用户一定错，只需要保证这些说法在取得证据前不被扭曲为事实。如果 Judge 认为回复“很专业”但同时判定事实失真，会单独记录为 `professional_but_distorted`，这正是负向压力测试要捕获的失败。

模拟用户和 Judge 都是代理测量，不能证明真人满意或专家认可。项目可以严谨声明的是：在公开规范与封闭环境下的客观终态、事实保真、策略合规及相对消融结果。
