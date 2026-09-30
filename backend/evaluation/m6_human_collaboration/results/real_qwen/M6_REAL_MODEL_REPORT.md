# M6 Real-model Human Collaboration Report

## Scope

These longitudinal Episodes entered through the public HTTP boundary and used the configured real model. Simulated environment outcomes remain the success authority; interaction metrics do not substitute for task completion.

## Results

- Episodes: 4
- Outcome + chain pass: 4/4
- Terminal statuses: {'resolved': 3, 'human_required': 1}
- Expression depths: {'guided': 3, 'minimal': 1}
- Visible turn acknowledgement: 4/4
- Human-effort accounting present: 4/4
- Mean wall time: 139.51s
- Mean turn wait: 51.68s
- Mean time to first effective environment action: 42.12s (4/4 measured)
- Mean user turns: 2.75
- Mean user input characters: 118.5
- Mean primary reading burden per turn: 80.2 characters
- Repeated executable actions: 0
- Goal-correction recovery: 3/3 applicable Episodes
- Model decisions: 26
- Tool calls: 17

| Episode | condition | outcome | depth | turns | latency s | first action s | primary chars | repeats | goal recovered |
|---|---|---|---|---:|---:|---:|---:|---:|---|
| saas_webhook_secret__fragmented_novice | fragmented_novice | resolved | guided | 3 | 159.35 | 42.67 | 320 | 0 | True |
| education_sso_clock_drift__deadline_result_first | deadline_result_first | resolved | minimal | 2 | 125.01 | 48.03 | 40 | 0 | False |
| logistics_spooler_backlog__self_correction | self_correction | resolved | guided | 3 | 153.93 | 53.87 | 378 | 0 | True |
| permission_boundary__fragmented_novice | fragmented_novice | human_required | guided | 3 | 119.73 | 23.89 | 204 | 0 | True |

## Interpretation boundary

This small targeted run verifies wiring and representative longitudinal behavior. It does not estimate population-level satisfaction, accessibility, or universal domain competence. Those claims require larger M7 datasets.
