# Submission verification — 3 November 2026

## Verified for this revision

- Queue cleanup retains each active segment's latest revision and removes only
  same-segment provisionals superseded by queued or incoming higher revisions.
  Queued finals and opaque messages are not coalesced. Rejected admission is atomic.
- All 135 Python tests pass locally on Windows, including actual loopback
  WebSockets, gated slow senders, interleaved updates, recovery and cleanup.
- `python submission_check.py --final` passes locally: required paths exist and
  the server accepts a WebSocket CLIENT_HELLO within the startup deadline.
  This checker does not establish performance or complete protocol correctness.
- The [108-case threshold experiment](../experiments/overload_thresholds/README.md)
  repeats the pre-fix counterexample and records zero useful latest-state drops
  in the fixed cases. It is virtual-time simulation, not public-test evidence.
- The report contains architecture/protocol, state/recovery, overload policy,
  T1–T5, a before/after comparison, memory observations, limitations and future work.
- README, AI_USAGE, CONTRIBUTIONS and report.pdf are present. Private study notes,
  virtual environments, expanded raw experiment folders and superseded archives
  remain outside the committed submission.

## Existing evidence and its limits

- Public T1–T5 were run three times on Ubuntu GB10 for snapshot `6dbd7da`.
  T3 also compares `acef9a3`. See the retained
  [methods and evidence](../experiments/delivery_20261103/README.md).
- Those public runs predate this cleanup fix. The current regression suite and
  simulation do not establish unchanged public-test performance after the fix.
- The single 300-second RSS observation increases over time. It neither proves
  a memory leak nor demonstrates a stable memory plateau; queue/state/task
  instrumentation is needed to identify the main contributor.

## Still required or unresolved

- Rerun the final source on Ubuntu: unit/integration tests, startup check and
  T1–T5 (three independent runs each). T5 requires the previously agreed manual
  sudo step on GB10. Record the exact tested commit and update report metrics
  if results change.
- Record and separately submit the compulsory 5–7 minute deterministic demo:
  two browsers, revisions becoming final, 15/30/60-second recall, reconnect and
  late join. No demo video was found in the repository; an external submission
  has not been verified.
- Complete a browser walkthrough of the final UI; local socket tests do not
  validate rendering, scrolling or the recorded demo.
- Bounded HISTORY can omit older finals at its byte budget. Eviction/teardown
  can discard queued data, and recovery is limited by retention/capacity.
  A successful HISTORY response is not proof that every missing final returned.
- Slow-reader freshness, sustained overload and long-term memory behavior remain
  limitations. Preserving useful provisional state can increase backlog or
  eviction pressure; 50% has not been established as an optimal entry threshold.
- Confirm contribution percentages and all members' AI disclosures with the team;
  the current contribution table lists 25% each.
- Submit the repository/ZIP, report and separate video by **4 November 2026**.
  Confirm the team interview slot for **6 November 2026** and prepare all members.

The main [project checklist](project_checklist.md) is a requirement inventory.
Unchecked historical entries are not a substitute for this evidence-based audit.
