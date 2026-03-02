from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description="Read port from ACTIVE_PORTS.json")
    p.add_argument("--file", default="ACTIVE_PORTS.json")
    p.add_argument("--default", type=int, default=5410)
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(args.default)
        return 0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        print(args.default)
        return 0

    port = data.get("port")
    if isinstance(port, int) and 1 <= port <= 65535:
        print(port)
        return 0

    try:
        port_i = int(port)
        if 1 <= port_i <= 65535:
            print(port_i)
            return 0
    except Exception:
        pass

    print(args.default)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
