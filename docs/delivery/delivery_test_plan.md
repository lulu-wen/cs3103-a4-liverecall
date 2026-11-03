# Delivery tests and experiment plan

## Local checks

From the repository root (activate your virtual environment):

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -t . -v
```

The recovery export passed 141 tests on Ubuntu Python 3.12.3
with websockets 16.1.1. This does not verify every supported Python version.
The original 38 component tests use only the standard library.

| Test | Main checks |
| --- | --- |
| `tests/delivery/test_client_queue.py` | FIFO, count/byte limits, UTF-8 sizes, wakeup, close and opt-in provisional replacement |
| `tests/delivery/test_client_sender.py` | Ordered sends, isolation, deadlines, errors and cancellation |
| `tests/delivery/test_client_session.py` | Registry/task cleanup and repeated cancellation |
| `tests/delivery/test_client_monitor.py` | Counters, timing, snapshots and transport availability |
| `tests/delivery/test_client_recovery.py` | UTF-8 batch limits, full ordered snapshots and preflight rejection |
| `tests/delivery/test_client_delivery.py` | 100 controlled peers, isolation, coalescing with an in-flight send, normal revision coverage and rejected replacement |
| `tests/server/test_server_delivery.py` | Real loopback WebSockets: HISTORY-first order, two clients, monitors, disconnect/shutdown cleanup, invalid hello, source replacement, full/oversized queue isolation |
| `tests/server/test_server_config.py` | Defaults, CLI/env precedence, invalid capacities/deadlines, and startup wiring |
| `tests/captions/test_caption_state.py`, `tests/captions/test_history.py`, `tests/server/test_caption_pipeline.py` | Caption validation, bounded finals history, source-to-client integration |
| `tests/server/test_server_recovery.py` | Batched HISTORY/active/live ordering, resume filtering, UTF-8 limits, tiny live queues, window validation and source updates during recovery |

The 100-peer test checks that:

- Normal senders continue while ten sends are blocked.
- Slow queues stay within their limits and explicitly reject excess messages.
- Accepted messages drain in order when blocked sends are released.
- Disconnect removes registry entries and leaves no running child tasks.

Fake peers do not exercise TCP or recover rejected data. These are not T3
results and do not establish latency, caption completeness or RSS bounds.

Three additional controlled-peer scenarios verify the coalescing API:

- A blocked in-flight revision is sent unchanged; obsolete queued revisions are
  replaced while finals and another healthy client's progress are preserved.
- Normal mode sends HISTORY followed by every queued caption revision.
- An oversized replacement is rejected and the original backlog still drains.

These tests invoke `push_caption()` directly. Two additional loopback tests
verify server FULL retries under count and byte limits: normal clients keep all
revisions while blocked clients receive the in-flight revision followed by the
replacement final without eviction. Unresolved final backlogs still evict; failed
admission does not imply successful recovery. Pressure-based transitions are implemented; see [the policy summary](overload_summary.md).

## Local server checks completed

- Four additional edge scenarios cover revision bursts, blocked-sender churn,
  ten-second reconnect and recovery after queue-full eviction. See the
  [test index](../../tests/README.md) for groups, commands and limitations.

- `delivery/` is connected to `solution_server.py`; the required startup checker passes.
- Recovery initialization now snapshots retained state and streams bounded HISTORY
  batches, then active snapshots, before queued live updates. Loopback tests cover
  message limits, preflight failure and updates during the recovery prefix.
- A gated server-side send tests overload deterministically over real sockets;
  it is not a real TCP slow-reader measurement.
- An eight-second, 20-client smoke run with four source segments delivered
  80/80 finals and 240/240 revisions, with no duplicate/stale messages or
  connection errors. It is one Windows run, not a formal public-test result.
- A second smoke check confirmed the unchanged runner's server inherited
  512 messages, 2097152 bytes, a 5-second send timeout and a 2-second close timeout.
  Final completeness/update coverage were 100%, with zero connection errors.

```bash
python submission_check.py
python run_demo.py --clients 20 --duration 8 --source-segments 4 --runs 1 --output-stem tmp/delivery-smoke-settled --quiet-server
```

Keep clients alive long enough for source completion; a three-second exploratory
run ended receivers before all four segments finished and is not completeness evidence.

## Public measurements completed

T1–T5 each have three current-version Ubuntu runs. T3 also has three before
runs and one supplementary 300 s RSS observation. See the
[current evidence index](../../experiments/recovery_batches/README.md) for commands,
raw outputs, source hashes and limitations.

- Normal T3 clients retained 100% coverage/completeness; slow freshness remains poor.
- T4 recovered retained finals after the scheduled disconnects.
- T5 applied the supplied delay/loss profile inside an isolated network namespace.
- No bandwidth-halving, arbitrary interleaved-source or long-term memory guarantee
  follows from these measurements.

## Remaining validation

- Browser reconnect, local recall and HISTORY rendering need end-to-end review.
- Individually oversized items and count-pruned history remain recovery limits.
- Validate final-aware overload behavior and longer connection-churn/RSS behavior.
- Re-run relevant measurements if runtime code or experiment settings change.
