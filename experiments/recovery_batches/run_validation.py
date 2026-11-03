"""Oracle-checked recovery timing over real loopback WebSockets.

This supplements, and never modifies, the supplied public measurement tools.
Recovery completes only when every required final and latest active payload has
been received correctly, not when the first HISTORY frame arrives.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
import solution_server as app


CASES = {
    "small_late_join": (20, 3, 100, 0, 256),
    "large_history": (96, 3, 20000, 0, 256),
    "resume_large_history": (96, 3, 20000, 16, 256),
    "many_active": (10, 300, 100, 0, 256),
    "one_slot_live_queue": (10, 16, 100, 0, 1),
}


async def run_case(name, repetition):
    finals, active, text_bytes, cursor, queue_slots = CASES[name]
    app.reset_caption_state()
    expected = {}
    for segment in range(1, finals + active + 1):
        final = segment <= finals
        # A non-ASCII prefix exercises encoded-byte rather than character limits.
        msg = dict(type="CAPTION_UPDATE", session="L1", segment=segment,
                   revision=3, final=final, source_ts=time.time(),
                   text="字幕" + "x" * text_bytes)
        raw = json.dumps(msg, ensure_ascii=False)
        event = app.STATE.apply(msg, raw)
        assert event is not None
        if final:
            app.HISTORY.add(event)
        if segment > cursor:
            expected[segment] = msg
    received = {}
    duplicates = 0
    history_frames = 0
    first_history_ms = complete_ms = None
    max_frame_bytes = 0
    failure = None
    arrival_types = []
    with patch.object(app, "QUEUE_MAX_MESSAGES", queue_slots):
        async with serve(app.handler, "127.0.0.1", 0, close_timeout=.5) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(uri, close_timeout=.5) as ws:
                connected = time.perf_counter()
                await ws.send(json.dumps(dict(type="CLIENT_HELLO", client_id=repetition,
                    session="L1", last_segment=cursor, last_revision=3 if cursor else 0,
                    history_seconds=60)))
                try:
                    async with asyncio.timeout(3):
                        while len(received) < len(expected):
                            raw = await ws.recv()
                            elapsed = (time.perf_counter() - connected) * 1000
                            max_frame_bytes = max(max_frame_bytes, len(raw.encode("utf-8")))
                            msg = json.loads(raw)
                            arrival_types.append(msg["type"])
                            if msg["type"] == "HISTORY":
                                history_frames += 1
                                if first_history_ms is None:
                                    first_history_ms = elapsed
                                assert all(item["final"] for item in msg["items"])
                                items = msg["items"]
                            else:
                                items = [msg]
                            for item in items:
                                segment = item["segment"]
                                assert item == expected[segment], "Incorrect recovery payload"
                                duplicates += segment in received
                                received[segment] = item
                            if received == expected:
                                complete_ms = elapsed
                except (TimeoutError, ConnectionClosed) as exc:
                    failure = type(exc).__name__
    assert not app.CLIENTS and not app.MONITORS and not app.EVICTIONS
    # A HISTORY batch arriving after active delivery would violate prefix order.
    first_active = next((i for i, t in enumerate(arrival_types) if t == "CAPTION_UPDATE"),
                        len(arrival_types))
    assert "HISTORY" not in arrival_types[first_active:]
    return dict(case=name, repetition=repetition, expected_finals=finals-cursor,
                expected_active=active, expected_total=len(expected), received_total=len(received),
                missing_segments=sorted(expected.keys()-received.keys()), duplicates=duplicates,
                first_history_ms=first_history_ms, complete_recovery_ms=complete_ms,
                history_frames=history_frames, max_frame_bytes=max_frame_bytes,
                queue_slots=queue_slots, failure=failure)


async def main(output):
    rows = []
    for name in CASES:
        for repetition in range(1, 4):
            row = await run_case(name, repetition)
            rows.append(row)
            print(json.dumps(row), flush=True)
    source_files = ["solution_server.py", "delivery/client_sender.py",
                    "delivery/client_session.py", "captions/history.py",
                    "experiments/recovery_batches/run_validation.py"]
    if (ROOT / "delivery/client_recovery.py").exists():
        source_files.append("delivery/client_recovery.py")
    summary = {}
    for name in CASES:
        group = [r for r in rows if r["case"] == name]
        times = [r["complete_recovery_ms"] for r in group if r["complete_recovery_ms"] is not None]
        summary[name] = dict(successful_runs=len(times), total_runs=len(group),
            median_complete_recovery_ms=statistics.median(times) if times else None,
            min_complete_recovery_ms=min(times) if times else None,
            max_complete_recovery_ms=max(times) if times else None,
            median_history_frames=statistics.median(r["history_frames"] for r in group),
            missing_counts=[len(r["missing_segments"]) for r in group])
    output.write_text(json.dumps(dict(method="Loopback oracle validation, not public T1-T5",
        python=platform.python_version(), platform=platform.platform(),
        recovery_clock="successful WebSocket connection until all expected finals and active state received",
        source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
        rows=rows, summary=summary), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("validation.json"))
    args = parser.parse_args()
    asyncio.run(main(args.output))
