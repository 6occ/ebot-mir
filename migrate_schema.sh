#!/usr/bin/env bash
set -euo pipefail

DB="/opt/Ebot/ebot.db"

echo "[MIGRATE] Backup DB..."
cp -a "$DB" "$DB.bak.$(date +%F_%H%M%S)"

# helper: check if column exists
has_col() {
  local table="$1" col="$2"
  sqlite3 "$DB" "PRAGMA table_info($table);" | awk -F'|' -v c="$2" '$2==c{found=1} END{exit found?0:1}'
}

echo "[MIGRATE] Ensure capital.realized_pnl..."
if ! has_col capital realized_pnl; then
  sqlite3 "$DB" "ALTER TABLE capital ADD COLUMN realized_pnl REAL NOT NULL DEFAULT 0;"
  echo "  + added capital.realized_pnl"
else
  echo "  = capital.realized_pnl exists"
fi

echo "[MIGRATE] Ensure table position..."
sqlite3 "$DB" "
CREATE TABLE IF NOT EXISTS position (
  pair    TEXT PRIMARY KEY,
  qty     REAL NOT NULL DEFAULT 0,
  avg     REAL NOT NULL DEFAULT 0,
  updated INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
"

echo "[MIGRATE] Seed baseline rows for KASUSDC..."
# capital: если строки нет — создадим; если есть — только убедимся, что realized_pnl есть (уже выше)
sqlite3 "$DB" "
INSERT OR IGNORE INTO capital(pair, limit_usd, available_usd, updated, realized_pnl)
VALUES ('KASUSDC', 1000.0, 1000.0, strftime('%s','now'), 0.0);
"

# position: если строки нет — создадим нулевую
sqlite3 "$DB" "
INSERT OR IGNORE INTO position(pair, qty, avg, updated)
VALUES ('KASUSDC', 0.0, 0.0, strftime('%s','now'));
"

echo "[MIGRATE] Useful indexes..."
sqlite3 "$DB" "CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created);"

echo "[MIGRATE] Optimize..."
sqlite3 "$DB" "PRAGMA optimize;"

echo "[MIGRATE] Done."
