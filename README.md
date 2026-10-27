# CS3103 Assignment 4 - LiveRecall

Group project repository for the CS3103 Assignment 4 LiveRecall mini-project.

## Current status

This repository currently contains the supplied starter kit, a working naive
baseline rather than a completed assignment solution. Begin with
[START_HERE.md](START_HERE.md) and [DEMO_GUIDE.md](DEMO_GUIDE.md).

The assignment handout PDF is intentionally excluded from version control.
The team's final `report.pdf` is still a required submission deliverable.

## Ubuntu quick start

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip iproute2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python submission_check.py
python solution_server.py --port 8765
```

See [DEMO_GUIDE.md](DEMO_GUIDE.md) for the browser and deterministic caption
source workflow, and [PUBLIC_TESTS.md](PUBLIC_TESTS.md) for public experiments.

## Final submission checklist

- Keep `solution_server.py`, `requirements.txt`, and browser files under `client/`.
- Complete this README with exact Ubuntu setup, server/browser instructions,
  public test commands and configuration, and design notes; use
  [README_TEMPLATE.md](README_TEMPLATE.md) as a guide.
- Create `AI_USAGE.md` from [AI_USAGE_TEMPLATE.md](AI_USAGE_TEMPLATE.md), including
  critical evaluation of at least one rejected or modified AI suggestion.
- Create `CONTRIBUTIONS.md` from
  [CONTRIBUTIONS_TEMPLATE.md](CONTRIBUTIONS_TEMPLATE.md), with named responsibilities
  and agreed contribution percentages.
- Add `report.pdf`: 4-5 pages excluding references and appendices. Cover architecture,
  protocol, revision/recovery semantics, backpressure/overload policy, experiments,
  at least one before/after optimization comparison, latency/freshness/completeness
  results, failures, trade-offs, limitations, and future work.
- Run T1-T5 in Ubuntu at least three times each and report median results.
- Submit the compulsory 5-7 minute deterministic application demo video separately.
- Run `python submission_check.py --final`; this is a compatibility check, not a
  full correctness or performance test.
- Submit one group ZIP or repository URL to Canvas
  `miniProject-Assignment4-submission` by 4 November 2026.

Use the group repository throughout development to retain contribution history.
Do not include generated virtual environments or model weights without approval.
