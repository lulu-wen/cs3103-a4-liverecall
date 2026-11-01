# Test groups

| Directory | Scope |
| --- | --- |
| `captions/` | Revision validation, immutable finals and bounded history |
| `delivery/` | Queue, sender, session cleanup, monitoring and controlled-peer integration |
| `server/` | Real loopback WebSockets, recovery, source pipeline and CLI/environment settings |

Run from the repository root with the project's Python environment:

```bash
# All tests
python -m unittest discover -s tests -t .

# One group
python -m unittest discover -s tests/delivery -t .

# One module
python -m unittest tests.server.test_server_recovery
```

Use `-t .` for discovery so `tests.delivery` and `tests.captions` do not shadow
the application packages `delivery` and `captions`.

## Additional edge scenarios

- Real loopback count/byte overflow: the server coalesces obsolete provisional
  revisions before eviction, while a healthy client receives every revision.

- 500 revisions across five segments: coalescing retains completed finals and
  the latest active captions within queue count/byte limits.
- Three waves of ten blocked senders disconnect: queues/tasks are released and
  a persistent healthy client continues receiving updates.
- Ten seconds offline, simulated with a history clock: reconnect receives only
  missing finals, the latest provisional state, then new live updates.
- Queue-full eviction over loopback: one healthy client continues, and the
  evicted client can recover two recent finals after reconnecting.

These are deterministic correctness checks, not instructor hidden tests or
public T1-T5 performance results. Controlled sends do not model TCP loss or
bandwidth precisely. Loopback recovery assumes the requested finals still fit
the retained history and initial-context budget. No RSS bound is established.
