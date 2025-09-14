#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/Ebot"
PYBIN="$ROOT/venv/bin/python3"
DB="$ROOT/ebot.db"
PAIR="${PAIR:-KASUSDC}"

ok()   { echo "OK"; }
fail() { echo "FAIL"; }
pad()  { printf "%-18s" "$1"; }

declare -A S

# 1) Python syntax (без исполнения)
for comp in BUY:S:buy.py SELL:S:sell.py SYNC:S:sync.py REPORT:S:report.py; do
  IFS=: read label _ file <<<"$comp"
  pad "$label"
  if "$PYBIN" -m py_compile "$ROOT/$file" >/dev/null 2>&1; then
    S["$label"]=ok; ok
  else
    S["$label"]=fail; fail
  fi
done

# 2) Consolidate (сухой прогон)
pad "CONSOLIDATE"
if [[ -x "$ROOT/consolidate.py" ]]; then
  if "$PYBIN" "$ROOT/consolidate.py" --dry-run >/dev/null 2>&1; then
    S["CONSOLIDATE"]=ok; ok
  else
    S["CONSOLIDATE"]=fail; fail
  fi
else
  echo "SKIP (no script)"; S["CONSOLIDATE"]="skip"
fi

# 3) Orchestrator service
pad "ORCHESTRATOR"
if systemctl is-active --quiet ebot.service; then
  S["ORCHESTRATOR"]=ok; ok
else
  S["ORCHESTRATOR"]=fail; fail
fi

# 4) Candles service + свежесть minmax
pad "CANDLES svc"
if systemctl is-active --quiet ebot-candles.service; then
  S["CANDLES_SVC"]=ok; ok
else
  S["CANDLES_SVC"]=fail; fail
fi

pad "CANDLES 24h"
if command -v sqlite3 >/dev/null 2>&1 && [[ -f "$DB" ]]; then
  MAXT=$(sqlite3 "$DB" "SELECT MAX(time) FROM minmax WHERE pair='$PAIR' AND time>=strftime('%s','now','-1 day');")
  NOW=$(date +%s)
  if [[ "$MAXT" =~ ^[0-9]+$ ]]; then
    AGE=$((NOW - MAXT))
    if (( AGE <= 300 )); then
      S["CANDLES_24H"]=ok; ok
    else
      echo "STALE (${AGE}s)"; S["CANDLES_24H"]="stale"
    fi
  else
    echo "NO DATA"; S["CANDLES_24H"]="nodata"
  fi
else
  echo "SKIP"; S["CANDLES_24H"]="skip"
fi

# 5) DB schema sanity
pad "DB schema"
if [[ -f "$DB" ]] && sqlite3 "$DB" ".schema orders" >/dev/null 2>&1; then
  S["DB"]=ok; ok
else
  S["DB"]=fail; fail
fi

echo
echo "===== SUMMARY ====="
summ() {
  local k="$1" t="$2"
  case "${S[$k]:-skip}" in
    ok) st="OK" ;;
    fail) st="FAIL" ;;
    stale) st="STALE" ;;
    nodata) st="NO DATA" ;;
    *) st="SKIP" ;;
  esac
  printf "%-12s : %s\n" "$t" "$st"
}
summ BUY          "Buy"
summ SELL         "Sell"
summ SYNC         "Sync"
summ REPORT       "Report"
summ CONSOLIDATE  "Consolidate"
summ ORCHESTRATOR "Orchestrator"
summ CANDLES_SVC  "Candles svc"
summ CANDLES_24H  "Candles 24h"
summ DB           "DB schema"
echo "===================="

# --- exit code summary ---
bad=0
for k in "${!S[@]}"; do
  case "${S[$k]}" in
    fail|stale|nodata) bad=1 ;;
  esac
done
exit $bad
