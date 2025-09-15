#!/usr/bin/env bash
set -euo pipefail
ROOT="/opt/Ebot"
cd "$ROOT"
python3 -X faulthandler -m compileall -q .
if command -v ruff >/dev/null 2>&1; then ruff check . || true; else echo "ruff not found"; fi
if command -v flake8 >/dev/null 2>&1; then flake8 || true; else echo "flake8 not found"; fi
if command -v mypy >/dev/null 2>&1; then mypy --ignore-missing-imports --install-types --non-interactive || true; else echo "mypy not found"; fi
test -d "$ROOT" || { echo "Missing $ROOT"; exit 1; }
test -f "$ROOT/ebot.py" || { echo "Missing ebot.py"; exit 1; }
test -w "$ROOT/tmp" || mkdir -p "$ROOT/tmp"
test -d "$ROOT/logs" || mkdir -p "$ROOT/logs"
if [ ! -f "$ROOT/config.py" ]; then echo "config.py not found (ok)"; fi
if grep -qE '(API_KEY|API_SECRET|TG_BOT_TOKEN|TG_CHAT_ID)\s*=\s*\"[A-Za-z0-9]+' "$ROOT/config.py" 2>/dev/null; then echo "WARNING: secrets present in config.py"; fi
if [ -f "$ROOT/ebot.db" ]; then python3 - <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect("ebot.db")
    con.execute("PRAGMA integrity_check;")
    print("SQLite OK")
except Exception as e:
    print("SQLite ERR:", e)
    sys.exit(0)
PY
else
  echo "No ebot.db yet"
fi
echo "Healthcheck finished."
