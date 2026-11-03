"""Virtual-time queue/policy comparison, separate from public networking tests."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import random
import platform
import statistics
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from delivery.client_monitor import DeliveryStats
from delivery.client_queue import ClientQueue, PushResult
from delivery.final_aware_queue import FinalAwareQueue
from delivery.overload_policy import OverloadMode, OverloadPolicy
import delivery.client_queue as fifo_module
import delivery.final_aware_queue as priority_module
from captions.history import History
from captions.state import Event


def events_for(name, seed):
    rng = random.Random(seed)
    events = []
    if name == "full_finals":
        return [(0, "push", (1, 1, True)), (0, "push", (2, 1, True)),
                (0, "push", (100, 1, False)),
                (.01, "take", None), (.02, "take", None), (.03, "take", None)]
    if name == "full_active":
        for segment in range(100, 132):
            events.append((0, "push", (segment, 1, False)))
        events.append((0, "push", (1, 1, True)))
        for n in range(1, 50):
            events.append((n * .01, "take", None))
        return events
    duration = 20
    final_rate = 5 if name == "normal" else 90
    if name == "almost_final_capacity":
        final_rate = 99
    active_rate = 10 if name == "normal" else 40
    for n in range(duration * final_rate):
        at = n / final_rate + rng.uniform(0, .001)
        events.append((at, "push", (n+1, 1, True)))
    revisions = [0] * 8
    for n in range(duration * active_rate):
        slot = n % 8
        revisions[slot] += 1
        at = n / active_rate + rng.uniform(0, .001)
        events.append((at, "push", (100000+slot, revisions[slot], False)))
    for n in range((duration+2) * 100):
        events.append((n * .01, "take", None))
    events.sort(key=lambda row: row[0])
    return events


async def run_case(name, version, seed):
    capacity = 2 if name == "full_finals" else 32
    queue_class = ClientQueue if version == "fifo" else FinalAwareQueue
    queue = queue_class(max_messages=capacity, max_bytes=65536)
    policy = OverloadPolicy(max_messages=capacity, max_bytes=65536)
    now = [0.0]
    expected = {}
    received = {}
    sent_pairs = set()
    final_ages = []
    generated_finals = set()
    generated_updates = 0
    evicted = False
    peak_depth = peak_bytes = peak_final = peak_active = 0
    drained_before_end = 0
    stream = events_for(name, seed)
    with patch.object(fifo_module, "monotonic", side_effect=lambda: now[0]), \
            patch.object(priority_module, "monotonic", side_effect=lambda: now[0]):
        for at, action, update in stream:
            now[0] = at
            if action == "push":
                segment, revision, final = update
                expected[segment] = (revision, final)
                generated_updates += 1
                if final:
                    generated_finals.add(segment)
                if evicted:
                    continue
                stats = DeliveryStats(queue.stats(), False, 0, 0, 0, 0, 0, 0, None, None)
                mode = policy.update(stats)
                payload = json.dumps(dict(segment=segment, revision=revision, final=final, created=at))
                result = queue.push_caption(payload, segment=segment, revision=revision,
                                            final=final, coalesce=mode is OverloadMode.DEGRADED)
                if result is PushResult.FULL:
                    result = queue.push_caption(payload, segment=segment, revision=revision,
                                                final=final, coalesce=True)
                if result is not PushResult.ACCEPTED:
                    evicted = True
                    queue.close()
                    continue
                observed = queue.stats()
                peak_depth = max(peak_depth, observed.depth)
                peak_bytes = max(peak_bytes, observed.pending_bytes)
                peak_final = max(peak_final, getattr(observed, "final_depth", 0))
                peak_active = max(peak_active, getattr(observed, "active_depth", 0))
                assert observed.pending_bytes <= (131072 if version == "priority" else 65536)
                if version == "priority":
                    assert observed.final_depth <= capacity
                    assert observed.active_depth <= 1000
            elif not evicted and queue.stats().depth:
                item = json.loads(await queue.get())
                pair = (item["segment"], item["revision"])
                sent_pairs.add(pair)
                received[item["segment"]] = (item["revision"], item["final"])
                if item["final"]:
                    final_ages.append((at-item["created"]) * 1000)
                    if at < 20:
                        drained_before_end += 1
    correct_finals = 0
    missing_active = 0
    for segment, state in expected.items():
        if state[1]:
            correct_finals += received.get(segment) == state
        else:
            missing_active += received.get(segment) != state
    ordered = sorted(final_ages)
    p95 = ordered[round((len(ordered)-1)*.95)] if ordered else None
    return dict(case=name, version=version, seed=seed, evicted=evicted,
        generated_finals=len(generated_finals), received_finals=correct_finals,
        final_completeness_pct=100*correct_finals/len(generated_finals),
        missing_active=missing_active, generated_updates=generated_updates,
        update_coverage_pct=100*len(sent_pairs)/generated_updates,
        final_p95_ms=p95, final_fresh_pct=100*sum(age<=500 for age in final_ages)/len(generated_finals),
        peak_depth=peak_depth, peak_payload_bytes=peak_bytes,
        peak_final_lane=peak_final, peak_active_lane=peak_active,
        finals_received_while_source_running=drained_before_end,
        degraded_entries=policy.transitions_to_degraded)


def history_capacity_probe():
    results = []
    for rate in (10, 50, 84, 100):
        clock = [0.0]
        history = History(clock=lambda: clock[0])
        for n in range(rate * 90):
            clock[0] = n / rate
            history.add(Event(n+1, 1, True, "{}", clock[0]))
        clock[0] = 90
        returned = history.since(0, 0, seconds=60)
        results.append(dict(finals_per_second=rate, required_recent=rate*60,
                            returned=len(returned), stored=len(history),
                            count_pruned=history.stats()["pruned_by_count"]))
    return results


async def main(output):
    rows = []
    names = ("normal", "full_finals", "full_active", "near_final_capacity", "almost_final_capacity")
    for name in names:
        for version in ("fifo", "priority"):
            for seed in (3103, 3104, 3105):
                row = await run_case(name, version, seed)
                rows.append(row)
    for row in rows:
        if row["version"] == "priority":
            assert not row["evicted"]
            assert row["final_completeness_pct"] == 100 and row["missing_active"] == 0
        if row["case"] == "normal":
            assert row["update_coverage_pct"] == 100
    hashes = {}
    for name in ("delivery/client_queue.py", "delivery/final_aware_queue.py",
                 "delivery/overload_policy.py", "captions/history.py",
                 "experiments/final_aware_delivery/run_comparison.py"):
        hashes[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    output.write_text(json.dumps(dict(method="Virtual-time application queue simulation, not network latency",
        python=platform.python_version(), platform=platform.platform(), source_sha256=hashes,
        service_per_second=100, rows=rows, history_capacity=history_capacity_probe()), indent=2)+"\n",
        encoding="utf-8", newline="\n")
    print("Wrote " + str(output))
    for name in names:
        for version in ("fifo", "priority"):
            group = [r for r in rows if r["case"] == name and r["version"] == version]
            print(name, version, "final complete:", statistics.median(r["final_completeness_pct"] for r in group),
                  "P95:", statistics.median(r["final_p95_ms"] for r in group if r["final_p95_ms"] is not None)
                  if any(r["final_p95_ms"] is not None for r in group) else None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("comparison.json"))
    args = parser.parse_args()
    asyncio.run(main(args.output))
