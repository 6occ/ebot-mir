#!/usr/bin/env bash
set -euo pipefail

ROOT=/opt/Ebot
cd "$ROOT"

banner(){ echo; echo "===== $* ====="; }

banner "GIT HEAD & BRANCH"
git rev-parse --short=12 HEAD 2>/dev/null || true
git branch --show-current 2>/dev/null || true
git log --oneline -5 2>/dev/null || true

banner "mexc_client.py — сигнатуры и WS/stream"
grep -nE 'class +MexcClient|def +_?(get|post)|websocket|ws|stream|subscribe|listen|kline|trades' mexc_client.py || true
echo "--- начало файла (до 180 строк) ---"
sed -n '1,180p' mexc_client.py

banner "candles.py — WS и обработка минутных свечей"
grep -nE 'WS|websocket|wss|subscribe|send|recv|kline|minmax|INSERT|upsert' candles.py || true
echo "--- начало файла (до 160 строк) ---"
sed -n '1,160p' candles.py

banner "ebot.py — запуск consolidate/report"
grep -nE 'consolidate|report|run_cmd|task_' ebot.py || true
sed -n '1,140p' ebot.py

banner "report.py — где шлём в Telegram через notify"
grep -nE 'notify|send_message|send_error|PNL|Баланс|Позици' report.py || true
sed -n '1,140p' report.py

banner "notify.py — наличие send_message/send_error"
sed -n '1,140p' notify.py 2>/dev/null || echo "no notify.py"

banner "config.py (маскируем секреты)"
sed -E \
 -e 's/(API_KEY\s*=\s*).*/\1"***"/' \
 -e 's/(API_SECRET\s*=\s*).*/\1"***"/' \
 -e 's/(TG_BOT_TOKEN|TELEGRAM_TOKEN\s*=\s*).*/\1"***"/' \
 -e 's/(TG_CHAT_ID|TELEGRAM_CHAT_ID\s*=\s*).*/\1"***"/' \
 -e 's/(WEBHOOK_URL\s*=\s*).*/\1"***"/' \
 -e 's/(WEBHOOK_TOKEN\s*=\s*).*/\1"***"/' \
 config.py 2>/dev/null || echo "no config.py"
sed -n '1,120p' config.example.py 2>/dev/null || true

banner "Сервисы — краткий статус"
systemctl is-active --quiet ebot.service && echo "ebot: active" || echo "ebot: NOT active"
systemctl is-active --quiet ebot-candles.service && echo "candles: active" || echo "candles: NOT active"

banner "Журналы (последние 40 строк) ebot.service — consolidate/report"
journalctl -u ebot.service -n 200 --no-pager | egrep -i 'consolidate|report|ERROR|STDOUT|STDERR' || true

banner "Consolidate — сухой прогон"
"$ROOT/venv/bin/python3" -u "$ROOT/consolidate.py" --dry-run || true
