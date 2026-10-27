# P3: queue and sender integration

`client_queue.py` stores bounded outgoing payloads. `client_sender.py` provides
`run_client_sender(ws, queue, send_timeout=10.0, close_timeout=3.0)` to consume
one queue and send messages in FIFO order. Both modules use only the Python
standard library and support Python 3.10+.

## Connection lifecycle

The future client handler should create one queue and exactly one sender task
for each connection. Keep a reference to both in the client registry.

```python
queue = ClientQueue(max_messages=256, max_bytes=1024 * 1024)
sender = asyncio.create_task(run_client_sender(ws, queue))
try:
    # P2 enqueues HISTORY before registering the client for live updates.
    # The source handler subsequently calls queue.push(serialized_message).
    await ws.wait_closed()
finally:
    # Remove the client from the registry here before releasing its resources.
    queue.close()
    sender.cancel()
    try:
        await sender
    except asyncio.CancelledError:
        pass
    # Handle/log send errors here according to the handler's error policy.
```

This is an integration outline, not a complete handler. P2 must coordinate
history construction and client registration to avoid missing live updates.
Capacity values are examples, not chosen evaluation parameters.

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
These do not replace a real WebSocket integration test or Ubuntu T3 runs.
The starter server has not yet been wired to these modules.
