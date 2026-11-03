"""Deterministic queue/policy experiment; no sockets or TCP performance claims.

Uses the production ClientQueue and OverloadPolicy with a virtual monotonic
clock. Run from the project root: python experiments/overload_thresholds/run_sweep.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import platform
import random
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from delivery.client_monitor import DeliveryStats
from delivery.client_queue import ClientQueue, PushResult
from delivery.overload_policy import OverloadMode, OverloadPolicy, OverloadThresholds
from experiments.overload_thresholds import baseline_client_queue as baseline


def workload(kind, seed, batch_segments=8, drain_seconds=2.0):
    rng = random.Random(seed)
    events = []
    now = 0.0
    for batch in range(12):
        segments = list(range(batch * batch_segments + 1, (batch + 1) * batch_segments + 1))
        if kind == "sequential":
            updates = [(s, r, r == 4) for s in segments for r in range(1, 5)]
        else:
            # Every segment remains active at the first drain checkpoint.
            updates = [(s, r, False) for r in range(1, 4) for s in segments]
        for s, r, final in updates:
            now += rng.uniform(0.008, 0.012)
            events.append((now, "caption", (s, r, final)))
        now += drain_seconds  # Let the consumer drain before comparing active state.
        events.append((now, "checkpoint", None))
        if kind != "sequential":
            for s in reversed(segments):
                now += 0.01
                events.append((now, "caption", (s, 4, True)))
            now += drain_seconds
            events.append((now, "checkpoint", None))
    return events


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * p)]


async def run_case(kind, seed, ratio, age_enabled, cross_segment, capacity=32):
    now = [0.0]
    # Frozen pre-fix implementation preserves the historical comparison.
    queue_type = baseline.ClientQueue if cross_segment else ClientQueue
    accepted = baseline.PushResult.ACCEPTED if cross_segment else PushResult.ACCEPTED
    full = baseline.PushResult.FULL if cross_segment else PushResult.FULL
    queue = queue_type(max_messages=capacity, max_bytes=1024 * 1024)
    # Ratio-only isolates the percentage; defaults retain production age triggers.
    thresholds = OverloadThresholds(
        enter_queue_ratio=ratio,
        enter_oldest_seconds=0.5 if age_enabled else float("inf"),
        enter_in_flight_seconds=0.5 if age_enabled else float("inf"),
    )
    policy = OverloadPolicy(max_messages=capacity, max_bytes=1024 * 1024,
                            thresholds=thresholds)
    source, received = {}, {}
    sent = set()
    ages = []
    discarded = unsafe = rejected = fallback = max_depth = 0
    active_checks = active_missing = final_checks = final_missing = 0
    reasons = {"ratio": 0, "oldest_age": 0}
    first_entry_depths = []
    next_receive = 0.0
    # A controlled consumer removes one message each 40 ms. This models an
    # application backlog, not a real browser receive or WebSocket send buffer.
    receive_interval = 0.04

    async def consume_until(target):
        nonlocal next_receive
        while next_receive <= target:
            now[0] = next_receive
            if queue.stats().depth:
                raw = json.loads(await queue.get())
                s, r, f = raw["segment"], raw["revision"], raw["final"]
                received[s] = (r, f)
                sent.add((s, r))
                ages.append((now[0] - raw["created"]) * 1000)
            next_receive += receive_interval
        now[0] = target

    batch_segments = 8 if capacity == 32 else 96
    drain_seconds = max(2.0, capacity * receive_interval + 1)
    with patch("delivery.client_queue.monotonic", side_effect=lambda: now[0]), \
            patch.object(baseline, "monotonic", side_effect=lambda: now[0]):
        for at, action, event in workload(kind, seed, batch_segments, drain_seconds):
            await consume_until(at)
            if action == "checkpoint":
                # Compare newest source state, not receipt of obsolete revisions.
                assert queue.stats().depth == 0, "Checkpoint must follow a full drain"
                for s, expected in source.items():
                    if expected[1]:
                        final_checks += 1
                        final_missing += received.get(s) != expected
                    else:
                        active_checks += 1
                        active_missing += received.get(s) != expected
                continue
            s, r, final = event
            source[s] = (r, final)
            stats = queue.stats()
            snapshot = DeliveryStats(stats, False, 0, 0, len(sent), 0, 0, 0, None, None)
            before_mode = policy.mode
            mode = policy.update(snapshot)
            if before_mode is OverloadMode.NORMAL and mode is OverloadMode.DEGRADED:
                first_entry_depths.append(stats.depth)
                if stats.depth / capacity >= ratio:
                    reasons["ratio"] += 1
                if stats.oldest_pending_seconds >= thresholds.enter_oldest_seconds:
                    reasons["oldest_age"] += 1
            degraded = mode is OverloadMode.DEGRADED
            raw = json.dumps(dict(segment=s, revision=r, final=final, created=at))
            # Private inspection is experiment-only instrumentation, never policy input.
            before = list(queue._items)
            extra = {"drop_older_segments": degraded} if cross_segment else {}
            result = queue.push_caption(raw, segment=s, revision=r, final=final,
                                        coalesce=degraded, **extra)
            if result is full:
                fallback += 1
                extra = {"drop_older_segments": True} if cross_segment else {}
                result = queue.push_caption(raw, segment=s, revision=r, final=final,
                                            coalesce=True, **extra)
            if result is not accepted:
                # Stop like server eviction; no simulated reconnect/recovery.
                rejected += 1
                break
            remaining = {id(item) for item in queue._items}
            for item in before:
                if id(item) not in remaining:
                    discarded += 1
                    # Unsafe = removing the newest still-active source state,
                    # with no newer same-segment update available to replace it.
                    unsafe += source[item.segment] == (item.revision, False)
            max_depth = max(max_depth, queue.stats().depth)
        await consume_until(now[0] + drain_seconds)
    total_segments = batch_segments * 12
    expected_updates = total_segments * 4
    finals = sum(received.get(s) == (4, True) for s in range(1, total_segments + 1))
    return dict(workload=kind, seed=seed, enter_ratio=ratio,
                age_triggers=age_enabled, cross_segment=cross_segment,
                capacity=capacity, segments_per_batch=batch_segments,
                live_coverage_pct=100 * len(sent) / expected_updates,
                final_completeness_pct=100 * finals / total_segments,
                active_checks=active_checks, active_latest_missing=active_missing,
                final_checks=final_checks, final_checkpoint_missing=final_missing,
                unsafe_latest_drops=unsafe, discarded_provisionals=discarded,
                admission_failures=rejected, full_fallbacks=fallback,
                peak_depth=max_depth, p95_receive_age_ms=percentile(ages, .95),
                fresh_pct=100 * sum(a <= 500 for a in ages) / len(ages),
                transitions_to_degraded=policy.transitions_to_degraded,
                transitions_to_normal=policy.transitions_to_normal,
                entry_depths=first_entry_depths, entry_reasons=reasons)


async def main():
    rows = []
    for kind in ("sequential", "interleaved"):
        for cross_segment in (True, False):
            for age_enabled in (True, False):
                for ratio in (.3, .5, .7):
                    for seed in (3103, 3104, 3105):
                        rows.append(await run_case(kind, seed, ratio, age_enabled, cross_segment))
    # Repeat the production queue count capacity; the smaller queue above makes
    # percentage crossings observable with a modest controlled burst.
    for cross_segment in (True, False):
        for age_enabled in (True, False):
            for ratio in (.3, .5, .7):
                for seed in (3103, 3104, 3105):
                    rows.append(await run_case("interleaved", seed, ratio, age_enabled,
                                               cross_segment, capacity=256))
    output = Path(__file__).with_name("results.json")
    # Reject inconsistent evidence rather than silently plotting it.
    assert len(rows) == 108
    assert all(r["peak_depth"] <= r["capacity"] for r in rows)
    assert all(r["final_completeness_pct"] == 100 for r in rows)
    assert all(r["admission_failures"] == 0 for r in rows)
    assert all(r["unsafe_latest_drops"] == r["active_latest_missing"] == 0
               for r in rows if not r["cross_segment"])
    assert any(r["active_latest_missing"] > 0 for r in rows if r["cross_segment"])
    output.write_text(json.dumps(dict(
        method="Virtual-time production queue/policy simulation; no network or recovery",
        python=platform.python_version(), platform=platform.platform(),
        source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                       for name in ("delivery/client_queue.py", "delivery/overload_policy.py",
                                    "experiments/overload_thresholds/baseline_client_queue.py",
                                    "experiments/overload_thresholds/run_sweep.py")},
        producer_interval_seconds=[.008, .012], consumer_interval_seconds=.04,
        seeds=[3103, 3104, 3105], rows=rows), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} cases to {output}")
    for cross in (True, False):
        subset = [r for r in rows if r["cross_segment"] == cross]
        print(f"cross_segment={cross}: unsafe drops={sum(r['unsafe_latest_drops'] for r in subset)}, "
              f"missing active checkpoints={sum(r['active_latest_missing'] for r in subset)}, "
              f"admission failures={sum(r['admission_failures'] for r in subset)}")


if __name__ == "__main__":
    asyncio.run(main())
