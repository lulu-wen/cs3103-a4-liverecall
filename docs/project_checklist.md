# Project checklist

Source: assignment specification, last updated 17 October 2026. Section numbers
below refer to that document. Check an item only after its behavior is integrated
and verified in the submitted application. Unchecked items include functionality
that exists only in the starter or in independently tested modules.

## Mandatory application functionality (section 3)

- [ ] **1. Live captions:** continuously display incoming captions and visually
  distinguish provisional (`final=false`) from finalized (`final=true`) captions.
- [ ] **2. Revision replacement:** replace the existing line for a segment with
  its newer revision instead of appending a duplicate line.
- [ ] **3. What did I miss?:** retain at least 60 seconds of finalized captions
  locally; default to the previous 30 seconds and offer 15/30/60-second views.
  Normal recall uses local data; request server recovery only for genuinely
  missed data, such as after disconnect or late join.
- [ ] **4. Automatic reconnect:** reconnect automatically after connection loss
  and display the connection state to the user.
- [ ] **5. Incremental recovery:** recover recent missed finalized captions after
  a short disconnect without replaying the entire lecture.
- [ ] **6. Late join:** obtain recent context when joining an ongoing lecture,
  then continue receiving live updates.
- [ ] **7. No duplicate finals:** do not display finalized captions twice after
  reconnect/recovery.
- [ ] **8. Bounded history:** retain sufficient bounded server history for up to
  60 seconds of finalized-caption recovery; avoid unbounded transcripts/backlogs.
- [ ] **9. Concurrent clients:** support many simultaneous clients; public tests
  use up to 100 and hidden evaluation may use substantially more.
- [ ] **10. Slow-client isolation:** a slow receiver must not cause unbounded
  delay or memory growth for normal clients.
- [ ] **11. Graceful overload:** obsolete provisional revisions may be coalesced
  or discarded, but preserve and correctly deliver every completed segment's
  final caption and preserve the newest relevant revision of active segments.

## Protocol and state correctness (section 4)

- [ ] Accept WebSocket `SOURCE_HELLO` with `session`; support the required
  one-source, one-session (`L1`) evaluation path.
- [ ] Preserve `CAPTION_UPDATE` fields and meanings: `type`, `session`, `segment`,
  `revision`, `source_ts`, `final`, and `text`. Do not assume a fixed revision
  count per segment; older revisions must never overwrite finalized state.
- [ ] Ignore unrecognized source fields and continue normal operation;
  `SOURCE_DONE` may be ignored after earlier updates are processed.
- [ ] Handle `CLIENT_HELLO` fields: `client_id`, `session`, `last_segment`,
  `last_revision`, and `history_seconds`. Resume progress identifies finalized
  state; a new client uses segment/revision `0/0`.
- [ ] Return `HISTORY` containing zero or more finalized `CAPTION_UPDATE` items,
  oldest first, excluding finals already reported by the client's resume state;
  support requested recovery context up to 60 seconds.
- [ ] Send live `CAPTION_UPDATE` messages; never display an older revision after
  accepting a newer revision of the same segment.
- [ ] Deliver the full revision sequence under normal conditions. Activate
  semantic suppression in response to overload and resume full-update behavior
  when the client/network catches up (sections 4.3 and 10).

## Bounded delivery under stress (sections 3, 4.3 and 10)

- [x] Connect bounded per-client queues, senders, cleanup and monitoring to
  `solution_server.py`; local WebSocket checks pass. Formal Ubuntu isolation
  and memory validation remain pending under the stress requirements below.
- [ ] Handle slow readers and constrained bandwidth without indefinitely queuing
  every revision; preserve finals and the latest useful active state.
- [ ] Converge promptly to current useful state after overload or disconnection
  ends, without first draining an obsolete provisional backlog.
- [ ] Verify bounded history, queues and memory behavior under stress; eventual
  delivery through unbounded resource growth is not acceptable.

## Repository, startup and Ubuntu compatibility (sections 5 and 11)

- [ ] Keep required paths at the repository root: `solution_server.py`,
  `requirements.txt`, `client/`, `README.md`, `AI_USAGE.md`, `CONTRIBUTIONS.md`,
  and `report.pdf`. Extra packages such as `delivery/`, `tests/` and `docs/`
  are allowed with documented non-interactive Ubuntu setup.
- [ ] Support `python solution_server.py --port <integer>`, including port 8765;
  accept clients at `ws://127.0.0.1:<port>` and start without interactive input,
  ready within 10 seconds on the evaluation VM.
- [ ] List all mandatory server Python dependencies in `requirements.txt`;
  require no cloud API, external Internet service or external database at runtime.
- [ ] Keep optional bonus dependencies separate from mandatory install/startup.
- [ ] Preserve instructor test programs/measurement logic and avoid assumptions
  about hidden timing or workload parameters.
- [ ] Use Ubuntu Linux for reproducible networking/performance experiments;
  install `python3`, `python3-venv`, `python3-pip`, and `iproute2`.
- [ ] Run the server and simulated clients in the same Ubuntu environment for
  the specified experiments and apply `tc/netem` to loopback as appropriate.
  WSL is not the reference grading environment; use a full Ubuntu VM if behavior
  differs. Multipass and binding to `0.0.0.0` are suggestions, not requirements.

## Mandatory deterministic application demo (section 6)

- [ ] Use the supplied `caption_source.py` and the same submitted server/browser
  client used for evaluation; do not substitute a separate mock-up.
- [ ] Show at least two browser clients receiving the running caption stream.
- [ ] Show provisional-to-final replacement without duplicate lines.
- [ ] Show default 30-second recall and selectable 15/30/60-second windows.
- [ ] Show a temporary disconnect, automatic reconnect and incremental recovery
  without duplicate final captions.
- [ ] Show a late-joining client obtaining context and continuing with live updates.

## Public experiments and measurements (sections 8 and 9)

Run each public test at least three independent times and report the median.
Use the unchanged public harness; commands are in [PUBLIC_TESTS.md](../PUBLIC_TESTS.md).

- [ ] **T1 - Basic correctness:** 20 clients on a normal local network; check
  revisions/finals and absence of stale/duplicate state.
- [ ] **T2 - Public scale:** 100 clients on a normal local network; measure
  Caption Age percentiles and completeness.
- [ ] **T3 - Slow clients:** 100 clients, 10% slow readers; demonstrate normal-client
  responsiveness and bounded resource usage.
- [ ] **T4 - Reconnect:** 100 clients, 20% temporarily disconnected; verify
  incremental recovery and no duplicate finals.
- [ ] **T5 - Network impairment:** use Ubuntu `tc/netem` delay/jitter/loss and
  explain TCP/WebSocket latency and tail behavior.
- [ ] Compare at least one public stress test before/after a major optimization
  or against an earlier/basic configuration; quantify changes and trade-offs.
- [ ] Report P50/P95/P99 Caption Age (`receive_time - source_ts`), Final Caption
  Completeness, Fresh Caption Ratio, Fresh Final Caption Ratio, Normal Update
  Coverage, recovery time and slow-client isolation. The default public freshness
  threshold is 500 ms; browser rendering delay is not part of Caption Age.

## Final deliverables and report (sections 12 and 14)

- [ ] Complete this README with exact Ubuntu setup, dependency installation,
  server/browser startup and public deterministic test instructions; use
  [README_TEMPLATE.md](../README_TEMPLATE.md) as a guide.
- [ ] Create root-level `AI_USAGE.md`: tools/purposes, one useful AI suggestion,
  and at least one incorrect/inefficient/modified generated suggestion or code
  fragment with a critical explanation. See [AI_USAGE_TEMPLATE.md](../AI_USAGE_TEMPLATE.md).
- [ ] Create root-level `CONTRIBUTIONS.md`: named responsibilities, components,
  experiments/report sections and agreed contribution percentages per member.
  See [CONTRIBUTIONS_TEMPLATE.md](../CONTRIBUTIONS_TEMPLATE.md).
- [ ] Complete `report.pdf`: 4-5 pages excluding references and appendices.
- [ ] Include architecture/protocol, revision/recovery semantics and
  backpressure/overload policy in the report.
- [ ] Include public experiments, at least one before/after optimization comparison,
  latency/freshness/final-completeness results and their analysis.
- [ ] Discuss failures, trade-offs, limitations and a short future-work/FYP direction.
- [ ] Separately submit the compulsory 5-7 minute deterministic application demo
  video, including revision replacement, recall and disconnect/reconnect recovery.
- [ ] Use the group repository throughout development; retain Git history as
  supporting evidence for contribution statements and the viva.
- [ ] Package all source in one group ZIP or repository, without generated virtual
  environments or model weights unless specifically approved.
- [ ] Submit the package or repo URL to Canvas `miniProject-Assignment4-submission`
  by **4 November 2026**.

## Final demo and individual technical interview (section 15)

- [ ] Reserve one 15-minute team slot on 6 November 2026 within the published
  10:00 AM-6:00 PM window, entering the team number in an empty slot.
- [ ] Have all members attend the same slot and be ready at least five minutes early.
- [ ] Prepare the server, browser clients, deterministic demo, source, logs/results
  and Ubuntu environment before the slot; setup counts toward the 15 minutes.
- [ ] Ensure every member can explain and modify the implementation, including
  revision/recovery, bounded buffering, overload policy, metrics and AI-assisted work.

## Additional project verification (not a separate spec requirement)

- [x] Implement and independently test bounded queue, sender, lifecycle cleanup
  and monitoring modules in `delivery/`.
- [x] Pass 52 local component/integration/configuration tests, including 100 controlled
  connections with ten blocked senders and real loopback server tests.
  These are not Ubuntu public-test results.
- [ ] Run `python submission_check.py --final` on the final Ubuntu submission;
  this checks compatibility, not full correctness/performance.

ASR integration is optional bonus work (section 17), not part of the mandatory
checklist. Multi-session support, authentication and TLS/WSS are also not required
for the mandatory one-source/one-session interface (section 4.1).
