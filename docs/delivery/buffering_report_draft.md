# Buffering and isolation: report contribution

The complete English text, setup, measured table and plots are in
[the report draft](../../report.pdf).
The [evidence index](../../experiments/delivery_20261103/README.md) retains
commands, source hashes, raw results and plotting scripts.

## Implementation

- Each connection owns a bounded FIFO, one sender and a supervised session.
- Producers enqueue without awaiting socket writes; a slow sender cannot hold
  up normal clients' sends.
- Defaults: 256 queued messages/1 MiB payload, 10 s send and 3 s close deadlines.
- Cleanup unregisters the connection, clears pending data and awaits child tasks.
- Recovery queues finals then latest provisional snapshots before registration.
- [Overload handling](overload_summary.md) uses hysteresis and semantic dropping;
  unresolved overload disconnects the client for retained-history recovery.

## Measured findings

- Ubuntu `6dbd7da`: 131 unit tests passed; T1–T5 each ran three times.
- T3 normal clients: 100% live coverage and final completeness in both snapshots.
- Slow P95 live age: 28.08 s before → 13.36 s current; final completeness:
  51.32% → 100% at the cutoff.
- Current T3 still has 20 connection-error events and 380 recovered history items
  per run. Slow fresh-final ratio is only 1.32%; recovery is not real-time delivery.
- Current T3 RSS peaks: 33.38–33.42 MiB. The separate 300 s observation rises
  approximately 0.76 MiB/min after 60 s; it does not establish a plateau.

## Limits

- Before/after changes whole default versions, so it does not isolate coalescing.
- Lower-segment dropping assumes sequential input, not arbitrary interleaving.
- Oversized history may omit older finals; complete chunked recovery is absent.
- RSS excludes clients and kernel buffers; bounded queues do not prove bounded
  long-term total memory.
