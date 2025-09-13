#!/usr/bin/env bash
set -euo pipefail
DB="/opt/Ebot/ebot.db"

echo "[MIGRATE] Backup..."
cp -f "$DB" "$DB.bak.$(date +%F_%H%M%S)"

echo "[MIGRATE] Ensure orders.mode..."
sqlite3 "$DB" "PRAGMA foreign_keys=ON; \
  CREATE TABLE IF NOT EXISTS _probe_orders AS SELECT * FROM orders LIMIT 0; \
  SELECT 1; " >/dev/null
HAS_MODE=$(sqlite3 "$DB" "PRAGMA table_info(orders);" | awk -F'|' '$2=="mode"{print 1}')
if [[ "$HAS_MODE" != "1" ]]; then
  sqlite3 "$DB" "ALTER TABLE orders ADD COLUMN mode TEXT DEFAULT '';"
  sqlite3 "$DB" "CREATE INDEX IF NOT EXISTS idx_orders_mode ON orders(mode);"
  echo "  + added orders.mode"
else
  echo "  = orders.mode exists"
fi

echo "[MIGRATE] Ensure fills.mode..."
HAS_MODE2=$(sqlite3 "$DB" "PRAGMA table_info(fills);" | awk -F'|' '$2=="mode"{print 1}')
if [[ "$HAS_MODE2" != "1" ]]; then
  sqlite3 "$DB" "ALTER TABLE fills ADD COLUMN mode TEXT DEFAULT '';"
  sqlite3 "$DB" "CREATE INDEX IF NOT EXISTS idx_fills_mode ON fills(mode);"
  echo "  + added fills.mode"
else
  echo "  = fills.mode exists"
fi

echo "[MIGRATE] VACUUM/optimize..."
sqlite3 "$DB" "VACUUM; PRAGMA optimize;"
echo "[MIGRATE] Done."
