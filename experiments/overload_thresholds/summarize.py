"""Build tables and a plot from the virtual-time experiment results."""

import json
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
data = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
rows = data["rows"]


def group(capacity, cross, age, ratio, kind="interleaved"):
    return [r for r in rows if (r["capacity"], r["cross_segment"], r["age_triggers"],
            r["enter_ratio"], r["workload"]) == (capacity, cross, age, ratio, kind)]


def table(capacity, age, kind="interleaved"):
    text = ["| Cleanup | Entry | Live coverage | Fresh ≤500 ms | P95 age (ms) | Unsafe drops | Missing latest active | Final complete |",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for cross in (True, False):
        for ratio in (.3, .5, .7):
            g = group(capacity, cross, age, ratio, kind)
            m = lambda k: median(r[k] for r in g)
            text.append(f"| {'Pre-fix cross-segment' if cross else 'Fixed semantic cleanup'} | {ratio:.0%} | "
                        f"{m('live_coverage_pct'):.2f}% | {m('fresh_pct'):.2f}% | "
                        f"{m('p95_receive_age_ms'):.1f} | {m('unsafe_latest_drops'):.0f} | "
                        f"{m('active_latest_missing'):.0f} | {m('final_completeness_pct'):.2f}% |")
    return "\n".join(text)


sections = ["# Overload entry-threshold experiment", "",
"This is a deterministic, virtual-time experiment using the fixed production `ClientQueue` and `OverloadPolicy`, compared against a frozen pre-fix queue. It does not run WebSockets, TCP, the full server, or recovery. Entry thresholds remain unchanged in production.", "",
"## Method", "",
"- Entry ratios: 30%, 50%, 70%; exit ratio stays 20%.",
"- Two trigger configurations: production age triggers (0.5 s entry / 0.15 s exit), and ratio-only entry (age entry disabled) to isolate percentage effects.",
"- Cleanup variants: frozen pre-fix cross-segment removal (`baseline_client_queue.py`), or fixed production queue-wide removal of superseded same-segment revisions. Both include the server's FULL retry. Rejected admission stops the simulated connection; no reconnect is modeled.",
"- Producer interval: seeded uniform 8–12 ms. Consumer: one dequeue every 40 ms. This deliberately creates application queue pressure and is not an emulation of public T3's socket behavior.",
"- Capacity: 32 messages for small controlled bursts; repeat interleaved workloads with the production 256-message capacity. Both use a 1 MiB byte limit and small JSON payloads; this is primarily count pressure.",
"- Twelve batches: eight segments per batch at capacity 32, or 96 at capacity 256. Each segment has three provisional revisions and one final. Sequential source completes each segment first; interleaved source sends revisions across multiple active segments, pauses to drain/check latest active state, then sends finals in reverse segment order.",
"- Each configuration runs seeds 3103, 3104, 3105. Tables show per-run medians. Queue and sender timing use virtual time, not wall-clock latency. No in-flight send is modeled.", "",
"## Metrics", "",
"- Live coverage: distinct dequeued segment/revision pairs divided by all generated updates. It can be below 100% when obsolete revisions are intentionally removed.",
"- Unsafe drops: queued provisional removals that equal that segment's newest still-active source revision at removal time. No newer same-segment update exists to replace them.",
"- Missing latest active: source-active segments whose latest revision is absent at post-drain checkpoints. This measures lost useful state rather than skipped obsolete versions.",
"- Final completeness: final state present after all batches drain. Later finals can restore state even if useful provisional state was previously lost.",
"- Freshness/P95: creation-to-dequeue age in this simulated application queue. They are not measured network latency.", "",
"## Production capacity: interleaved, production age triggers", "", table(256, True), "",
"## Production capacity: interleaved, ratio-only entry", "", table(256, False), "",
"## Small controlled queue: interleaved, production age triggers", "", table(32, True), "",
"## Small controlled queue: sequential, production age triggers", "", table(32, True, "sequential"), "",
"## Interpretation", "",
"- Selecting a percentage cannot make cross-segment removal semantically safe: a higher segment ID does not establish that lower active segments are obsolete.",
"- Fixed semantic cleanup preserves every segment's latest active state in these workloads, while legitimately lowering revision coverage. It removes older same-segment provisionals anywhere in the queue, including those superseded by queued finals.",
"- Inspect `entry_reasons` in results.json: age entry can dominate ratio thresholds. Identical ratio results under age triggers do not demonstrate that the percentage is irrelevant in all workloads.",
"- A threshold recommendation must prioritize zero useful-state loss, then compare age, freshness and coverage across realistic producer/consumer rates and real server runs. These synthetic workloads do not establish a universally optimal percentage.",
"- The experiment does not test byte-heavy payloads, in-flight sends, socket buffers, reconnection, sustained final-only overload, or memory growth.", "",
"## Reproduce", "", "The sweep uses only the Python standard library plus repository modules. Plotting additionally needs matplotlib; it is an optional experiment dependency, not a server dependency.", "", "```powershell", ".venv/Scripts/python.exe experiments/overload_thresholds/run_sweep.py", ".venv/Scripts/python.exe -m pip install matplotlib", ".venv/Scripts/python.exe experiments/overload_thresholds/summarize.py", "```", "",
"Raw per-seed values after the fix: [results.json](results.json). The original experiment is retained in [before_fix_results.json](before_fix_results.json); its safe control cleaned only the incoming segment. Figure: [threshold_comparison.png](threshold_comparison.png).", ""]
(HERE / "README.md").write_text("\n".join(sections), encoding="utf-8")

fig, axes = plt.subplots(1, 3, figsize=(13, 4), constrained_layout=True)
for cross, color, name in ((True, "#b34838", "Pre-fix cross-segment"),
                           (False, "#143b61", "Fixed semantic cleanup")):
    for ax, key, label in zip(axes,
            ("active_latest_missing", "p95_receive_age_ms", "live_coverage_pct"),
            ("Missing latest active (count)", "P95 queue receive age (ms)", "Live revision coverage (%)")):
        groups = [group(256, cross, True, ratio) for ratio in (.3, .5, .7)]
        centers = [median(r[key] for r in g) for g in groups]
        lower = [c - min(r[key] for r in g) for c, g in zip(centers, groups)]
        upper = [max(r[key] for r in g) - c for c, g in zip(centers, groups)]
        ax.errorbar([30, 50, 70], centers, yerr=[lower, upper], marker="o",
                    capsize=4, color=color, label=name)
        ax.set(xlabel="Entry queue utilization (%)", ylabel=label, xticks=[30, 50, 70])
        ax.grid(alpha=.2)
axes[0].legend(fontsize=8)
fig.suptitle("Virtual-time interleaved workload · capacity 256 · production age triggers\n"
             "Three seeds; median and observed range; not network latency", fontsize=11)
fig.savefig(HERE / "threshold_comparison.png", dpi=160)
plt.close(fig)
print("Wrote README.md and threshold_comparison.png")
