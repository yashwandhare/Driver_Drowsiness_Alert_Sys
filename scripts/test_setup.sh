#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Backend static checks.
backend/.venv/bin/python -m py_compile backend/app/main.py backend/app/config.py backend/app/ws.py

# Backend app wiring checks without network sockets.
backend/.venv/bin/python - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path('backend').resolve()))
from app.main import app

paths = {r.path for r in app.routes}
required = {'/', '/status', '/metrics', '/config', '/frame', '/debug/frame.jpg'}
missing = required - paths
if missing:
    raise SystemExit(f"Missing routes: {sorted(missing)}")
print('Backend routes OK')
PY

# Frontend smoke check.
rg -q "<title>" frontend/index.html
rg -q "mode-debug" frontend/index.html
rg -q "debug/frame.jpg" frontend/index.html

echo "PASS: setup smoke checks completed"
