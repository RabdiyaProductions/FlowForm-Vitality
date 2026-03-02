from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

APP_ID = "FlowForm-app"
PORT_RANGE = range(5400, 5500)
DEFAULT_PORT = 5410
ROOT_DIR = Path(__file__).resolve().parent
PORTS_FILE = ROOT_DIR / "PORTS.json"
ACTIVE_PORTS_FILE = ROOT_DIR / "ACTIVE_PORTS.json"


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex((host, port)) != 0


def parse_preferred_port() -> int | None:
    if not PORTS_FILE.exists():
        return None
    try:
        data = json.loads(PORTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    candidates = []
    if isinstance(data, dict):
        candidates.append(data.get(APP_ID))
        apps = data.get("apps")
        if isinstance(apps, dict):
            candidates.append(apps.get(APP_ID))

    for candidate in candidates:
        if isinstance(candidate, int) and 1 <= candidate <= 65535:
            return candidate
    return None


def resolve_port() -> int:
    preferred = parse_preferred_port()
    if preferred is None:
        preferred = DEFAULT_PORT

    if is_port_free(preferred):
        return preferred

    for port in PORT_RANGE:
        if is_port_free(port):
            return port
    raise RuntimeError("No free port found in range 5400-5499")


def write_active_ports(port: int) -> None:
    payload = {
        "app": APP_ID,
        "port": port,
        "updated_at": int(time.time()),
    }
    ACTIVE_PORTS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def wait_for_port(port: int, timeout: float = 30.0, host: str = "127.0.0.1") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_port_free(port, host=host):
            return True
        time.sleep(0.25)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve and monitor FlowForm boot port")
    parser.add_argument("--print-port", action="store_true", help="Print resolved port")
    parser.add_argument("--write-active", action="store_true", help="Write ACTIVE_PORTS.json")
    parser.add_argument("--wait", action="store_true", help="Wait for a port to become open")
    parser.add_argument("--port", type=int, default=None, help="Port used with --wait")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout for --wait")
    args = parser.parse_args()

    if args.wait:
        if args.port is None:
            print("--wait requires --port", file=sys.stderr)
            return 2
        ok = wait_for_port(args.port, args.timeout)
        return 0 if ok else 1

    port = resolve_port()
    if args.write_active:
        write_active_ports(port)
    if args.print_port:
        print(port)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
