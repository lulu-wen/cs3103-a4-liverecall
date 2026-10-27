# P3: queue and sender integration

`client_queue.py` stores bounded outgoing payloads. `client_sender.py` provides
`run_client_sender(ws, queue, send_timeout=10.0, close_timeout=3.0)` to consume
one queue and send messages in FIFO order. Both modules use only the Python
standard library and support Python 3.10+.

## Connection lifecycle

`client_session.py` now provides `run_client_session()` to register a queue,
start exactly one sender, and supervise connection shutdown. The future client
handler creates a queue and passes ownership to this function:

```python
from client_queue import ClientQueue, PushResult
from client_session import run_client_session

# Server-owned registry shared with the future producer/broadcast code.
clients = {}

async def client_handler(ws, serialized_history):
    queue = ClientQueue(max_messages=256, max_bytes=1024 * 1024)
    # P2 supplies the history, before live registration.
    if queue.push(serialized_history) is not PushResult.ACCEPTED:
        queue.close()
        await ws.close(code=1013, reason="Initial history exceeds queue capacity")
        return
    await run_client_session(ws, queue, clients)
    # Send failures propagate here; handle/log them at the handler boundary.
```

This is an integration outline, not a complete handler. P2 must coordinate
history construction and client registration to avoid missing live updates.
Capacity values are examples, not chosen evaluation parameters.

The session waits for either connection close or sender completion. Every exit
path removes its own registry entry before awaiting cleanup, closes the queue,
cancels and awaits the sender/connection waiter, and attempts connection close.
It also handles handler cancellation (including repeated cancellation during
cleanup). A replacement registry entry is not removed by the older session.
Duplicate registrations and closed queues are rejected before ownership transfer.

Calling an async function does not start it: the caller owns resources until
`run_client_session()` starts executing. If a newly created session task is
cancelled before ever starting, its caller must close the connection/queue.

## Behavior and responsibilities

- The producer checks every `PushResult`. Rejected payloads are not delivered;
  P4 must decide the overload response, particularly for final captions.
- The sender sends one payload at a time, with a configurable send deadline.
  It yields between sends so a busy connection does not monopolize the loop.
- Closing an empty queue wakes the sender. Queue close discards pending data;
  it does not interrupt a send already in flight. Cancel and await the sender
  when tearing down the connection.
- Timeout requests connection close with code 1013; other send errors use 1011.
  Sender exit closes the queue and attempts a bounded connection close.
- Send exceptions and cancellation propagate to the task owner. Close failures
  are logged without replacing the original send exception.
- Failed sends are not retried: the peer may already have received some data.
  P2 owns reconnect/recovery and deduplication. Queue teardown alone does not
  guarantee recovery or preservation of finals.
- Queue statistics exclude the message in flight and transport buffers. The
  optional monitor below exposes these separately. P4 owns overload decisions.

## Monitoring (P4 interface)

Create one `ClientMonitor` per connection, pass it to either the session or a
standalone sender, and retain it while that connection is active:

```python
from client_monitor import ClientMonitor

monitor = ClientMonitor()
# Within a monitor/policy callback running on the same event loop:
stats = monitor.snapshot(queue, ws)
print(stats.queue.depth, stats.queue.oldest_pending_seconds)
print(stats.in_flight, stats.in_flight_seconds, stats.write_buffer_bytes)

# In the client handler, pass the same monitor to the session:
await run_client_session(ws, queue, clients, monitor=monitor)
```

Read snapshots from the producer/policy callback while the session runs; the
session call itself waits until disconnect. Pass the matching queue/connection
to `snapshot()`, and release any external monitor registry entry on disconnect.
The monitor itself creates no tasks and retains no connection/payload references
or sample history. Existing calls without `monitor=` still work.

| Field | Meaning |
| --- | --- |
| `queue.depth` | Messages still waiting in the application queue |
| `queue.pending_bytes` | Encoded payload bytes still in that queue |
| `queue.oldest_pending_seconds` | Age of the oldest queued message; 0 when empty |
| `queue.closed` | Whether the queue has been closed |
| `in_flight` | Whether the sender is currently attempting a send |
| `in_flight_bytes` | Payload size of that attempt (0 when idle) |
| `in_flight_seconds` | Time since the current send attempt began; excludes queue waiting |
| `sent_messages` | Number of successful local `send()` completions |
| `sent_payload_bytes` | Sum of their UTF-8 text/binary payload bytes |
| `send_errors` | Failed send attempts, excluding timeouts and cancellation |
| `send_timeouts` | Send attempts exceeding the configured deadline |
| `last_send_seconds` | Duration of the last finished attempt, including failed/cancelled attempts; initially `None` |
| `write_buffer_bytes` | Current asyncio transport output buffer size; `None` if unavailable |

Snapshots are immutable. Timing uses a monotonic clock. Successful `send()`
does not acknowledge receipt or rendering by the browser, so these counters
are not caption completeness/freshness measurements. An empty payload may be
in flight with 0 bytes; check `in_flight` rather than bytes alone.

Transport size is read through `ws.transport.get_write_buffer_size()` when
available ([asyncio transport API](https://docs.python.org/3/library/asyncio-protocol.html#asyncio.WriteTransport.get_write_buffer_size)).
It excludes OS TCP buffers and total process memory. Unknown is different from
zero; do not add transport bytes to in-flight bytes as an exact memory total,
because they may represent overlapping data and different encodings/framing.
Monitoring introduces no automatic dropping, lag threshold or disconnect policy.

## Verification

```bash
python -m unittest discover -s tests -v
```

Sender tests use controlled fake connections to check FIFO, slow-client
isolation, send deadlines, failures, cancellation, and cleanup deadlines.
Session tests cover registry removal, blocked-send cancellation, empty queues,
queue close, sender failures/timeouts, repeated cancellation, stuck close,
replacement/duplicate registrations, and 25 simultaneous disconnects. They check
that no child tasks are left running after each completed session test.
These do not replace a real WebSocket integration test or Ubuntu T3 runs.
Monitor tests cover UTF-8 sizes, deterministic timing, read-only snapshots,
unknown transport data, blocked sends with empty queues, successful sends,
separate error/timeout counters, cancellation, and session integration.
The starter server has not yet been wired to these modules.
