"""Verify exact tested sources, raw public results and published checksums."""

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-hashes", action="store_true")
    args = parser.parse_args()
    recovery = json.loads((HERE / "current.json").read_text())
    assert len(recovery["rows"]) == 15
    for row in recovery["rows"]:
        assert row["complete_recovery_ms"] is not None
        assert not row["missing_segments"] and row["duplicates"] == 0
        assert row["max_frame_bytes"] <= 2**20
    public = json.loads((HERE / "public_summary.json").read_text())
    with tarfile.open(HERE / "accepted-evidence.tar.gz") as archive:
        manifest = json.load(archive.extractfile("current/SOURCE_SHA256.json"))
        for name, expected in manifest.items():
            if name.startswith(("captions/", "delivery/", "client/", "tests/")) or name in (
                "solution_server.py", "run_demo.py", "load_clients.py",
                "caption_source.py", "analyse_results.py",
            ):
                assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
        for label in ("t1", "t2", "t3", "t4", "t5"):
            phase = json.load(archive.extractfile(f"evidence/{label}_evidence.json"))
            assert len(phase["runs"]) == 3
            for run in phase["runs"]:
                assert run["public"]["final_completeness_pct"] == 100
                assert run["public"]["duplicates"] == run["public"]["stale"] == 0
            for key, measured in public["tests"][label]["metrics"].items():
                median = statistics.median(run["public"][key] for run in phase["runs"])
                assert abs(measured["median"] - median) < 1e-8, label + ": " + key
        assert json.load(archive.extractfile("evidence/recovery_current.json")) == recovery
        comparison = json.loads((HERE / "comparison.json").read_text())
        assert comparison == json.load(archive.extractfile("evidence/comparison.json"))
    for name, expected in comparison["source_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    protected = [row for row in comparison["rows"] if row["version"] == "priority"]
    assert len(protected) == 15
    for row in protected:
        assert not row["evicted"] and row["final_completeness_pct"] == 100
        assert row["missing_active"] == 0
    for name in ("t1", "t2"):
        assert public["tests"][name]["metrics"]["normal_update_coverage_pct"]["median"] == 100
    sums = HERE / "SHA256SUMS.json"
    if args.write_hashes:
        hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(HERE.iterdir())
            if path.is_file() and path != sums
        }
        sums.write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8", newline="\n")
    for name, expected in json.loads(sums.read_text()).items():
        assert hashlib.sha256((HERE / name).read_bytes()).hexdigest() == expected, name
    print("PASS: exact runtime/test hashes, recovery, public medians and artifact checksums")


if __name__ == "__main__":
    main()
