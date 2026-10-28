# Per-client delivery

Each client has its own queue and sender. A slow send can wait without blocking
other senders. `solution_server.py` now uses these modules; revision/recovery and
semantic overload behavior are still incomplete.

## Modules

| Module in `delivery/` | Job |
| --- | --- |
| `client_queue.py` | Store outgoing messages with count and byte limits |
| `client_sender.py` | Send one queue's messages in order |
| `client_session.py` | Register the client, supervise tasks and clean up |
| `client_monitor.py` | Report queue, send and transport-buffer state |

## Assignment requirements covered

| Spec | Support and remaining work |
| --- | --- |
| Section 3, #9: Concurrent clients | Separate queues/tasks; local WebSocket checks pass; Ubuntu scale tests pending |
| Section 3, #10: Slow-client isolation | Integrated bounded queues and independent sends; Ubuntu stress tests pending |
| Section 3, #11 and section 4.3: Graceful overload | Admission results and monitoring; semantic overload policy pending |
| Section 8, T3: Slow clients | Local tests ready; Ubuntu measurements pending |
| Section 10: Boundedness | Queue limits and cleanup; total memory needs measurement |

Server recovery history (section 3, #8) is separate from outgoing queues.

## Connect a client

Current server wiring:

- `CLIENTS` maps sockets to queues; `MONITORS` holds each connection's monitor.
- `broadcast_to_queues()` calls `push()` without waiting on socket writes.
- `get_client_stats(ws)` returns an active client's monitoring snapshot.
- Empty HISTORY is queued before registration; real history/recovery is pending.
- Default queue caps: 256 messages and 1 MiB of encoded payloads, both provisional.
- Rejected admission unregisters the client and starts one tracked close task
  (code 1013). The handler awaits/removes that task and its monitor on exit.
- This is a temporary fail-fast fallback, not final-preserving overload handling.

Simplified lifecycle example, from the repository root:

```python
from delivery.client_queue import ClientQueue, PushResult
from delivery.client_monitor import ClientMonitor
from delivery.client_session import run_client_session

clients = {}  # Shared with future broadcast code.

async def client_handler(ws, serialized_history):
    queue = ClientQueue(max_messages=256, max_bytes=1024 * 1024)
    if queue.push(serialized_history) is not PushResult.ACCEPTED:
        queue.close()
        await ws.close(code=1013, reason="Initial history exceeds queue capacity")
        return
    monitor = ClientMonitor()
    await run_client_session(ws, queue, clients, monitor=monitor)
```

- Queue sizes above are examples, not tuned settings.
- Recovery must coordinate HISTORY and registration so live updates are not missed.
- Producers check every `push()` result: `ACCEPTED`, `FULL`, `TOO_LARGE` or
  `CLOSED`. Rejected messages are not delivered.
- On exit, the session unregisters the client, clears the queue, cancels/awaits
  its tasks and attempts socket close.
- Send failures propagate to the handler. Reconnect recovery handles uncertain
  delivery and duplicates; failed sends are not retried locally.
- Closing a queue discards pending data. This alone does not preserve finals.
- The caller owns resources until the session starts, including cleanup if a
  task is cancelled before its first execution.

## Experiment settings

Configure the server at startup; no code edits are needed:

| CLI option | Environment variable | Default |
| --- | --- | --- |
| `--queue-max-messages` | `LIVERECALL_QUEUE_MAX_MESSAGES` | 256 messages/client |
| `--queue-max-bytes` | `LIVERECALL_QUEUE_MAX_BYTES` | 1048576 payload bytes/client |
| `--send-timeout` | `LIVERECALL_SEND_TIMEOUT` | 10 seconds/attempt |
| `--close-timeout` | `LIVERECALL_CLOSE_TIMEOUT` | 3 seconds/attempt |

- CLI values override environment values; otherwise the original defaults apply.
- Limits must be positive integers; deadlines must be positive finite seconds.
- Invalid settings fail before startup. A byte limit too small for initial
  HISTORY will cause clients to be rejected, even though the value is positive.
- The server prints effective settings at startup. Keep them with experiment results.
- `python solution_server.py --help` lists all options.

The public runner starts its own server and only passes `--port`. Use inherited
environment variables to configure it without changing instructor test code.

Ubuntu (variables apply only to this command):

```bash
env LIVERECALL_QUEUE_MAX_MESSAGES=512 LIVERECALL_SEND_TIMEOUT=5 python run_demo.py --clients 100 --duration 60 --runs 3 --slow-percent 10 --slow-ms 500 --output-stem t3_q512
```

PowerShell (clear the variables afterward):

```powershell
$env:LIVERECALL_QUEUE_MAX_MESSAGES = "512"
$env:LIVERECALL_SEND_TIMEOUT = "5"
python run_demo.py --clients 20 --duration 20 --runs 1 --output-stem local_q512
Remove-Item Env:LIVERECALL_QUEUE_MAX_MESSAGES, Env:LIVERECALL_SEND_TIMEOUT
```

Use a distinct output stem per configuration. PowerShell runs are development
checks; formal performance measurements still require Ubuntu.

## Deadline behavior

- `send_timeout=10.0`: maximum wait for one send; timeout closes with code 1013.
- `close_timeout=3.0`: limit for each close attempt.
- Other send errors close with code 1011. Errors/cancellation propagate.
- These are configurable choices, not spec-mandated values. No zero/disabled
  timeout mode is provided.

## Monitoring

During a session, a policy callback can call `monitor.snapshot(queue, ws)`.
Use one monitor per connection with the matching queue/socket.

| Snapshot fields | Meaning |
| --- | --- |
| `queue.depth`, `queue.pending_bytes` | Queued message count and UTF-8 payload bytes |
| `queue.oldest_pending_seconds`, `queue.closed` | Oldest queued age; whether queue is closed |
| `in_flight`, `in_flight_bytes`, `in_flight_seconds` | Current send attempt, full payload size and elapsed time |
| `sent_messages`, `sent_payload_bytes` | Successful local send completions and payload bytes |
| `send_errors`, `send_timeouts` | Separate error/timeout counts; cancellation counts as neither |
| `last_send_seconds` | Last attempt duration, including failures/cancellation; initially `None` |
| `write_buffer_bytes` | Asyncio output-buffer size; `None` if unavailable |

- Snapshots are immutable. Timing uses a monotonic clock.
- Queue age excludes the current send. In-flight bytes are not remaining bytes.
- Successful `send()` does not confirm browser receipt or rendering.
- Transport size excludes OS TCP buffers and data in the network. Do not add it
  to in-flight bytes as an exact memory total; data may overlap.
- Monitoring stores no sample history and chooses no overload policy.
- Remove entries from any external monitor registry on disconnect.

See the [test plan](delivery_test_plan.md), [report draft](buffering_report_draft.md)
and [submission drafts](delivery_submission_drafts.md).
