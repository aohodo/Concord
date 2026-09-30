# M3 Phase 2 Method: Adaptive Collaboration as a Human-Inspired Control Prior

## Product claim

M3 does not assume that more agents are better. It treats experienced-team behavior as a computational prior: preserve one Case Owner, delegate only a bounded cognitive bottleneck, isolate independent judgments, measure coordination cost, verify advice against the environment, and shrink or stop the team when marginal value disappears.

This is a product control design, not anthropomorphic role-play. Agent names do not establish expertise; only capabilities and downstream verified outcomes affect future routing.

## Behavior-to-runtime mapping

| Experienced human/team behavior | Runtime mechanism | Observable check |
|---|---|---|
| Incident commander retains the whole case | M2 remains Primary Case Owner; M3 cannot execute environment writes | every M3 packet remains `advisory_until_m2_verifies` |
| Ask the smallest useful set of specialists | adaptive topology and marginal-gain gate | adaptive agent count and `no_benefit` decisions |
| Know who has proved useful at which task | capability/task-specific outcome memory | only cited M2 tool outcomes update contextual reliability |
| Treat failed actions as strong feedback | failed collaborators and disproven advice receive negative outcomes | outcome store and failure counts |
| Get an independent second opinion for high-risk action | first-pass `facts_only` context | hypotheses are hidden from reviewer/explorer contexts |
| Do not mistake repeated hearsay for corroboration | basis lineage and shared-source warnings | `shared_source_groups` and synthesis warnings |
| Stop when the next useful step is environmental evidence | evidence boundary returns to M2 | `evidence_required`, not another discussion round |
| Expand only for a concrete complementary gap | second round consumes explicit `additional_assignments` | no unconstrained re-fan-out |
| Preserve the main line when the user adds facts | monotonic Case revision and stale-result rejection | mid-flight revision episodes |
| Keep the user informed without exposing hidden reasoning | grounded progress events and SSE | stage/status messages only |

## Industrial references

- Google SRE incident management keeps an Incident Commander responsible for high-level state while the operations group is the only group modifying the system. It also allows response teams to expand and contract with need. This motivates M2 ownership, M3 advisory-only operation, and bounded team resizing: <https://sre.google/sre-book/managing-incidents/>.
- Anthropic's production multi-agent research system uses an orchestrator-worker structure for genuinely parallel, open-ended work, while reporting much higher token consumption and warning that tightly dependent tasks are a poor fit. This motivates the executed fixed-topology control, token accounting, serial-dependency negative cases, and the marginal-gain gate: <https://www.anthropic.com/engineering/multi-agent-research-system>.
- Anthropic's effective-agent guidance distinguishes flexible orchestrator-workers from predefined parallelization. This supports capability-driven assignments rather than a permanent domain fan-out: <https://www.anthropic.com/engineering/building-effective-agents>.

## Academic references

- Kozlowski and Ilgen's team-effectiveness review motivates shared mental models and transactive memory: <https://doi.org/10.1111/j.1529-1006.2006.00030.x>.
- MAST reports multi-agent failures in specification/system design, inter-agent misalignment, and verification/termination. Phase 2 therefore evaluates whole trajectories, makes termination explicit, and never accepts consensus as verification: <https://arxiv.org/abs/2503.13657>.
- Adaptive Graph Pruning studies task-dependent agent quantity and communication topology. Concord independently implements a non-trained product analogue through hard team-size bounds, explicit complementary assignments, and cost-aware stopping; it does not claim to reproduce the paper's learned pruning method: <https://arxiv.org/abs/2506.02951>.
- VerifyMAS frames failure attribution as hypothesis verification over complete trajectories. Concord uses a smaller engineering version: M2 must cite the M3 assignment it adopted, and only the resulting tool observation can update reliability: <https://arxiv.org/abs/2605.17467>.
- AgentLocate studies agent and decisive-step failure localization. Concord preserves assignment IDs through the M2 tool trace for the same auditability goal, without claiming equivalent learned attribution accuracy: <https://arxiv.org/abs/2607.07989>.

## Evaluation design

The eight boundary cases run under three actually executed controls:

1. `m2_only`: no collaborators;
2. `fixed_three`: three independent workers on every case;
3. `adaptive`: decide whether and how to collaborate.

The 36 longitudinal episodes add fragmented reports, deadline pressure, failed prior attempts, wrong user hypotheses, and mid-flight corrections. They deliberately test stale-result rejection and verified outcome learning. Synthetic outcome labels are explicit and must not be described as expert human ground truth.

Structural novelty is only a duplication proxy. It is never treated as truth or success. Positive reliability credit requires downstream evidence; absent attribution produces no credit.
