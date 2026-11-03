"""Reuse the established public analysis, with this export's provenance."""

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "recovery_batches"))
from summarize_public import summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=HERE / "evidence")
    args = parser.parse_args()
    output = HERE / "public_summary.json"
    summarize(args.evidence, output)
    result = json.loads(output.read_text())
    metadata = json.loads((args.evidence / "rerun_metadata.json").read_text())
    result["source_snapshot"] = metadata["source"]
    output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
