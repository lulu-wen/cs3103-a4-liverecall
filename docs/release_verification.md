# Submission verification — 3 November 2026

## Verified for this revision

- Queue cleanup retains each active segment's latest revision and removes only
  same-segment provisionals superseded by queued or incoming higher revisions.
  Queued finals and opaque messages are not coalesced. Rejected admission is atomic.
- All 158 Python tests pass on Windows and Ubuntu GB10, including actual loopback
  WebSockets, gated slow senders, interleaved updates, recovery and cleanup.
- `python submission_check.py --final` passes locally: required paths exist and
  the server accepts a WebSocket CLIENT_HELLO within the startup deadline.
  This checker does not establish performance or complete protocol correctness.
- The [108-case threshold experiment](../experiments/overload_thresholds/README.md)
  repeats the pre-fix counterexample and records zero useful latest-state drops
  in the fixed cases. It is virtual-time simulation, not public-test evidence.
- The report contains architecture/protocol, state/recovery, overload policy,
  T1–T5, a before/after comparison, memory observations, limitations and future work.
- RecoveryPlan preflights individual messages and streams bounded HISTORY batches,
  active snapshots and then the live queue. The 15-run Ubuntu oracle experiment
  verifies every required payload and measures actual full-state completion.
- The final-aware evidence verifier matches runtime/test hashes against the
  exact tested export and public medians against the retained raw outputs.
  Controlled final-heavy cases retain all finals/latest active state, but the
  History capacity probe exposes truncation above about 83 finals/s.
- README, AI_USAGE, CONTRIBUTIONS and report.pdf are present. Private study notes,
  virtual environments, expanded raw experiment folders and superseded archives
  remain outside the committed submission.

## Existing evidence and its limits

- Public T1–T5 were run three times on Ubuntu GB10 for snapshot `6dbd7da`.
  T3 also compares `acef9a3`. See the retained
  [methods and evidence](../experiments/delivery_20261103/README.md).
- The [final-aware export](../experiments/final_aware_delivery/README.md),
  identified by source manifest `f115f048f59d`, reran T1–T5 three times each
  on Ubuntu and repeated the 300-second workload. Every public run had 100%
  final completeness; T1/T2 had 100% Normal Update Coverage. T3 still records
  20 reconnect errors and poor slow-client freshness. Report numbers use this
  export; historical `acef9a3` remains explicitly labeled. All 15 final-aware
  controlled scheduler cases preserve finals and latest active state.
- The repeated 300-second RSS observation increases over time. It neither proves
  a memory leak nor demonstrates a stable memory plateau; queue/state/task
  instrumentation is needed to identify the main contributor.

## Still required or unresolved

- Before final submission, match the committed runtime files to the tested source
  hashes and run the final-package startup check on Ubuntu. Current Ubuntu tests
  and non-final startup check pass; local `--final` confirms required artifacts.
- Record and separately submit the compulsory 5–7 minute deterministic demo:
  two browsers, revisions becoming final, 15/30/60-second recall, reconnect and
  late join. No demo video was found in the repository; an external submission
  has not been verified.
- Complete a browser walkthrough of the final UI; local socket tests do not
  validate rendering, scrolling or the recorded demo.
- Batched HISTORY no longer omits retained finals at a total-response byte budget.
  Count-pruned/expired finals and individually oversized items remain limits.
  Final-aware scheduling now separates bounded final/control FIFO and latest-active
  map. Hard lane exhaustion and teardown can still discard pending data.
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
