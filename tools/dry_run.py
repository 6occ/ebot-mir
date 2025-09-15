#!/usr/bin/env python3
import sqlite3
import argparse
import time
from datetime import datetime

try:
    # используем общие функции отчёта (чистые)
    from reports.core import compute_channel_24h, calc_pnl_blocks, fetch_close_at_or_before
except Exception:
    compute_channel_24h = None
    calc_pnl_blocks = None
    fetch_close_at_or_before = None

def utc_ts() -> int: return int(time.time())

def fetch_last_rows(conn, pair: str, limit: int = 50):
    cur = conn.cursor()
    try:
        cur.execute("SELECT time, min, max, mid, open, close FROM minmax WHERE pair=? ORDER BY time DESC LIMIT ?", (pair, limit))
        candles = cur.fetchall()[::-1]
    except Exception:
        candles = []
    try:
        cur.execute("SELECT side, price, qty, status FROM orders WHERE pair=? ORDER BY COALESCE(updated,created) DESC LIMIT ?", (pair, limit))
        orders = cur.fetchall()
    except Exception:
        orders = []
    try:
        cur.execute("SELECT qty, avg FROM positions WHERE pair=? ORDER BY COALESCE(updated,time) DESC LIMIT 1", (pair,))
        r = cur.fetchone()
        pos = (float(r[0] or 0.0), float(r[1] or 0.0)) if r else (0.0, 0.0)
    except Exception:
        pos = (0.0, 0.0)
    last = None
    try:
        cur.execute("SELECT close FROM minmax WHERE pair=? ORDER BY time DESC LIMIT 1", (pair,))
        row = cur.fetchone()
        last = float(row[0]) if row else None
    except Exception:
        last = None
    return candles, orders, pos, last

def main():
    ap = argparse.ArgumentParser(description="Offline dry-run: мини-срез данных, без записи в БД")
    ap.add_argument("--db", default="ebot.db")
    ap.add_argument("--pair", default="KASUSDC")
    ap.add_argument("--limit", type=int, default=50, help="сколько последних свечей/ордеров читать")
    ap.add_argument("--show-report", action="store_true", help="дополнительно собрать текст отчёта (reports.core)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    candles, orders, (qty, avg), last = fetch_last_rows(conn, args.pair, args.limit)

    print(f"DRY-RUN @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"PAIR={args.pair}")
    print(f"candles_read={len(candles)} orders_read={len(orders)} position_qty={qty} avg={avg}")
    print(f"last={last if last is not None else 'n/a'}")

    # Чистые расчёты (если доступен модуль reports.core)
    if compute_channel_24h and candles:
        ch = compute_channel_24h(candles)
        if ch:
            print(f"channel24h: lower={ch['lower']:.6f} upper={ch['upper']:.6f} mid={ch['mid']:.6f} spread={ch['spread']:.6f}")
    if calc_pnl_blocks and last is not None:
        now = utc_ts()
        px_1h = px_24h = None
        if fetch_close_at_or_before:
            _, px_1h = fetch_close_at_or_before(conn, args.pair, now-3600)
            _, px_24h = fetch_close_at_or_before(conn, args.pair, now-24*3600)
        pnl1_abs, pnl1_pct, pnl24_abs, pnl24_pct, total_abs, total_pct = calc_pnl_blocks(last or 0.0, qty, avg, px_1h, px_24h)
        print("pnl:", pnl1_abs, pnl1_pct, pnl24_abs, pnl24_pct, total_abs, total_pct)

    if args.show_report:
        try:
            from reports import run as run_report
            txt = run_report("daily")
            print("----- REPORT (preview) -----")
            print("\n".join(txt.splitlines()[:40]))
            print("----------------------------")
        except Exception as e:
            print(f"report preview error: {e}")

if __name__ == "__main__":
    main()
