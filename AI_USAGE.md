# AI / Coding-Assistant Usage

## Tools and purposes

| Tool | Documented use |
| --- | --- |
| OpenAI Codex | Drafting delivery queues/senders/lifecycle/monitoring, integration tests, code explanations, documentation and experiment/plotting helpers |
| OpenAI Codex with Google Drive/Docs tools | Drafting code-grounded report sections and inserting measured tables/figures into the shared report |

The record covers assistance documented in this project's development history.
Each member should verify that their own tool use is represented before final submission.

## Useful suggestion: isolate each client's sender

Codex suggested a bounded queue and independent asyncio sender per connection.
The producer can admit updates without awaiting a slow receiver's socket write.
Session supervision owns task cancellation, registry removal and queue cleanup.
Controlled-peer and real-loopback tests verify ordering, isolation and teardown.
The tested `6dbd7da` snapshot passed 131 tests on Ubuntu Python 3.12.3.

## Suggestion requiring correction: overly narrow coalescing

An early generated queue implementation replaced obsolete provisionals only for
the incoming caption's segment:

```python
if item.segment == segment and item.final is False and item.revision < revision:
    obsolete.append(item)
```

During review, WEN PENG-JU identified a counterexample: a full queue containing
`[s1r1, s1r2, s2r1, s2r2]` could reject `s3r1` even though retaining
`[s1r2, s2r2, s3r1]` would fit. Tests limited to the original design missed this
cross-segment case. A queue-wide correction was implemented in `3497349` with
five queue regression tests and one loopback scenario.

Later integration added `drop_older_segments=True`, which could discard a lower
segment's latest provisional while it was still active. Human review challenged
that assumption. A 108-case virtual-time experiment reproduced the loss under
interleaved updates, even with 100% eventual final completeness.

The current correction removes that flag and scans the whole queue for a higher
revision of each same segment. Only superseded provisionals are removed; finals,
opaque messages and each active segment's latest version remain protected from
coalescing. Queue regressions and a gated loopback WebSocket test cover this case.
The repeated sweep records no useful latest-state drops in the fixed cases.
These synthetic checks do not replace a full Ubuntu public-test rerun. See the
[experiment methods and limitations](experiments/overload_thresholds/README.md).

## Validation and reporting limits

- AI-generated explanations were checked against source code and concrete cases.
- Experimental numbers came from executed public tools and retained CSV/JSON,
  not generated estimates. Source hashes, commands and RSS traces are retained in
  [the evidence directory](experiments/delivery_20261103/README.md).
- The before/after T3 comparison changes whole default versions. It does not
  isolate one optimization or establish that coalescing caused the improvement.
- Slow-client reconnect errors and upward RSS growth are reported explicitly.
  Passing tests and a short memory trace do not establish complete spec compliance.
- Diagrams retain editable Mermaid/Python sources and rendered PNGs.
- Recovery batching was added after review found that initial context could
  exceed one message or the live queue. An independent expected-state oracle
  verifies all finals and latest active payloads before reporting full recovery
  time. The unchanged public T1–T5 tools were rerun on Ubuntu; the new results
  and source hashes are in [the recovery evidence](experiments/recovery_batches/README.md).
- Relative privileged commands were found to target an older experiment directory.
  Absolute paths and process/source checks corrected this; overlapping T4/RSS
  measurements were retained separately and replaced by serialized accepted runs.

## Final-aware delivery revision

- AI assisted implementation of NORMAL FIFO and DEGRADED final/control FIFO
  plus latest-active map, retaining fixed independent lane bounds. Same-segment
  latest state is preserved; provisionals do not use the final FIFO's capacity.
- Review identified that a fixed 4:1 service split would cap finals at 80% even
  when the receiver can sustain the final-only stream. A backlog override
  prioritizes finals when at least `min(4, FIFO capacity)` are pending.
- 158 tests passed on Windows and Ubuntu. Controlled scheduler cases, full
  T1–T5 reruns, complete recovery checks and one 300 s RSS run are retained in
  [the final-aware evidence](experiments/final_aware_delivery/README.md).
- Public T3 slow-reader delay/reconnects persist. The report separates these
  observations from virtual-time scheduling improvements and does not claim
  a pure priority optimization, optimal threshold or memory leak freedom.
- A capacity probe exposes the unchanged 5,000-item History cap truncating 60 s
  at high final rates; this remains a reported limitation.

## Responsibility

The team remains responsible for submitted code, results and claims. Every member
must be able to explain and modify the components attributed to them during the viva.
This document does not assert that every member has already reviewed every file.
