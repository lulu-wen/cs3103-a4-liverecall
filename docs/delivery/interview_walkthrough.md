# Interview walkthrough

This describes the current implementation, including unfinished overload policy.
Related tests live under `tests/captions/`, `tests/delivery/` and `tests/server/`.

## 1. Why can an older revision never overwrite a newer one?

Path: `solution_server.handle_source` -> `CaptionState.apply` / `_decide`
-> broadcast -> browser `applyCaption` -> `acceptUpdate` -> render.

- The server rejects equal/older revisions of an active segment and all changes
  after finalization. Only accepted updates enter history or delivery queues.
- The browser independently rejects duplicate/older revisions and updates to
  an already finalized segment. HISTORY uses this same acceptance path.
- Rendering changes the existing segment line, rather than appending a revision.
- Queue metadata is supplied by validated state; `push_caption()` itself does
  not validate the ordering of the whole caption stream.

## 2. What happens after ten seconds offline?

Path: browser close handler -> three-second reconnect attempt -> CLIENT_HELLO
-> server `handle_client` -> `History.since` -> `history_message`
-> `CaptionState.active_snapshot` -> `run_client_session` -> sender.

- The browser retains its latest accepted final cursor in page memory. It sends
  that cursor and requests 60 seconds of context on reconnect.
- The server selects recent finals beyond that cursor, oldest first, and queues
  HISTORY followed by the latest provisional for each active segment.
- Initial admission and registration have no intervening await, so subsequent
  live updates cannot slip into a history-to-live gap.
- Browser HISTORY entries go through `applyCaption`, preventing duplicate finals.
- This is same-page recovery. Refresh resets the in-memory cursor; sessionStorage
  currently retains the client ID, not the caption state.
- Retention, queue capacity and the HISTORY byte budget still limit recovery.
  Oversized HISTORY is truncated to newer context, not split into multiple frames.

## 3. Where is per-client buffering, and how is it bounded?

Path: `handle_client` creates `ClientQueue` -> CLIENTS maps each socket to its
queue -> producer pushes -> that client's sender calls `get()` then `ws.send()`.

- Defaults: 256 pending messages and 1 MiB encoded payload bytes per client.
- CLI/environment settings configure these caps. Either cap can reject admission.
- One independent sender task per connection prevents a socket write from blocking
  broadcasts to all other clients.
- These caps exclude object overhead, the in-flight payload, transport buffers
  and kernel TCP buffers. They do not prove bounded total server RSS.

## 4. Why do P95/P99 increase under packet loss despite TCP reliability?

- TCP preserves ordered bytes and retransmits lost data; reliability does not
  impose a delivery deadline.
- Later bytes can wait for missing earlier bytes: head-of-line blocking.
- Loss recovery and congestion control can reduce throughput, causing additional
  waiting in transport/application queues. Tail percentiles reflect slow samples.
- Trace this through sender `await ws.send()` and `ClientMonitor.snapshot()`:
  pending age, in-flight time and transport-buffer size describe different stages.
- A successful local send is not confirmation of client rendering.
- Use Ubuntu T5/netem and actual measurements to establish the effect; these new
  functional tests do not simulate packet loss.

## 5. What if a client consumes more slowly than captions arrive?

Path: blocked/slower send -> transport/backlog growth -> queue admission result
-> `broadcast_to_queues` -> unregister -> `close_overloaded_client`.

- Current server behavior: FULL first retries admission with queue-wide
  provisional coalescing. Unresolved failure unregisters that client and schedules
  a bounded close attempt with code 1013. Other clients continue independently.
- Session cleanup closes the queue, cancels/awaits its tasks and removes registry
  ownership. The handler removes MONITORS and awaits/removes EVICTIONS.
- Reconnect may recover recent finals from server history, which is updated before
  broadcasting. Eviction alone does not guarantee final completeness.

## 6. Which provisional revisions can safely be dropped?

Path: `ClientQueue.push_caption(..., coalesce=True)` -> `_push`.

- Scan the entire queue plus the incoming caption for each segment's highest
  revision. Remove a queued non-final only if a higher revision of that same
  segment exists in this candidate batch, even if the incoming segment differs.
- Preserve existing finals, HISTORY, other segments' latest state and in-flight
  messages. Reject admission without changing the backlog if replacement cannot fit.
- Normal mode keeps every revision. Coalescing must be enabled explicitly by policy.
- Server broadcasts use `push_caption()` normally, enabling coalescing only on
  FULL. Initial HISTORY/snapshots remain opaque `push()` messages.

## 7. What if bandwidth is cut in half?

- If available capacity remains above offered traffic, queues may remain stable.
  Otherwise delay/backlog tends to grow until a cap or timeout is reached.
- Current server behavior is the bounded FIFO with coalescing on FULL described
  above, then fail-fast eviction if still necessary. It has no lag-based mode.
- Intended next step: use monitoring/lag signals to enable coalescing, preserving
  finals/latest active state; use explicit recovery when admission remains impossible.
- Doubling the queue postpones overflow; it does not create network capacity.
- Validate on Ubuntu with bandwidth control; do not treat blocked fake sends as
  a measured half-bandwidth experiment.

## 8. Show the overload/backpressure policy code.

| Responsibility | Code |
| --- | --- |
| Count/byte admission and optional coalescing | `delivery/client_queue.py`: `_push`, `push_caption` |
| Local send deadline and bounded close | `delivery/client_sender.py`: `run_client_sender` |
| Queue/send/transport observations | `delivery/client_monitor.py`: `snapshot` |
| Current rejection/eviction fallback | `solution_server.py`: `broadcast_to_queues`, `close_overloaded_client` |
| Cleanup and task ownership | `delivery/client_session.py`: `run_client_session` |
| Missing piece | Automatic overload detection, degraded-mode transitions and final-preserving recovery policy |

Coalescing on FULL is integrated. Lag-based mode transitions and complete
final-preserving overload recovery are still unfinished.

## 9. Explain a surprising experimental result.

Development evidence from GB10 Ubuntu on 28 October 2026, commit `f2d125a`:

- Three T3 runs: normal clients had median P95 3.712 ms and 100% final completeness.
- Slow clients had median P95 about 28.071 seconds and 51.316% final completeness
  at the measurement cutoff, despite zero recorded connection errors.
- Interpretation: isolation can keep healthy clients responsive while slow clients
  remain badly behind. A live connection and reliable transport do not establish
  freshness or completeness within a finite experiment.
- These data alone do not establish queue overflow or that finals were discarded.
- These are older development results, not measurements of the current implementation.
  Re-run formal experiments on the completed version before claiming improvement.
