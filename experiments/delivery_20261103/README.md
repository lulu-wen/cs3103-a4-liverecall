# Public experiment evidence — 3 November 2026

Measured results supporting [report.pdf](../../report.pdf). Temporary rerun
folders, expanded raw files, SVG duplicates and report-generation drafts are
excluded from Git.

## Files

| File | Purpose |
| --- | --- |
| `delivery-evidence-final-20261103.tar.gz` | Complete raw CSV/JSON, logs, RSS traces, metadata and collection helpers |
| `analysis_summary.json` | T3 before/after medians/ranges and RSS statistics |
| `public_summary.json` | T1/T2/T4/T5 per-run values and medians |
| `figures/*.png` | Four report charts: T3, RSS, public workloads and T5 receive-age distributions |
| `run_evidence.py` | Collect T3 before/after and server RSS without changing public tools |
| `run_other_evidence.py` | Collect T1/T2/T4/T5; T5 uses an isolated network namespace |
| `summarize_evidence.py` | Rebuild T3/RSS summaries and charts |
| `plot_public_results.py` | Plot public summaries and accepted T5 CSV distributions |
| `SHA256SUMS.json` | SHA-256 hashes of the published evidence files |

## Environment and method

- GB10: Ubuntu 24.04.4 ARM64, 20 logical CPUs, Python 3.12.3, websockets 16.1.1.
- Current snapshot: `6dbd7daa03c729f03f78a944be0871d31b92cc63`.
- Before snapshot: `acef9a301432cea5936c1303ce1799b8905d904a`, before P4/keepalive
  changes. This is an earlier implementation, not the starter `baseline` branch.
- Public source, runner, clients and analyser matched by SHA-256 across snapshots.
- Three sequential runs per public test/configuration, fresh server each time;
  source seed 3103. Reported values are medians of per-run metrics, not pooled
  percentiles. Whiskers show observed min/max, not confidence intervals.
- T1/T2/T3/T4 used same-host loopback without impairment.
- T5 used the supplied `delay_loss.sh` inside a private network namespace:
  120 ms ±30 ms normal jitter and 3% loss. Source/server and server/client paths
  both traverse this interface. Netem randomness was not seeded.
- T5 uses the accepted repeated three-run batch. The profile reset and host
  `noqueue` qdisc were verified; no experiment processes remained afterward.
- Queue defaults: 256 messages/1 MiB; send/close deadlines 10/3 s. Keepalive
  changed from 20/20 s before to 2/5 s currently.

## Public results

| Test | Workload per run | P50 / P95 / P99 (ms) | Fresh live % | Final % | Live coverage % |
| --- | --- | --- | --- | --- | --- |
| T1 | 20 clients, 20 s | 1.27 / 1.68 / 2.41 | 100 | 100 | 100 |
| T2 | 100 clients, 40 s | 2.84 / 3.95 / 4.26 | 100 | 100 | 100 |
| T4 | 100 clients, 60 s; 20% offline at 15 s for 5 s | 3.15 / 5.26 / 5.73 | 100 | 100 | 98.25 |
| T5 | 100 clients, 40 s; delay/loss | 255.91 / 691.05 / 1002.58 | 89.12 | 100 | 99.96 |

- All listed runs recorded zero duplicate/stale captions and zero connection errors.
- T4 recovered 140 items/run; HISTORY-response P95 was 1.87 ms and fresh-final
  ratio 98.42%. Its summary was reconstructed from complete public CSV/JSON
  after the SSH observer disconnected; missing observer metadata was not invented.
- T5 fresh-final ratio was 86.86%. Final completeness does not imply freshness.
- Live age excludes HISTORY; final completeness includes accepted/recovered finals.
  Freshness uses 500 ms. Live coverage counts distinct received revisions.
- HISTORY-response time measures request-to-response, not the age of recovered text.

## T3 and memory

- T3: 100 clients, 60 s, 10% slow readers waiting 500 ms per receive;
  76 final segments/228 revisions per run, three runs per version.
- Normal clients retained 100% live coverage and final completeness in both versions.
- Slow P95 live age: 28.08 s before → 13.36 s current; final completeness:
  51.32% → 100%. Current slow fresh-final ratio remained only 1.32%.
- Current T3 recorded 20 connection-error events and 380 recovered items/run.
  These observed costs are retained in the report and raw results.
- Server-child VmRSS was sampled every 0.5 s with PID attribution. T3 peak RSS:
  31.93–31.99 MiB before; 33.38–33.42 MiB current.
- One supplementary 300 s current run rose from 32.15 MiB (seconds 10–30 median)
  to 36.65 MiB (last 30 s median); post-60 s trend: 0.76 MiB/min.
- RSS excludes clients/kernel buffers. This upward trace proves neither a plateau
  nor leak freedom. Whole-version comparison does not isolate one optimization.
- The tested current Ubuntu snapshot passed 131 unit tests. Browser behavior,
  bandwidth halving, arbitrary interleaved segments and long-term memory growth
  are not established by these measurements.

## Rebuild charts

From the repository root, with matplotlib/numpy available in a plotting environment:

```bash
tar -xzf experiments/delivery_20261103/delivery-evidence-final-20261103.tar.gz -C experiments/delivery_20261103
python experiments/delivery_20261103/summarize_evidence.py
python experiments/delivery_20261103/plot_public_results.py
```

The expanded `evidence/` directory and generated SVGs remain ignored. The archive
includes the original per-run official results; `public_summary.json` retains
their three-run medians and identifies the selected data directory.

## Re-run collection

Use an isolated Ubuntu directory containing immutable `before/` and `current/`
source snapshots, each with `REVISION.txt`. Copy the two collection helpers there
and use the same virtual environment for both snapshots:

```bash
python run_evidence.py --python /absolute/path/to/venv/bin/python
python run_other_evidence.py --python /absolute/path/to/venv/bin/python --mode remaining
sudo /absolute/path/to/venv/bin/python run_other_evidence.py --python /absolute/path/to/venv/bin/python --mode t5
```

Use a new evidence directory for each collection. T5 serializes with the other
workloads, refuses shaping outside a separate namespace and resets its profile
on exit. Instructor measurement programs are unchanged. Exact executed commands
and source hashes are inside the archive's metadata/summary files.
