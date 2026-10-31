# Report draft: buffering and slow-client isolation

Notes for the shared report. Delivery and initial recovery are wired together;
semantic overload handling and final-version performance evidence remain pending.

## Design

- One bounded FIFO and one sender task per client.
- The producer enqueues without waiting for network writes.
- Each sender sends accepted messages in FIFO order and yields between sends.
- Tasks share one asyncio event loop; no OS thread is created per client.
- Before registration, queue finalized HISTORY then current provisional snapshots
  without yielding. Reject the whole initial context if any admission fails.

## Limits and trade-offs

- Admission checks message count and encoded payload bytes.
- A full queue rejects new data without silently evicting old messages.
- The current server unregisters and closes that client with code 1013. This
  temporary fallback cannot guarantee final delivery without semantic recovery.
- Rejection is not delivery: overload handling must preserve finals and the
  latest useful active state, using recovery where appropriate.
- Limits exclude object overhead, the current send, transport and OS buffers.
- Send/close deadlines default to 10/3 seconds. These are design choices awaiting
  overload-policy review, not measured optimal values.
- Queue limits and deadlines can be set by CLI/env; startup logs the effective
  settings so experiment configurations can be recorded without editing code.
- Initial messages respect the client's 1 MiB receive limit. HISTORY still keeps
  only the newest context fitting its budget; complete oversized recovery remains
  a limitation rather than a final-delivery guarantee.

## Cleanup and monitoring

- Session exit removes the registry entry, clears the queue, cancels/awaits tasks
  and attempts socket close. Cleanup tolerates repeated cancellation.
- Uncertain sends are not retried locally; recovery handles resume/deduplication.
- Snapshots expose queue load, in-flight age, local send counts and transport size.
- Local send completion is not browser acknowledgement or caption completeness.

## Evidence and remaining work

- All 108 local tests passed on 31 October 2026, including twelve new recovery
  initialization tests through real loopback WebSockets.
- Three controlled-peer tests cover coalescing with an in-flight send, normal
  revision delivery and preservation of the backlog after rejected replacement.
- With 100 controlled peers and ten blocked sends, normal senders continue;
  slow queues reject at their limits and accepted data drains in order later.
- Tests check registry/task cleanup and isolation of a failed session.
- A local 20-client smoke check delivered 80/80 finals and 240/240 revisions;
  this single short Windows run is not a formal public performance result.
- Re-run Ubuntu tests, RSS observations and baseline comparison on the completed
  implementation before making final performance claims.

Use the [experiment plan](delivery_test_plan.md) to collect missing evidence.
