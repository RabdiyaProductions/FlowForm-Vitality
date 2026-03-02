from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> int:
    print("[run_full_tests] $", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(cwd))


def main() -> int:
    root = Path(__file__).resolve().parent.parent

    steps = [
        [sys.executable, "tools/check_structure.py"],
        [sys.executable, "-m", "pytest", "tests_smoke.py"],
        [sys.executable, "-m", "pytest", "smoke_test.py"],
    ]

    for step in steps:
        code = run(step, root)
        if code != 0:
            return code

    print("[run_full_tests] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
