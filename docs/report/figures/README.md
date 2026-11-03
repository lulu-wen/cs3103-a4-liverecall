# Report diagrams

| Editable source | Rendered figure | Purpose |
| --- | --- | --- |
| `render_architecture.py` | `system_architecture.png` | Fixed horizontal layout with queues, senders, history and browser state |
| `reconnect_recovery.mmd` | `reconnect_recovery.png` | Final-cursor recovery, initial ordering and browser merge |

The architecture uses a fixed matplotlib layout for readability on a report page.
`system_architecture.mmd` retains the equivalent Mermaid flow model. Render it with:

```bash
python docs/report/figures/render_architecture.py
```

The recovery diagram uses Mermaid CLI 12.2.0. These are documentation tools, not
required server dependencies. From the repository root:

```bash
npx --yes --package @mermaid-js/mermaid-cli@12.2.0 mmdc \
  -i docs/report/figures/reconnect_recovery.mmd \
  -o docs/report/figures/reconnect_recovery.png \
  -c docs/report/figures/mermaid-config.json -b white -s 2
```

If using an existing Chrome installation,
pass `-p <puppeteer-config.json>` with its `executablePath`. The local Windows
configuration is under ignored `tmp/`; no machine-specific browser path is
required by the application.

The architecture figure is logical: it does not imply separate server processes.
Each client has independent tasks/queues on the same server event loop.
