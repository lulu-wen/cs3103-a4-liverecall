# Submission documentation drafts

Notes to merge into final root-level documents; these do not replace them.

## README notes

- Modules live in `delivery/`; import through the package:
  `from delivery.client_queue import ClientQueue`.
- Entry point: `python solution_server.py --port 8765`.
- Public runs exercise revision state, history and the integrated delivery path;
  semantic overload handling remains incomplete.
- See [interfaces](client_delivery.md), [tests](delivery_test_plan.md) and the
  [project checklist](../project_checklist.md).

## AI_USAGE notes

- **Tool:** OpenAI Codex, for code/test/documentation drafts and explanations
  of asyncio, resource ownership and monitoring.
- **Useful suggestion:** separate bounded queues from independent senders.
  Local tests check ordering, limits, slow-client isolation and cleanup.
- **Modified suggestion:** replace `asyncio.timeout()` (Python 3.11+) with
  `asyncio.wait_for()` to target Python 3.10+. Ubuntu validation remains pending.
- **Design caveat:** the generated 10-second send deadline is not a spec
  requirement. It remains enabled; disconnection alone does not preserve finals.
- Add other members' tools/examples and review all claims before submission.
  Do not claim team review or understanding before it occurs.

### AI-generated design corrected after human review

- **Initial suggestion:** on a full queue, remove obsolete provisional revisions
  only for the incoming caption's segment:

  ```python
  if item.segment == segment and item.final is False and item.revision < revision:
      obsolete.append(item)
  ```

- **Why it was incomplete:** a new segment could be rejected even when other
  segments had redundant provisional revisions. For example, a four-message
  queue `[s1r1, s1r2, s2r1, s2r2]` could reject `s3r1` and trigger eviction,
  although retaining `[s1r2, s2r2, s3r1]` would fit. This was a design limitation,
  not evidence of a measured production outage or final-caption loss.
- **Human review:** Wen identified this case while walking through the generated
  code and asked why full-queue cleanup ignored other segments.
- **Correction:** scan the queue and incoming caption for each segment's highest
  revision, then remove queued provisionals superseded by a higher revision of
  the same segment. Preserve all finals, opaque messages, each segment's latest
  state, survivor order/timestamps and in-flight sends. Apply changes only when
  the complete admission fits; otherwise leave the original queue unchanged.
- **Validation:** five new queue tests and one loopback test cover cross-segment
  reclamation, queued finals, atomic rejection, UTF-8 bytes and survivor age.
  All 116 local tests passed on 1 November 2026. Ubuntu performance improvement
  has not been measured for this change.
- **Evidence:** fix commit `3497349`, merged in
  [PR #8](https://github.com/lulu-wen/cs3103-a4-liverecall/pull/8).
- **Lesson:** tests built around the original AI-generated design can all pass
  while missing a broader overload case. Human counterexamples are needed to
  challenge assumptions; passing tests alone does not establish spec completeness.

## CONTRIBUTIONS notes

- **Wen Penj-Ju / A0342654A:** to fill.
- **Work:** bounded queue, sender, cleanup, monitor and independent tests.
- **Writing:** buffering/isolation report section and documentation compilation.
- **Work added:** server wiring and real loopback WebSocket regression tests.
- **Work added:** recovery-to-delivery initialization, active snapshots, initial
  message/capacity limits and twelve loopback regression tests.
- **Pending:** oversized recovery/semantic overload policy, final-version Ubuntu
  T3/RSS experiments and analysis.
- **Contribution percentage:** team to agree; all members' values must total 100%.
- Record shared work; do not infer contribution shares from commit counts.

Git evidence: `af666c6` (queue), `35a5b24` (sender), `f017c55` (cleanup),
`bee8934` (monitor), `766a93e` (layout), `0376733` (integration tests).
