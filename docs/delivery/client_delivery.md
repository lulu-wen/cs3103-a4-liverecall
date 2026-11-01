# Per-client delivery

Each client has its own queue and sender. A slow send can wait without blocking
other senders. `solution_server.py` uses them together with caption revision
state and recovery. Semantic overload handling remains incomplete.

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
- `broadcast_to_queues()` calls `push_caption()` without waiting on socket writes.
  Normal admission preserves every revision. FULL retries once with coalescing;
  other failures do not trigger a replacement attempt.
- `get_client_stats(ws)` returns an active client's monitoring snapshot.
- HISTORY and the latest active provisional updates are queued before registration.
- Default queue caps: 256 messages and 1 MiB of encoded payloads, both provisional.
- Unresolved admission failure unregisters the client and starts one tracked close task
  (code 1013). The handler awaits/removes that task and its monitor on exit.
- This is a temporary fail-fast fallback, not final-preserving overload handling.

## Caption coalescing API

`push()` remains an opaque FIFO. Policy code can use `push_caption()` with
validated caption metadata:

```python
result = queue.push_caption(
    event.raw, segment=event.segment, revision=event.revision,
    final=event.final, coalesce=degraded,
)
```

- `degraded` is chosen by the overload policy; the queue does not detect overload.
- With `coalesce=False` (default), every revision is queued normally.
- With `coalesce=True`, admission scans all queued captions plus the incoming
  caption for each segment's highest revision. It removes queued provisionals
  with a lower revision, even for segments unrelated to the incoming caption.
- Finals, HISTORY, each segment's latest state and in-flight sends are preserved.
  Remaining messages retain their order; the incoming update is appended.
- If admission still fails, the entire old backlog stays unchanged. Handle the
  result explicitly; coalescing does not guarantee final delivery or recovery.
- Metadata must match the payload and come from validated caption state.
- Current live broadcasts use this API and enable coalescing on a FULL retry.
  Lag-based degraded-mode transitions remain pending. Initial context still uses
  `push()`, so these opaque initial messages are not replacement candidates.
- A coalescing push scans the bounded queue; normal pushes remain constant-time
  apart from UTF-8 byte counting.

## Recovery initialization

`handle_client()` in `solution_server.py` prepares this FIFO before starting the sender:

```text
HISTORY (finals only) -> latest active provisional updates -> later live updates
```

- Resume cursors must be non-negative integers (not booleans).
- History seconds must be a non-negative finite number; values above 60 are
  capped at 60. Omitted fields default to cursor 0/0 and 30 seconds.
- Invalid recovery fields close with code 1008 before creating delivery resources.
- Each initial message must fit the client's 1 MiB limit, even with a larger queue.
- The HISTORY budget is `min(queue byte limit, 1 MiB)`. Existing history selection
  keeps newer context if older items do not fit; this is not chunked recovery.
- Every initial push must succeed. If the combined count/bytes cannot fit, clear
  the queue and close with 1013 before registration or sending any partial context.
- There is no await from context selection through successful registration;
  source updates cannot interleave and create a history-to-live gap.
- This does not guarantee all missed finals when history exceeds the byte budget;
  semantic overload/recovery policy still needs to address that case.

## Lifecycle rules

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
