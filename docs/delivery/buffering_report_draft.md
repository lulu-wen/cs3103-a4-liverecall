# Buffering and isolation: report contribution

The complete English text, setup, measured table and plots are in
[the report draft](../../report.pdf).
The [current evidence index](../../experiments/recovery_batches/README.md) retains
commands, source hashes, raw results and plotting scripts.

## Implementation

- Each connection owns a bounded FIFO, one sender and a supervised session.
- Producers enqueue without awaiting socket writes; a slow sender cannot hold
  up normal clients' sends.
- Defaults: 256 queued messages/1 MiB payload, 10 s send and 3 s close deadlines.
- Cleanup unregisters the connection, clears pending data and awaits child tasks.
- Recovery snapshots state before registration; the sender streams bounded
  HISTORY batches and active snapshots before consuming its live queue.
- [Overload handling](overload_summary.md) uses hysteresis and semantic dropping;
  unresolved overload disconnects the client for retained-history recovery.

## Measured findings

- Recovery export `f630bc443b7a`: 141 Ubuntu tests passed; T1–T5 each ran three times.
- The independent recovery oracle verified all required state in 15/15 runs;
  approximately 1.9 MiB of history fit in two batches, median completion 16.34 ms.
- T3 normal clients: 100% live coverage and final completeness in both snapshots.
- Slow P95 live age: 28.08 s before → 13.36 s current; final completeness:
  51.32% → 100% at the cutoff.
- Current T3 still has 20 connection-error events and 380 recovered history items
  per run. Slow fresh-final ratio is only 1.32%; recovery is not real-time delivery.
- Current T3 RSS peaks: 33.36–33.38 MiB. The separate 300 s workload rises
  approximately 0.70 MiB/min after 60 s; it does not establish a plateau.

## Limits

- Before/after changes whole default versions, so it does not isolate coalescing.
- Lower-segment dropping was removed; coalescing retains each active segment's
  latest revision. Live overflow can still evict clients and discard pending data.
- Batching sends all selected retained finals, but expired/count-pruned entries
  and individually oversized items remain limits.
- RSS excludes clients and kernel buffers; bounded queues do not prove bounded
  long-term total memory.
