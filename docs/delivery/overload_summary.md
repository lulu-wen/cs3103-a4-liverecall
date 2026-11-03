# Current overload policy

`delivery/overload_policy.py` evaluates each client's monitoring snapshot.

- Enter DEGRADED when queue utilization reaches 50%, oldest queued age reaches
  0.5 s, or the in-flight attempt reaches 0.5 s.
- Queue utilization is the larger of message-count and payload-byte utilization.
- Return to NORMAL only when utilization is at most 20%, oldest age at most
  0.15 s, and in-flight age at most 0.15 s (or no send is active).
- Different entry/exit thresholds reduce rapid mode switching.
- DEGRADED enables same-segment coalescing and lower-segment provisional dropping.
  Finals and opaque initial messages remain protected from these removals.
- FULL admission retries atomically with both dropping flags. If it still fails,
  the server unregisters the client and schedules close code 1013.

These are queue/send-pressure signals, not browser acknowledgements or measured
ping RTT. Keepalive independently uses a 2 s ping interval and 5 s pong timeout.
`--naive` disables proactive transitions but retains the FULL fallback.

The lower-segment rule assumes sequential source segments. Initial HISTORY can
omit older finals when its byte budget is exceeded. See the
[measured evidence](../../experiments/delivery_20261103/README.md) for observed
freshness, reconnect costs and memory growth.
