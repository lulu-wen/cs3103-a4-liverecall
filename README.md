# LiveRecall - CS3103 Assignment 4

LiveRecall is a classroom caption application: follow live subtitles, review
recent context with **What did I miss?**, and recover missed captions after reconnecting.

## Architecture

```text
caption_source.py -> solution_server.py -> browser clients
```

- **Caption source:** the supplied deterministic generator produces revisions.
- **Server:** distributes captions and will manage revision state, recent history
  and recovery.
- **Browser:** displays captions and will provide local recall and auto-reconnect.
- **Delivery package:** bounded per-client queues, senders, cleanup and monitoring.

## Current status

- Server/browser still use the naive starter behavior.
- `delivery/` is independently tested but not connected to the server yet.
- Local tests pass; Ubuntu public performance results are pending.
- Track required work in the [project checklist](docs/project_checklist.md).

## Project layout

```text
solution_server.py   Required server entry point
caption_source.py    Deterministic caption source
client/              Browser UI
delivery/            Per-client delivery modules
tests/               Component/integration tests
docs/delivery/       Delivery design, test plan and drafts
```

## Ubuntu setup

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip iproute2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python submission_check.py
```

See [START_HERE.md](START_HERE.md) and [UBUNTU_SETUP.md](UBUNTU_SETUP.md) for details.

## Run the starter demo

Run in separate terminals from the repository root. Activate the virtual
environment in each terminal that runs the server or source.

```bash
# Server
python solution_server.py --port 8765

# Browser files: open http://127.0.0.1:8000
python -m http.server 8000 --directory client

# Caption source
python caption_source.py --duration 120
```

The starter is incomplete; see [DEMO_GUIDE.md](DEMO_GUIDE.md) for the final demo behavior.

## Tests

```bash
# Independent delivery checks; standard library only
python -m unittest discover -s tests -v

# Public run; uses the current server
python run_demo.py --clients 20 --duration 20 --runs 3 --output-stem t1
```

- Independent tests use controlled peers, not real networking benchmarks.
- Run mandatory public experiments in Ubuntu: [PUBLIC_TESTS.md](PUBLIC_TESTS.md).
- See the [delivery test plan](docs/delivery/delivery_test_plan.md) for remaining checks.

## Documentation

- [Project checklist](docs/project_checklist.md): spec requirements and status.
- [Delivery guide](docs/delivery/client_delivery.md): modules, lifecycle and monitoring.
- [Report draft](docs/delivery/buffering_report_draft.md): design and evidence gaps.
- [Submission drafts](docs/delivery/delivery_submission_drafts.md): AI usage and contributions.

The assignment handout PDF is excluded from Git. The team's final `report.pdf`
is still required.
