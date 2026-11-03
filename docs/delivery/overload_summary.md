# Current overload policy

`delivery/overload_policy.py` evaluates each client's monitoring snapshot.

- Enter DEGRADED when queue utilization reaches 50%, oldest queued age reaches
  0.5 s, or the in-flight attempt reaches 0.5 s.
- Queue utilization is the larger of message-count and payload-byte utilization.
- Return to NORMAL only when utilization is at most 20%, oldest age at most
  0.15 s, and in-flight age at most 0.15 s (or no send is active).
- Different entry/exit thresholds reduce rapid mode switching.
- DEGRADED scans the queue for provisionals superseded by a higher revision of
  the same segment. Segment ID order alone never justifies dropping a message.
  Finals and opaque initial messages remain protected from these removals.
- This protection applies to coalescing, not to every disconnect: queue teardown
  discards pending data, and recovery remains subject to history/capacity limits.
- FULL admission retries atomically with coalescing. If it still fails,
  the server unregisters the client and schedules close code 1013.

These are queue/send-pressure signals, not browser acknowledgements or measured
ping RTT. Keepalive independently uses a 2 s ping interval and 5 s pong timeout.
`--naive` disables proactive transitions but retains the FULL fallback.

The former lower-segment rule was removed after interleaved-source experiments
reproduced loss of still-useful latest provisionals. See the
[threshold experiment](../../experiments/overload_thresholds/README.md).
Keeping these revisions can increase backlog or lead to earlier eviction under
sustained overload; preservation does not guarantee immediate delivery.
Recovery now streams all selected retained finals in bounded HISTORY batches;
expired/count-pruned entries, individual message limits and live-queue overflow
remain limitations. See the
[measured evidence](../../experiments/delivery_20261103/README.md) for observed
freshness, reconnect costs and memory growth.
