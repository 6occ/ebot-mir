#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import sqlite3
import time
from datetime import datetime

def _period_from_args(ts_from: int|None, ts_to: int|None, last_days: int|None):
    if last_days:
        now = int(time.time())
        ts_from = now - last_days*24*3600
        ts_to = now
    return ts_from, ts_to

def calc_aggregates(conn: sqlite3.Connection, pair: str, ts_from: int|None, ts_to: int|None) -> dict:
    cur = conn.cursor()
    q = "SELECT side, price, qty, COALESCE(fee,0.0) AS fee FROM fills WHERE pair=?"
    params = [pair]
    if ts_from is not None:
        q += " AND ts>=?"; params.append(int(ts_from))
    if ts_to is not None:
        q += " AND ts<=?"; params.append(int(ts_to))
    q += " ORDER BY ts ASC, id ASC"
    usdc_in_from_sells = 0.0
    usdc_out_to_buys = 0.0
    fee_quote_total = 0.0
    kas_net = 0.0
    try:
        for side, price, qty, fee in cur.execute(q, params):
            side = str(side or "").upper()
            price = float(price or 0.0)
            qty = float(qty or 0.0)
            fee = float(fee or 0.0)
            if side == "BUY":
                usdc_out_to_buys += price * qty
                kas_net += qty
                fee_quote_total += fee
            elif side == "SELL":
                usdc_in_from_sells += price * qty
                kas_net -= qty
                fee_quote_total += fee
    except Exception:
        pass
    usdc_net_after_fee = usdc_in_from_sells - usdc_out_to_buys - fee_quote_total
    return dict(
        usdc_in_from_sells=usdc_in_from_sells,
        usdc_out_to_buys=usdc_out_to_buys,
        fee_quote_total=fee_quote_total,
        kas_net=kas_net,
        usdc_net_after_fee=usdc_net_after_fee,
    )

def main():
    ap = argparse.ArgumentParser(description="Aggregates from fills: USDC in/out, fees, KAS/USDC net")
    ap.add_argument("--db", default="ebot.db")
    ap.add_argument("--pair", default="KASUSDC")
    ap.add_argument("--from", dest="ts_from", type=int, default=None, help="unix ts from (inclusive)")
    ap.add_argument("--to", dest="ts_to", type=int, default=None, help="unix ts to (inclusive)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--last-day", action="store_true", help="последние 24 часа")
    group.add_argument("--last-week", action="store_true", help="последние 7 дней")
    ap.add_argument("--last-days", type=int, default=None, help="последние N дней (альтернатива --from/--to)")
    args = ap.parse_args()

    last_days = args.last_days or (1 if args.last_day else 7 if args.last_week else None)
    ts_from, ts_to = _period_from_args(args.ts_from, args.ts_to, last_days)

    conn = sqlite3.connect(args.db)
    agg = calc_aggregates(conn, args.pair, ts_from, ts_to)

    header = f"Summary @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    period = (
        f"from={ts_from or '-'} to={ts_to or '-'}"
        if (ts_from or ts_to) else (f"last_days={last_days}" if last_days else "all_time")
    )
    print(header)
    print(f"PAIR={args.pair} ({period})")
    print(f"usdc_in_from_sells={agg['usdc_in_from_sells']:.2f}")
    print(f"usdc_out_to_buys={agg['usdc_out_to_buys']:.2f}")
    print(f"fee_quote_total={agg['fee_quote_total']:.2f}")
    print(f"kas_net={agg['kas_net']:.6f}")
    print(f"usdc_net_after_fee={agg['usdc_net_after_fee']:.2f}")

if __name__ == "__main__":
    main()
