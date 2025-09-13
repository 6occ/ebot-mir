#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time, statistics, datetime
from datetime import timezone, timedelta
from sqlalchemy import and_

from config import (
    PAIR, PAUSE_TRADING, REPORT_PERIOD_MIN,
    BASE_ASSET, QUOTE_ASSET
)
from models import SessionLocal, MinMax
from models_trading import SessionT, Order, Fill, Position, Capital
from mexc_client import MexcClient
from notify import send_message, send_error

MSK = timezone(timedelta(hours=3))
now_ts = lambda: int(time.time())
SHOW_DEBUG_DELTA = False  # поставь True, если хочешь видеть Δ EX-DB в отчёте

def fmt_usd(v):
    try:  return f"${v:,.2f}".replace(",", " ")
    except: return f"${float(v):.2f}"

def fmt_qty(v):
    try:  return f"{v:,.2f}".replace(",", " ")
    except: return f"{float(v):.2f}"

def get_last_price():
    cli = MexcClient()
    p = float(cli.price(PAIR))
    return max(0.0, p), cli

def get_bounds_24h():
    s = SessionLocal()
    try:
        cutoff = now_ts() - 86400
        rows = (s.query(MinMax)
                  .filter(and_(MinMax.pair==PAIR, MinMax.time>=cutoff))
                  .all())
        if not rows:
            return (0.0, 0.0)
        min24 = min(r.min for r in rows)
        max24 = max(r.max for r in rows)
        mid24 = statistics.mean(r.mid for r in rows)
        spread = max24 - min24
        lower = max(0.0, mid24 - spread/4.0)
        upper = max(0.0, mid24 + spread/4.0)
        return (lower, upper)
    finally:
        s.close()

def load_state(last_price):
    st = SessionT()
    try:
        cap = st.query(Capital).filter(Capital.pair==PAIR).first()
        pos = st.query(Position).filter(Position.pair==PAIR).first()

        opens = (st.query(Order)
                   .filter(and_(Order.pair==PAIR,
                                Order.status.in_(("NEW","PARTIALLY_FILLED"))))
                   .all())
        buy_open  = [o for o in opens if o.side=="BUY"]
        sell_open = [o for o in opens if o.side=="SELL"]

        # DB reserved (для дебага/сравнения)
        db_reserved_usd = 0.0
        for o in buy_open:
            filled = float(o.filled_qty or 0.0)
            rem = max(0.0, float(o.qty or 0.0) - filled)
            db_reserved_usd += rem * float(o.price or 0.0)

        available_db = float(cap.available_usd) if cap else 0.0
        pos_qty = float(pos.qty) if pos else 0.0
        pos_avg = float(pos.avg) if pos else 0.0
        pos_val = pos_qty * last_price

        # fills stats
        def count_fills(period_sec):
            cutoff = now_ts() - period_sec
            q = st.query(Fill).filter(and_(Fill.pair==PAIR, Fill.ts>=cutoff))
            b = q.filter(Fill.side=="BUY").count()
            s = st.query(Fill).filter(and_(Fill.pair==PAIR, Fill.ts>=cutoff, Fill.side=="SELL")).count()
            return b, s
        m30_b, m30_s = count_fills(1800)
        h1_b,  h1_s  = count_fills(3600)
        d1_b,  d1_s  = count_fills(86400)

        last10 = (st.query(Order)
                    .filter(Order.pair==PAIR)
                    .order_by(Order.created.desc())
                    .limit(10).all())

        return {
            "available_db": available_db,
            "db_reserved_usd": db_reserved_usd,
            "pos_qty": pos_qty,
            "pos_avg": pos_avg,
            "pos_val": pos_val,
            "open_buy_cnt":  len(buy_open),
            "open_sell_cnt": len(sell_open),
            "fills_stats": {
                "30m": (m30_b, m30_s),
                "1h":  (h1_b, h1_s),
                "24h": (d1_b, d1_s),
            },
            "last10": last10,
        }
    finally:
        st.close()

def load_exchange_balances(cli: MexcClient, last_price: float):
    acct = cli.account() or {}
    bals = {b.get("asset"): (float(b.get("free",0)), float(b.get("locked",0)))
            for b in acct.get("balances", [])}
    q_free, q_locked = bals.get(QUOTE_ASSET, (0.0, 0.0))
    b_free, b_locked = bals.get(BASE_ASSET,  (0.0, 0.0))
    equity = (q_free + q_locked) + (b_free + b_locked) * last_price
    # Для отчёта:
    available = q_free
    reserved  = q_locked
    return available, reserved, equity

def render_report():
    now = datetime.datetime.now(MSK)
    last_price, cli = get_last_price()
    lower, upper = get_bounds_24h()
    st = load_state(last_price)

    # Биржевые цифры — источник истины для «Итого/Доступно/Резерв»
    ex_available, ex_reserved, ex_equity = load_exchange_balances(cli, last_price)

    # Заголовок
    head = []
    head.append(f"🧾 Отчёт по {PAIR} (MSK) {now:%Y-%m-%d %H:%M}")
    head.append("")
    head.append(f"Период: {REPORT_PERIOD_MIN} мин")
    head.append(f"Цена: {last_price:.6f}")
    head.append(f"Канал: [{lower:.4f}..{upper:.4f}]" if lower>0 and upper>0 else "Канал: —")

    # Баланс (по бирже)
    trade = "OFF" if PAUSE_TRADING else "ON"
    bal = []
    bal.append("")
    bal.append("💼 Баланс и позиция")
    bal.append(f"Торговля: {trade}")
    bal.append(f"Итого: {fmt_usd(ex_equity)}")
    bal.append(f"Доступно: {fmt_usd(ex_available)}")
    bal.append(f"В ордерах (BUY, резерв): {fmt_usd(ex_reserved)}")
    bal.append(f"Позиция: {fmt_qty(st['pos_qty'])} KAS AVG: {st['pos_avg']:.6f}")
    bal.append(f"Стоимость позиции: {fmt_usd(st['pos_val'])}")

    # (опционально) строка с Δ для дебага
    if SHOW_DEBUG_DELTA:
        equity_db = (st['available_db'] + st['db_reserved_usd'] + st['pos_val'])
        bal.append(f"[Δ debug] EX={fmt_usd(ex_equity)} | DB={fmt_usd(equity_db)} | Δ={fmt_usd(ex_equity - equity_db)}")

    # Статистика
    (m30b, m30s) = st["fills_stats"]["30m"]
    (h1b,  h1s ) = st["fills_stats"]["1h"]
    (d1b,  d1s ) = st["fills_stats"]["24h"]
    stats = []
    stats.append("")
    stats.append("📊 Статистика")
    stats.append(f"BUY={st['open_buy_cnt']} | SELL={st['open_sell_cnt']}")
    stats.append("")
    stats.append("Исполнено:")
    stats.append(f"30м → BUY={m30b} SELL={m30s}")
    stats.append(f"1ч → BUY={h1b} SELL={h1s}")
    stats.append(f"24ч → BUY={d1b} SELL={d1s}")

    # Последние ордера
    tail = []
    tail.append("")
    tail.append("10 последних ордеров:")
    if not st["last10"]:
        tail.append("— нет данных —")
    else:
        for o in st["last10"]:
            t = datetime.datetime.fromtimestamp(int(o.created or now_ts()), MSK).strftime("%H:%M:%S")
            side = o.side or ""
            price = float(o.price or 0.0)
            qty = float(o.qty or 0.0)
            status = o.status or ""
            tail.append(f"{t} | {side} @ {price:.6f} | qty={qty:.6f} | {status}")

    return "\n".join(head + bal + stats + tail)

def main():
    try:
        msg = render_report()
        send_message(msg)
    except Exception as e:
        try: send_error("report", e)
        except: pass
        raise

if __name__ == "__main__":
    main()
