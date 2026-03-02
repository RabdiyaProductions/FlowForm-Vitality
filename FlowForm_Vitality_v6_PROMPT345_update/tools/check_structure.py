from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# No nested zips in the standalone app bundle.
ALLOWED_ZIPS: set[str] = set()

ALLOWED_RUN_SCRIPTS = {
    # Canonical boot (root)
    "setup.bat",
    "_setup_core.bat",
    "run.bat",
    "open_browser.bat",
    "run_tests.bat",
    "boot_port.py",
    "run_server.py",

    # Wrapper boot (double-click friendly)
    "_BAT/1_setup.bat",
    "_BAT/2_run.bat",
    "_BAT/3_open_browser.bat",
    "_BAT/6_run_tests.bat",

    # Tooling
    "tools/run_full_tests.py",
    "tools/check_structure.py",
}

BOOT_STACK_GROUPS = {
    "root_boot": {
        "setup.bat",
        "_setup_core.bat",
        "run.bat",
        "open_browser.bat",
        "run_tests.bat",
        "run_server.py",
        "boot_port.py",
    },
    "wrapper_boot": {
        "_BAT/1_setup.bat",
        "_BAT/2_run.bat",
        "_BAT/3_open_browser.bat",
        "_BAT/6_run_tests.bat",
    },
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def find_any_zips() -> list[str]:
    """
    Find any ZIP files that violate the bundle structure.

    ZIP files are disallowed in the repo root and most subfolders, with the
    exception of those placed within a `RELEASES/` directory. This allows
    shipping release artifacts alongside the code without breaking the
    structure guard. Any ZIP found outside of the RELEASES/ folder will
    trigger a violation.
    """
    violations: list[str] = []
    for path in ROOT.rglob("*.zip"):
        rp = rel(path)
        # Allow explicit whitelisted zips in the root (no path separator).
        if path.name in ALLOWED_ZIPS and "/" not in rp:
            continue
        # Permit zips under a RELEASES/ folder.
        parts = rp.split("/")
        if parts and parts[0].upper() == "RELEASES":
            continue
        violations.append(f"ZIP not allowed in standalone bundle: {rp}")
    return violations


def find_unapproved_run_scripts() -> list[str]:
    run_like: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rp = rel(path)
        low = rp.lower()

        # Ignore generated/runtime dirs
        if low.startswith(".git/") or low.startswith(".venv/"):
            continue
        if low.startswith("data/") or low.startswith("logs/"):
            continue
        if low.startswith("tests/") or "/tests/" in low:
            continue
        if low.startswith("site/"):
            continue

        if low.endswith(".bat") or low.endswith(".py"):
            if any(token in low for token in ("run", "boot", "setup")):
                run_like.append(path)

    violations: list[str] = []
    for path in sorted(run_like):
        rp = rel(path)
        if rp not in ALLOWED_RUN_SCRIPTS:
            violations.append(f"Unapproved run/boot/test script: {rp}")

    return violations


def find_duplicate_boot_stacks() -> list[str]:
    # A "boot stack" is counted when both setup+run entrypoints exist within a folder.
    stack_candidates: list[tuple[str, str]] = []

    for folder in {p.parent for p in ROOT.rglob("*.bat")}:
        rel_folder = rel(folder) if folder != ROOT else "."
        bat_names = {p.name.lower() for p in folder.glob("*.bat")}
        has_setup = any("setup" in name for name in bat_names)
        has_run = any("run" in name for name in bat_names)
        if has_setup and has_run:
            stack_candidates.append((rel_folder, ", ".join(sorted(bat_names))))

    allowed_stack_folders = {".", "_BAT"}
    violations: list[str] = []

    for folder, names in sorted(stack_candidates):
        if folder not in allowed_stack_folders:
            violations.append(f"Duplicate boot stack folder detected: {folder} ({names})")

    existing = {rel(p) for p in ROOT.rglob("*") if p.is_file()}
    for stack_name, required in BOOT_STACK_GROUPS.items():
        missing = sorted(required - existing)
        if missing:
            violations.append(
                f"Required boot stack files missing for {stack_name}: {', '.join(missing)}"
            )

    return violations


def main() -> int:
    checks = [
        find_any_zips,
        find_unapproved_run_scripts,
        find_duplicate_boot_stacks,
    ]

    violations: list[str] = []
    for check in checks:
        violations.extend(check())

    if violations:
        print("[check_structure] FAIL")
        for item in violations:
            print(f" - {item}")
        return 1

    print("[check_structure] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
