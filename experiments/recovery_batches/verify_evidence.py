"""Check recorded outcomes, exact tested runtime, and published file hashes."""

import argparse
import ast
import hashlib
import io
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
    current = json.loads((HERE / "current.json").read_text())
    assert len(current["rows"]) == 15
    for row in current["rows"]:
        assert row["complete_recovery_ms"] is not None
        assert not row["missing_segments"] and row["duplicates"] == 0
        assert row["max_frame_bytes"] <= 2**20
    summary = json.loads((HERE / "public_summary.json").read_text())
    formatting_path = HERE / "formatting_equivalence.json"
    formatting = {}
    if formatting_path.exists():
        formatting = json.loads(formatting_path.read_text())
    with tarfile.open(HERE / "accepted-evidence.tar.gz") as archive:
        manifest = json.load(archive.extractfile("current/SOURCE_SHA256.json"))
        for name, expected_hash in manifest.items():
            runtime = name.startswith(("captions/", "delivery/", "client/", "tests/"))
            if runtime or name in ("solution_server.py", "run_demo.py", "load_clients.py",
                                    "caption_source.py", "analyse_results.py"):
                actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                if actual != expected_hash:
                    # Only explicitly recorded formatting changes are accepted.
                    record = formatting.get(name, {})
                    assert record.get("tested_sha256") == expected_hash, name
                    assert record.get("formatted_sha256") == actual, name
                    source_bytes = archive.extractfile("recovery_current.tar.gz").read()
                    with tarfile.open(fileobj=io.BytesIO(source_bytes)) as source:
                        tested = source.extractfile(name).read()
                    assert hashlib.sha256(tested).hexdigest() == expected_hash, name
                    old_tree = ast.dump(ast.parse(tested.decode("utf-8")))
                    new_tree = ast.dump(ast.parse((ROOT / name).read_text(encoding="utf-8")))
                    assert old_tree == new_tree, "Runtime logic changed: " + name
        for label in ("t1", "t2", "t3", "t4", "t5"):
            phase = json.load(archive.extractfile(f"evidence/{label}_evidence.json"))
            assert len(phase["runs"]) == 3
            for run in phase["runs"]:
                assert run["public"]["final_completeness_pct"] == 100
                assert run["public"]["duplicates"] == run["public"]["stale"] == 0
            for key, measured in summary["tests"][label]["metrics"].items():
                expected = statistics.median(run["public"][key] for run in phase["runs"])
                assert abs(measured["median"] - expected) < 1e-8, label + ": " + key
    hashes_path = HERE / "SHA256SUMS.json"
    if args.write_hashes:
        hashes = {}
        for path in sorted(HERE.iterdir()):
            if path.is_file() and path.name != hashes_path.name:
                hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes_path.write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8", newline="\n")
    hashes = json.loads(hashes_path.read_text())
    for name, expected in hashes.items():
        assert hashlib.sha256((HERE / name).read_bytes()).hexdigest() == expected, name
    print("PASS: recovery, public medians, runtime equivalence and artifact hashes")


if __name__ == "__main__":
    main()
