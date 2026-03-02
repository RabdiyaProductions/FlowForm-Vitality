from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_http(url: str, timeout_seconds: float = 20.0) -> bool:
    end = time.time() + timeout_seconds
    while time.time() < end:
        try:
            with urlopen(url, timeout=2) as response:
                return response.status == 200
        except Exception:
            time.sleep(0.25)
    return False


def test_smoke_server_boot_and_health() -> None:
    repo_root = Path(__file__).resolve().parent
    port = find_free_port()

    process = subprocess.Popen(
        [sys.executable, str(repo_root / "run_server.py"), "--port", str(port)],
        cwd=str(repo_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        health_url = f"http://127.0.0.1:{port}/health"
        assert wait_for_http(health_url), "Server did not become healthy in time"
        with urlopen(health_url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload["status"] in {"ok", "degraded"}
            assert "version" in payload
            assert "time" in payload
            assert isinstance(payload["db_ok"], bool)
            assert payload["provider_status"] in {"configured", "not_configured"}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
