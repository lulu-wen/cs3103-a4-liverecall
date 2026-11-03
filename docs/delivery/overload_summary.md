# Current overload policy

`delivery/overload_policy.py` evaluates each client's monitoring snapshot.

- Enter DEGRADED when pressure reaches 50% of the NORMAL queue budget, oldest age reaches
  0.5 s, or the in-flight attempt reaches 0.5 s.
- Pressure is the larger of total pending count/NORMAL count budget and total
  pending bytes/NORMAL byte budget. It can exceed 100% while separate degraded
  lanes are populated; it is not a percentage of their combined hard capacity.
- Return to NORMAL only when utilization is at most 20%, oldest age at most
  0.15 s, and in-flight age at most 0.15 s (or no send is active).
- Different entry/exit thresholds reduce rapid mode switching.
- DEGRADED uses a protected final/control FIFO (256 items/1 MiB) and latest-active
  map (1,000 segments/1 MiB), independently bounded. NORMAL uses one 256-item/1 MiB
  FIFO. Provisional replacements never consume final-lane capacity.
- Under low final pressure, four protected sends allow an active turn. A final
  backlog of at least min(4, FIFO capacity) overrides that share and drains
  finals first. Latest active state remains stored, but may wait under pressure.
- This protection applies to coalescing, not to every disconnect: queue teardown
  discards pending data, and recovery remains subject to history/capacity limits.
- FULL admission retries atomically with protected lanes. If it still fails,
  the server unregisters the client and schedules close code 1013.

These are queue/send-pressure signals, not browser acknowledgements or measured
ping RTT. Keepalive independently uses a 2 s ping interval and 5 s pong timeout.
`--naive` disables proactive transitions but retains the FULL fallback.

The former lower-segment rule was removed after interleaved-source experiments
reproduced loss of still-useful latest provisionals. See the
[threshold experiment](../../experiments/overload_thresholds/README.md).
The [final-aware comparison](../../experiments/final_aware_delivery/README.md)
checks protected admission, high final-only utilization and actual lane bounds.
Keeping these revisions can increase backlog or lead to earlier eviction under
sustained overload; preservation does not guarantee immediate delivery.
Recovery now streams all selected retained finals in bounded HISTORY batches;
expired/count-pruned entries, individual message limits and live-queue overflow
remain limitations. See the
[measured evidence](../../experiments/delivery_20261103/README.md) for observed
freshness, reconnect costs and memory growth.
