from __future__ import annotations

import argparse
import sys
import time
import urllib.request


def wait_for_url(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                resp.read(16)
            return True
        except Exception:
            time.sleep(1)
    return False


def main() -> int:
    p = argparse.ArgumentParser(description="Wait for an HTTP endpoint to become available")
    p.add_argument("url")
    p.add_argument("--timeout", type=float, default=40.0)
    args = p.parse_args()

    ok = wait_for_url(args.url, args.timeout)
    print("READY" if ok else "NOT_READY")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
