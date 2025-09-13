#!/usr/bin/env bash
set -euo pipefail

DB="/opt/Ebot/ebot.db"

echo "==[ $(date -Is) ]== DB cleanup start"

# SQL: чистим старое и вакуумим
sqlite3 "$DB" <<'SQL'
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 1) Минутные своды: старше 14 дней не держим
DELETE FROM minmax
 WHERE time < strftime('%s','now','-14 days');

-- 2) Ордеры: FILLED/CANCELED старше 60 дней удаляем
DELETE FROM orders
 WHERE status IN ('FILLED','CANCELED')
   AND updated < strftime('%s','now','-60 days');

-- 3) Fills: старше 60 дней чистим
DELETE FROM fills
 WHERE ts < strftime('%s','now','-60 days');

-- 4) Вакуум
VACUUM;
SQL

echo "==[ $(date -Is) ]== DB cleanup done"

# 5) Чистка локальных логов бота: файлы старше 7 дней
find /opt/Ebot/logs -type f -mtime +7 -print -delete 2>/dev/null || true
# заодно удалим пустые подпапки, если остались
find /opt/Ebot/logs -type d -empty -delete 2>/dev/null || true

# 6) Journald — придерживаемся тех же правил
journalctl --vacuum-time=14d || true
journalctl --vacuum-size=150M || true

echo "==[ $(date -Is) ]== Journald vacuum done"
