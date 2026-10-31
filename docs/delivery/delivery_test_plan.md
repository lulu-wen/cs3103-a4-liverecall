# Delivery tests and experiment plan

## Local checks

From the repository root (activate your virtual environment):

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

All 104 tests passed on Windows on 31 October 2026 with websockets 16.1.1.
The original 38 component tests still use only the standard library.
The code targets Python 3.10+;
that runtime still needs verification on the team's Ubuntu environment.

| Test | Main checks |
| --- | --- |
| `test_client_queue.py` | FIFO, count/byte limits, UTF-8 sizes, wakeup, close and opt-in provisional replacement |
| `test_client_sender.py` | Ordered sends, isolation, deadlines, errors and cancellation |
| `test_client_session.py` | Registry/task cleanup and repeated cancellation |
| `test_client_monitor.py` | Counters, timing, snapshots and transport availability |
| `test_client_delivery.py` | 100 controlled peers, isolation, coalescing with an in-flight send, normal revision coverage and rejected replacement |
| `test_server_delivery.py` | Real loopback WebSockets: HISTORY-first order, two clients, monitors, disconnect/shutdown cleanup, invalid hello, source replacement, full/oversized queue isolation |
| `test_server_config.py` | Defaults, CLI/env precedence, invalid capacities/deadlines, and startup wiring |
| `test_caption_state.py`, `test_history.py`, `test_caption_pipeline.py` | Caption validation, bounded finals history, source-to-client integration |
| `test_server_recovery.py` | History/active/live ordering, resume filtering, UTF-8/message limits, initial capacity rejection, window validation and join during source updates |

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

These tests invoke `push_caption()` directly. The server overload policy is
not wired yet, and failed admission does not imply successful recovery.

## Local server checks completed

- `delivery/` is connected to `solution_server.py`; the required startup checker passes.
- Recovery initialization now queues HISTORY followed by the latest active
  provisional updates. Twelve new loopback tests cover ordering, message limits,
  count/byte overflow without partial delivery, and invalid recovery requests.
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

## Integration still needed

- Validate browser reconnect, local recall and HISTORY merge end to end.
- Integrate semantic overload handling and recovery beyond a single HISTORY budget.
- Verify final-caption preservation during overload/reconnect.

## Ubuntu T3 (not run yet)

For formal Ubuntu measurements, use the unchanged public harness:

```bash
python run_demo.py --clients 100 --duration 60 --runs 3 --slow-percent 10 --slow-ms 500 --output-stem t3
```

- Keep all run outputs and report medians.
- Compare normal/slow clients separately: P50/P95/P99, freshness, completeness
  and update coverage.
- Observe server RSS in another terminal:

```bash
python monitor_server.py --pid <SERVER_PID> --duration 60 --output t3_memory_run1.csv
```

- Restart the observer for each new PID: the runner restarts the server between
  runs. Use a different output filename each time.
- Inspect queue limits, in-flight age and transport buffers too. Queue limits
  alone do not prove bounded process memory.

## Before/after comparison (not measured yet)

- Compare frozen `baseline` with the integrated implementation on the same VM.
- Keep workload/measurement logic unchanged; use at least three runs each.
- Record commit IDs, VM resources, versions, queue limits and timeouts.
- Use [CLI or environment settings](client_delivery.md#experiment-settings) and
  retain the server's startup settings line. The public runner inherits env values.
- Report medians and correctness alongside performance. Explain trade-offs.
