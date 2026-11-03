"""Package this accepted rerun and its exact source export on GB10."""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile

import websockets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    root = parser.parse_args().root.resolve()
    host = {
        "os_release": Path("/etc/os-release").read_text(),
        "architecture": platform.machine(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
        "websockets": websockets.__version__,
    }
    (root / "evidence/host.json").write_text(json.dumps(host, indent=2))
    names = [
        "evidence", "current/SOURCE_SHA256.json", "current/REVISION.txt",
        "final_aware_current.tar.gz", "run_other_evidence.py", "run_evidence.py",
        "current/experiments/recovery_batches/run_current.py",
        "current/experiments/recovery_batches/run_memory.py",
    ]
    with tarfile.open(root / "accepted-evidence.tar.gz", "w:gz") as archive:
        for name in names:
            archive.add(root / name, arcname=name)
    subprocess.run(["tc", "qdisc", "show", "dev", "lo"], check=True)
    print("Wrote accepted-evidence.tar.gz")


if __name__ == "__main__":
    main()
