# Buffering and isolation: report contribution

The English report text and figures are in [report.pdf](../../report.pdf).
[Current evidence](../../experiments/final_aware_delivery/README.md) retains
methods, source hashes, raw results and plotting scripts.

## Implementation

- Each connection owns one queue, sender and supervised session. Producers
  enqueue without awaiting socket writes.
- NORMAL retains all accepted revisions in FIFO order: 256 messages/1 MiB.
- DEGRADED separates a final/control FIFO (256/1 MiB) and latest-active map
  (1,000 segments/1 MiB). Modes are exclusive: at most 1,256 pending live
  items/2 MiB, excluding object/socket overhead, in-flight sends and recovery.
- Finals retain order; active entries replace only same-segment older versions.
  Newer finals remove their own provisionals. No cross-segment latest-state drop.
- The scheduler prioritizes finals, sharing spare service with active state.
  A final backlog override avoids a fixed 80% final-throughput ceiling.
- RecoveryPlan streams bounded HISTORY batches and active snapshots before
  live delivery. Cleanup unregisters, clears pending state and awaits tasks.
- Send/close deadlines remain 10 s/3 s; hard lane exhaustion can still evict.

## Measured findings

- Export `f115f048f59d`: 158 Ubuntu tests; T1–T5 each repeated three times.
- Every public run has 100% final completeness; T1/T2 have 100% revision coverage.
- All 15 recovery-oracle runs return every required final/latest active payload.
- All 15 final-aware controlled cases preserve finals/latest active state;
  normal coverage is 100%. These virtual-time cases are not network benchmarks.
- Public T3 slow-client P95 remains 13.36 s, with 20 reconnect errors and 380
  recovered items per run. Slow fresh-final ratio is 1.32% despite full final
  completeness. The scheduler did not materially improve this public result.
- T3 RSS peaks: 33.54–33.63 MiB. One 300 s workload rises about 0.72 MiB/min
  after 60 s; no stable plateau or root cause is established.

## Limits

- Storage and scheduling both change; this is not a priority-only comparison.
- Hard bounds can still cause eviction; disconnect clears pending data.
- Already in-flight/TCP-buffered provisional messages cannot be coalesced.
- The unchanged 5,000-final History cap can truncate the required 60 s window
  above about 83 finals/s in a capacity probe. Retention policy remains open.
- Bounded containers and a short RSS trace do not establish bounded total memory.
