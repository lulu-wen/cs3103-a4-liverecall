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
- Queue statistics exclude the message in flight and transport buffers. P4's
  write-buffer monitoring and semantic overload policy are separate work.

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
The starter server has not yet been wired to these modules.
