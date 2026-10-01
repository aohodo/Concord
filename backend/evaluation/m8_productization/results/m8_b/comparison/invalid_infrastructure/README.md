# Excluded infrastructure sample

The preserved `host_suspension/v2_no_failure_memory.jsonl` contains one Episode whose wall-clock
measurement crossed a workstation sleep interval. The process, network request and durable worker
were suspended while the wall clock continued to advance, producing an approximately 8,080-second
latency and a non-terminal `running` snapshot.

That row is excluded from the comparison summary. It is not treated as an Agent failure and is not
used to claim that failure memory improves success or latency. The replacement row was collected
while the host remained active; its provenance is recorded under
`../reruns/no_failure_memory_timeout_fix/` and `../merged_no_failure_memory/`.
