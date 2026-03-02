from __future__ import annotations

import argparse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE_DIR = ROOT / "RELEASES"

EXCLUDE_PREFIXES = (
    ".git/",
    ".venv/",
    "__pycache__/",
    "data/",
    "logs/",
    "instance/",
    "RELEASES/",
)
EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def should_include(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if any(rel.startswith(prefix) for prefix in EXCLUDE_PREFIXES):
        return False
    if any(rel.endswith(sfx) for sfx in EXCLUDE_SUFFIXES):
        return False
    return True


def build_release_zip(output: Path) -> tuple[int, list[str]]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if should_include(path):
            files.append(path)

    output.parent.mkdir(parents=True, exist_ok=True)
    included: list[str] = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            rel = path.relative_to(ROOT).as_posix()
            zf.write(path, arcname=rel)
            included.append(rel)
    return len(included), included


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(description="Create clean FlowForm release ZIP")
    parser.add_argument("--output", default=str(RELEASE_DIR / f"flowform-release-{ts}.zip"))
    args = parser.parse_args()

    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output

    count, _ = build_release_zip(output)
    print(f"[make_release] Wrote {output} with {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
