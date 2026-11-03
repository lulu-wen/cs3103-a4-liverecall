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

Later shared integration changed the policy again: current code can discard
lower-numbered segments' provisionals when `drop_older_segments=True`. This
assumes the supplied source completes segments sequentially; it must not be
described as preserving arbitrary interleaved active segments. The historical
correction and the current implementation are distinct. Human counterexamples
remain necessary even when generated tests pass.

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

## Responsibility

The team remains responsible for submitted code, results and claims. Every member
must be able to explain and modify the components attributed to them during the viva.
This document does not assert that every member has already reviewed every file.
