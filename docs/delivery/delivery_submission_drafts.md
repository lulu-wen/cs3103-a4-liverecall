# Submission documentation drafts

Notes to merge into final root-level documents; these do not replace them.

## README notes

- Modules live in `delivery/`; import through the package:
  `from delivery.client_queue import ClientQueue`.
- Entry point: `python solution_server.py --port 8765`.
- Integration is pending; current public runs still exercise the starter server.
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

## CONTRIBUTIONS notes

- **Wen Penj-Ju / A0342654A:** to fill.
- **Work:** bounded queue, sender, cleanup, monitor and independent tests.
- **Writing:** buffering/isolation report section and documentation compilation.
- **Pending:** integration, Ubuntu T3/RSS experiments and final analysis.
- **Contribution percentage:** team to agree; all members' values must total 100%.
- Record shared work; do not infer contribution shares from commit counts.

Git evidence: `af666c6` (queue), `35a5b24` (sender), `f017c55` (cleanup),
`bee8934` (monitor), `766a93e` (layout), `0376733` (integration tests).
