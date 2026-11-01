# LiveRecall - CS3103 Assignment 4

LiveRecall is a classroom caption application: follow live subtitles, review
recent context with **What did I miss?**, and recover missed captions after reconnecting.

## Architecture

```text
caption_source.py -> solution_server.py -> browser clients
```

- **Caption source:** the supplied deterministic generator produces revisions.
- **Server:** validates revisions, stores recent finals and serves recovery
  context before live delivery.
- **Browser:** displays revisions, keeps local recall and reconnects automatically.
- **Delivery package:** bounded per-client queues, senders, cleanup and monitoring.

## Current status

- The server now uses bounded per-client queues, senders, cleanup and monitoring.
- Source updates pass through revision state (stale, duplicate, after-final and
  malformed updates are dropped) and finals are kept in a bounded 90 s history
  (`captions/`). Joining/resuming clients receive HISTORY, then the latest active
  provisional state, before later live updates.
- The browser shows one line per segment, replaces revisions in place and
  distinguishes provisional from final captions. Local recall, HISTORY merge and
  fixed-delay reconnect are implemented; full browser recovery validation and
  semantic overload handling remain pending.
- Live broadcasts retry FULL with queue-wide provisional coalescing; unresolved
  admission failure temporarily closes that client with code 1013. This
  fallback does not guarantee delivery/recovery of finals under overload.
- Local tests pass; Ubuntu public performance results are pending.
- Track required work in the [project checklist](docs/project_checklist.md).

## Project layout

```text
solution_server.py   Required server entry point
caption_source.py    Deterministic caption source
client/              Browser UI
captions/            Revision state and bounded caption history
delivery/            Per-client delivery modules
tests/               Component/integration tests
  captions/          Caption revision state and history
  delivery/          Queues, senders, lifecycle, monitoring and controlled peers
  server/            Loopback protocol, recovery, pipeline and configuration
docs/delivery/       Delivery design, test plan and drafts
docs/state/          Revision state and history design
```

Run all tests from the repository root: `python -m unittest discover -s tests -t .`.
See [test groups and commands](tests/README.md).

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

## Run the application

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

The application is incomplete; see [DEMO_GUIDE.md](DEMO_GUIDE.md) for the final demo behavior.

## Delivery settings

The required command still works with the original defaults. For experiments:

```bash
python solution_server.py --port 8765 --queue-max-messages 512 --queue-max-bytes 2097152 --send-timeout 5 --close-timeout 2
```

- Queue limits must be positive integers; bytes count encoded payloads.
- Timeouts are positive finite seconds and accept fractions.
- Priority: CLI argument > `LIVERECALL_*` environment variable > default.
- Startup prints the settings actually used. See the
  [configuration guide](docs/delivery/client_delivery.md#experiment-settings)
  for defaults and unchanged-harness examples.

## Tests

```bash
# Component and real WebSocket checks; install requirements.txt first
python -m unittest discover -s tests -t . -v

# Public run; uses the current server
python run_demo.py --clients 20 --duration 20 --runs 3 --output-stem t1
```

- Component tests use controlled peers; server tests also use loopback WebSockets.
- Neither replaces the required Ubuntu networking benchmarks.
- Run mandatory public experiments in Ubuntu: [PUBLIC_TESTS.md](PUBLIC_TESTS.md).
- See the [delivery test plan](docs/delivery/delivery_test_plan.md) for remaining checks.

## Documentation

- [Project checklist](docs/project_checklist.md): spec requirements and status.
- [Delivery guide](docs/delivery/client_delivery.md): modules, lifecycle and monitoring.
- [Report draft](docs/delivery/buffering_report_draft.md): design and evidence gaps.
- [Submission drafts](docs/delivery/delivery_submission_drafts.md): AI usage and contributions.

The assignment handout PDF is excluded from Git. The team's final `report.pdf`
is still required.
