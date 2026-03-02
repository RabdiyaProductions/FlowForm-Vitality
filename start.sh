#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

python -m pip install -r requirements.txt

HOST="${HOST:-0.0.0.0}"
PORT_VALUE="${PORT:-5410}"

exec python run_server.py --host "$HOST" --port "$PORT_VALUE"
