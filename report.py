#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math, time
from datetime import datetime, timezone, timedelta

from config import (
    PAIR, BASE_ASSET, QUOTE_ASSET,
    START_CAPITAL_USD, MAKER_FEE_PCT,
    REPORT_PERIOD_MIN,
)

from models import SessionLocal, init_db
from models_trading import (
    SessionT, init_trading_db,
    Order, Position, Capital,
)

MSK = timezone(timedelta(hours=3))
now_ts = lambda: int(time.time())

def _round(x, n=6):
    try:
        return round(float(x), n)
    except Exception:
        return 0.0

def _fetch_last_price(sess_core) -> float:
    # Берём последнюю свечу из core-DB (models.MinMax)
    from models import MinMax
    row = (sess_core.query(MinMax)
           .filter(MinMax.pair == PAIR)
           .order_by(MinMax.time.desc())
           .first())
    if not row:
        return 0.0
    return float(row.close if row.close else row.mid or 0.0)

def _equity_snapshot(sessT, last_price: float):
    cap = sessT.query(Capital).filter(Capital.pair == PAIR).first()
    pos = sessT.query(Position).filter(Position.pair == PAIR).first()
    available = float(cap.available_usd) if cap else 0.0

    # Резерв под открытые BUY
    open_buy = (sessT.query(Order)
                .filter(Order.pair==PAIR,
                        Order.side=="BUY",
                        Order.status.in_(("NEW","PARTIALLY_FILLED"))).all())
    reserved = sum(float(o.reserved or 0.0) for o in open_buy)

    qty = float(pos.qty) if pos else 0.0
    avg = float(pos.avg) if pos else 0.0
    pos_val = qty * float(last_price)

    equity = available + reserved + pos_val
    return {
        "available": available,
        "reserved": reserved,
        "qty": qty,
        "avg": avg,
        "pos_val": pos_val,
        "equity": equity,
    }

def _try_sum_amount(sessT, table: str, col_ts: str, since_ts: int):
    """
    Возвращает (sum_buy, sum_sell) по таблице table,
    где сумма считается как Σ(price*qty) по каждому side.
    """
    from sqlalchemy import text
    sql = text(f"""
        SELECT side, SUM(CAST(price AS REAL) * CAST(qty AS REAL)) AS amt
        FROM {table}
        WHERE pair = :pair AND {col_ts} >= :since
        GROUP BY side
    """)
    rows = sessT.execute(sql, {"pair": PAIR, "since": since_ts}).fetchall()
    sums = {"BUY": 0.0, "SELL": 0.0}
    for side, amt in rows:
        if side in ("BUY","SELL") and amt is not None:
            sums[side] = float(amt)
    return sums["BUY"], sums["SELL"]

def _pnl_since(sessT, since_minutes: int) -> float:
    """
    Реализованный PNL за окно (минут).
    Пробует:
      - таблицы: fills, trades
      - колонки времени: ts, time, created
    PNL = ΣSELL - ΣBUY - fee*(ΣSELL+ΣBUY)
    """
    since_ts = now_ts() - since_minutes * 60
    tables = ["fills", "trades"]
    cols   = ["ts", "time", "created"]

    last_err = None
    for t in tables:
        for c in cols:
            try:
                buy_sum, sell_sum = _try_sum_amount(sessT, t, c, since_ts)
                gross = sell_sum - buy_sum
                fees  = MAKER_FEE_PCT * (abs(sell_sum) + abs(buy_sum))
                return gross - fees
            except Exception as e:
                last_err = e
                continue
    # Если ничего не нашли/не удалось — считаем 0, но не падаем отчётом
    return 0.0

def _fmt_money(x: float) -> str:
    s = f"{x:,.2f}".replace(",", "_").replace("_", " ")
    return s

def _fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"

def main():
    init_db()
    init_trading_db()

    sess_core = SessionLocal()
    sessT     = SessionT()
    try:
        last = _fetch_last_price(sess_core)
        snap = _equity_snapshot(sessT, last)

        # PNL окна
        pnl_1h   = _pnl_since(sessT, 60)
        pnl_24h  = _pnl_since(sessT, 1440)
        # PNL всего = текущее equity - стартовый капитал
        pnl_all  = snap["equity"] - START_CAPITAL_USD

        # Для «процентов» используем относительную базу:
        # за окна — от START_CAPITAL_USD (как точка отсчёта в README),
        # «Всего» — тоже от START_CAPITAL_USD.
        pct_1h  = pnl_1h  / START_CAPITAL_USD if START_CAPITAL_USD else 0.0
        pct_24h = pnl_24h / START_CAPITAL_USD if START_CAPITAL_USD else 0.0
        pct_all = pnl_all / START_CAPITAL_USD if START_CAPITAL_USD else 0.0

        # Канал (берём по формуле из minmax за 24ч)
        from statistics import mean
        from models import MinMax
        cutoff = now_ts() - 86400
        rows = (sess_core.query(MinMax)
                .filter(MinMax.pair==PAIR, MinMax.time>=cutoff).all())
        if rows:
            mn = min(r.min for r in rows)
            mx = max(r.max for r in rows)
            mid24 = mean(r.mid for r in rows)
            spread = max(0.0, (mx - mn))
            lower = max(0.0, mid24 - spread/4.0)
            upper = max(0.0, mid24 + spread/4.0)
        else:
            lower = upper = 0.0

        # Текст отчёта
        ts = datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
        lines = []
        lines.append(f"🧾 Отчёт по {PAIR} (MSK) {ts}\n")
        lines.append(f"Период: {REPORT_PERIOD_MIN} мин")
        lines.append(f"Цена: {last:.6f}")
        if lower and upper:
            lines.append(f"Канал: [{_round(lower,6)}..{_round(upper,6)}]\n")
        else:
            lines.append("Канал: n/a\n")

        lines.append("💼 Баланс и позиция")
        lines.append("Торговля: ON")
        lines.append(f"Итого: ${_fmt_money(snap['equity'])}")
        lines.append(f"Доступно: ${_fmt_money(snap['available'])}")
        lines.append(f"В ордерах (BUY, резерв): ${_fmt_money(snap['reserved'])}")
        lines.append(f"Позиция: {_round(snap['qty'],6)} {BASE_ASSET} AVG: {_round(snap['avg'],6)}")
        lines.append(f"Стоимость позиции: ${_fmt_money(snap['pos_val'])}\n")

        # Блок PNL — как ты просил
        lines.append("PNL")
        lines.append(f"1 час: {_fmt_money(pnl_1h)} ({_fmt_pct(pct_1h)})")
        lines.append(f"24 часа: {_fmt_money(pnl_24h)} ({_fmt_pct(pct_24h)})")
        lines.append(f"Всего: {_fmt_money(pnl_all)} ({_fmt_pct(pct_all)})\n")

        # Статистика (как было)
        buy_cnt = (sessT.query(Order)
                   .filter(Order.pair==PAIR, Order.side=="BUY",
                           Order.status.in_(("NEW","PARTIALLY_FILLED"))).count())
        sell_cnt = (sessT.query(Order)
                    .filter(Order.pair==PAIR, Order.side=="SELL",
                            Order.status.in_(("NEW","PARTIALLY_FILLED"))).count())
        lines.append("📊 Статистика")
        lines.append(f"BUY={buy_cnt} | SELL={sell_cnt}\n")

        # Последние 10 ордеров
        last_orders = (sessT.query(Order)
                       .filter(Order.pair==PAIR)
                       .order_by(Order.created.desc())
                       .limit(10).all())
        lines.append("10 последних ордеров:")
        for o in last_orders:
            t = datetime.fromtimestamp(int(o.created), MSK).strftime("%H:%M:%S")
            lines.append(f"{t} | {o.side} @ {_round(o.price,6)} | qty={_round(o.qty,6)} | {o.status}")

        print("\n".join(lines))

    finally:
        sessT.close()
        sess_core.close()

if __name__ == "__main__":
    main()
