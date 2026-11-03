# Overload entry-threshold experiment

This is a deterministic, virtual-time experiment using the fixed production `ClientQueue` and `OverloadPolicy`, compared against a frozen pre-fix queue. It does not run WebSockets, TCP, the full server, or recovery. Entry thresholds remain unchanged in production.

## Method

- Entry ratios: 30%, 50%, 70%; exit ratio stays 20%.
- Two trigger configurations: production age triggers (0.5 s entry / 0.15 s exit), and ratio-only entry (age entry disabled) to isolate percentage effects.
- Cleanup variants: frozen pre-fix cross-segment removal (`baseline_client_queue.py`), or fixed production queue-wide removal of superseded same-segment revisions. Both include the server's FULL retry. Rejected admission stops the simulated connection; no reconnect is modeled.
- Producer interval: seeded uniform 8–12 ms. Consumer: one dequeue every 40 ms. This deliberately creates application queue pressure and is not an emulation of public T3's socket behavior.
- Capacity: 32 messages for small controlled bursts; repeat interleaved workloads with the production 256-message capacity. Both use a 1 MiB byte limit and small JSON payloads; this is primarily count pressure.
- Twelve batches: eight segments per batch at capacity 32, or 96 at capacity 256. Each segment has three provisional revisions and one final. Sequential source completes each segment first; interleaved source sends revisions across multiple active segments, pauses to drain/check latest active state, then sends finals in reverse segment order.
- Each configuration runs seeds 3103, 3104, 3105. Tables show per-run medians. Queue and sender timing use virtual time, not wall-clock latency. No in-flight send is modeled.

## Metrics

- Live coverage: distinct dequeued segment/revision pairs divided by all generated updates. It can be below 100% when obsolete revisions are intentionally removed.
- Unsafe drops: queued provisional removals that equal that segment's newest still-active source revision at removal time. No newer same-segment update exists to replace them.
- Missing latest active: source-active segments whose latest revision is absent at post-drain checkpoints. This measures lost useful state rather than skipped obsolete versions.
- Final completeness: final state present after all batches drain. Later finals can restore state even if useful provisional state was previously lost.
- Freshness/P95: creation-to-dequeue age in this simulated application queue. They are not measured network latency.

## Production capacity: interleaved, production age triggers

| Cleanup | Entry | Live coverage | Fresh ≤500 ms | P95 age (ms) | Unsafe drops | Missing latest active | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pre-fix cross-segment | 30% | 51.76% | 41.15% | 2586.9 | 2223 | 606 | 100.00% |
| Pre-fix cross-segment | 50% | 51.76% | 41.15% | 2586.9 | 2223 | 606 | 100.00% |
| Pre-fix cross-segment | 70% | 51.76% | 41.15% | 2586.9 | 2223 | 606 | 100.00% |
| Fixed semantic cleanup | 30% | 68.68% | 12.45% | 3437.6 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 50% | 68.68% | 12.45% | 3437.6 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 70% | 68.68% | 12.45% | 3437.6 | 0 | 0 | 100.00% |

## Production capacity: interleaved, ratio-only entry

| Cleanup | Entry | Live coverage | Fresh ≤500 ms | P95 age (ms) | Unsafe drops | Missing latest active | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pre-fix cross-segment | 30% | 62.78% | 20.43% | 2724.4 | 1138 | 0 | 100.00% |
| Pre-fix cross-segment | 50% | 62.78% | 20.43% | 2724.4 | 1138 | 0 | 100.00% |
| Pre-fix cross-segment | 70% | 43.95% | 20.05% | 2636.2 | 1140 | 1140 | 100.00% |
| Fixed semantic cleanup | 30% | 68.68% | 12.45% | 3437.6 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 50% | 68.68% | 12.45% | 3437.6 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 70% | 68.68% | 12.45% | 3437.6 | 0 | 0 | 100.00% |

## Small controlled queue: interleaved, production age triggers

| Cleanup | Entry | Live coverage | Fresh ≤500 ms | P95 age (ms) | Unsafe drops | Missing latest active | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pre-fix cross-segment | 30% | 64.32% | 100.00% | 355.2 | 89 | 28 | 100.00% |
| Pre-fix cross-segment | 50% | 49.74% | 100.00% | 224.8 | 77 | 77 | 100.00% |
| Pre-fix cross-segment | 70% | 100.00% | 75.78% | 687.1 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 30% | 67.71% | 100.00% | 280.0 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 50% | 67.71% | 100.00% | 280.0 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 70% | 100.00% | 75.78% | 687.1 | 0 | 0 | 100.00% |

## Small controlled queue: sequential, production age triggers

| Cleanup | Entry | Live coverage | Fresh ≤500 ms | P95 age (ms) | Unsafe drops | Missing latest active | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pre-fix cross-segment | 30% | 49.48% | 100.00% | 340.0 | 0 | 0 | 100.00% |
| Pre-fix cross-segment | 50% | 60.68% | 100.00% | 435.7 | 0 | 0 | 100.00% |
| Pre-fix cross-segment | 70% | 43.75% | 100.00% | 244.2 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 30% | 49.48% | 100.00% | 340.0 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 50% | 60.68% | 100.00% | 435.7 | 0 | 0 | 100.00% |
| Fixed semantic cleanup | 70% | 43.75% | 100.00% | 244.2 | 0 | 0 | 100.00% |

## Interpretation

- Selecting a percentage cannot make cross-segment removal semantically safe: a higher segment ID does not establish that lower active segments are obsolete.
- Fixed semantic cleanup preserves every segment's latest active state in these workloads, while legitimately lowering revision coverage. It removes older same-segment provisionals anywhere in the queue, including those superseded by queued finals.
- Inspect `entry_reasons` in results.json: age entry can dominate ratio thresholds. Identical ratio results under age triggers do not demonstrate that the percentage is irrelevant in all workloads.
- A threshold recommendation must prioritize zero useful-state loss, then compare age, freshness and coverage across realistic producer/consumer rates and real server runs. These synthetic workloads do not establish a universally optimal percentage.
- The experiment does not test byte-heavy payloads, in-flight sends, socket buffers, reconnection, sustained final-only overload, or memory growth.

## Reproduce

The sweep uses only the Python standard library plus repository modules. Plotting additionally needs matplotlib; it is an optional experiment dependency, not a server dependency.

```powershell
.venv/Scripts/python.exe experiments/overload_thresholds/run_sweep.py
.venv/Scripts/python.exe -m pip install matplotlib
.venv/Scripts/python.exe experiments/overload_thresholds/summarize.py
```

Raw per-seed values after the fix: [results.json](results.json). The original experiment is retained in [before_fix_results.json](before_fix_results.json); its safe control cleaned only the incoming segment. Figure: [threshold_comparison.png](threshold_comparison.png).
