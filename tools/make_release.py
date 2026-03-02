#!/usr/bin/env python3
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "RELEASES"
RELEASES.mkdir(exist_ok=True)

EXCLUDE_PREFIXES = (
    ".git/", ".venv/", "__pycache__/", "logs/", "data/", "instance/", "RELEASES/", "node_modules/"
)
EXCLUDE_NAMES = {".DS_Store"}


def include_path(rel: str) -> bool:
    if any(rel.startswith(prefix) for prefix in EXCLUDE_PREFIXES):
        return False
    if any(part in {"__pycache__", ".venv"} for part in rel.split('/')):
        return False
    if Path(rel).name in EXCLUDE_NAMES:
        return False
    if rel.endswith('.pyc') or rel.endswith('.pyo'):
        return False
    return True


def detect_version() -> str:
    app_server = ROOT / "app_server.py"
    version = "unknown"
    if app_server.exists():
        for line in app_server.read_text().splitlines():
            if line.startswith("APP_VERSION"):
                version = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return version


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version = detect_version()
    out_zip = RELEASES / f"flowform-release-{version}-{ts}.zip"

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if not include_path(rel):
                continue
            zf.write(path, arcname=rel)

        stamp = {
            "version": version,
            "built_at": ts,
            "tool": "tools/make_release.py",
        }
        zf.writestr("VERSION_STAMP.txt", json.dumps(stamp, indent=2))

    print(f"[make_release] wrote {out_zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
