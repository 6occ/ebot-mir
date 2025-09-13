#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import math, time
from datetime import timezone, timedelta
from statistics import mean
from sqlalchemy import and_
from config import (
    PAIR,
    MIN_ORDER_USD,
    SELL_SPLIT,
    SELL_MIN_GAIN,
    SELL_MICROSHIFT,
    BASE_ASSET,
)
from models_trading import SessionT, Order, Position, init_trading_db
from models import SessionLocal, MinMax
from mexc_client import MexcClient
from notify import send_error

MSK = timezone(timedelta(hours=3))
now_ts = lambda: int(time.time())

def floor6(x: float) -> float:
    return math.floor(float(x) * 1_000_000) / 1_000_000

def round_price(p: float) -> float:
    return floor6(p)

def last_price(client: MexcClient) -> float:
    p = float(client.price(PAIR))
    return max(0.0, p)

def get_24h_bounds():
    s = SessionLocal()
    try:
        cutoff = now_ts() - 86400
        rows = (s.query(MinMax)
                .filter(and_(MinMax.pair == PAIR, MinMax.time >= cutoff))
                .all())
        if not rows:
            return 0.0, 0.0, 0.0
        min24 = min(r.min for r in rows)
        max24 = max(r.max for r in rows)
        mid24 = mean(r.mid for r in rows)
        spread = max24 - min24
        lower = round_price(mid24 - spread/4.0)
        upper = round_price(mid24 + spread/4.0)
        return lower, upper, mid24
    finally:
        s.close()

def fetch_position(sessT):
    pos = sessT.query(Position).filter(Position.pair==PAIR).first()
    if not pos:
        return 0.0, 0.0
    return float(pos.qty or 0.0), float(pos.avg or 0.0)

def open_sell_remaining(sessT):
    opens = (sessT.query(Order)
             .filter(and_(Order.pair==PAIR,
                          Order.side=="SELL",
                          Order.status.in_(("NEW","PARTIALLY_FILLED"))))
             .all())
    rem = 0.0
    for o in opens:
        q = max(0.0, float(o.qty or 0.0) - float(o.filled_qty or 0.0))
        rem += q
    return floor6(rem), opens

def exchange_free_kas(cli: MexcClient) -> float:
    acct = cli.account() or {}
    bals = acct.get("balances") or []
    for b in bals:
        if b.get("asset") == BASE_ASSET:
            try:
                return max(0.0, float(b.get("free") or 0.0))
            except Exception:
                return 0.0
    return 0.0

def avoid_collision_price(sessT, target_p: float) -> float:
    p = round_price(target_p)
    exists = (sessT.query(Order)
              .filter(and_(Order.pair==PAIR,
                           Order.side=="SELL",
                           Order.status.in_(("NEW","PARTIALLY_FILLED")),
                           Order.price==p))
              .first())
    if exists:
        return round_price(p + SELL_MICROSHIFT)
    return p

def place_limit_sell(cli: MexcClient, sessT, price: float, qty: float):
    price = round_price(price)
    qty   = floor6(qty)
    if qty <= 0 or price <= 0:
        return False
    if qty * price < MIN_ORDER_USD:
        return False
    # биржа
    resp = cli.place_order(PAIR, "SELL", price, qty)
    oid  = str(resp.get("orderId") or f"SELL_{now_ts()}")
    # локально
    o = Order(id=oid, pair=PAIR, side="SELL", price=price, qty=qty,
              filled_qty=0.0, status="NEW",
              created=now_ts(), updated=now_ts(),
              paper=False, reserved=0.0, mode="GRID")
    sessT.merge(o)
    sessT.commit()
    return True

def place_sell_orders():
    init_trading_db()
    cli = MexcClient()
    sessT = SessionT()
    try:
        last = last_price(cli)
        _, upper, _ = get_24h_bounds()
        pos_qty, pos_avg = fetch_position(sessT)
        open_rem, _ = open_sell_remaining(sessT)

        # Локально доступно с учётом уже открытых SELL
        local_avail = floor6(max(0.0, pos_qty - open_rem))
        # Фактически свободно на бирже
        exch_free = floor6(exchange_free_kas(cli))
        # Берём минимум, чтобы не ловить Oversold
        avail_qty = floor6(min(local_avail, exch_free))

        if avail_qty <= 0:
            return  # нечего продавать

        # Цели: верх канала и середина last..upper (или last, если upper неизвестен)
        target_upper = upper if upper > 0 else last
        target_mid   = (last + target_upper) / 2.0 if target_upper > 0 else last

        # «Пол»: не ниже avg*(1+SELL_MIN_GAIN)
        min_ok = pos_avg * (1.0 + SELL_MIN_GAIN) if pos_avg > 0 else 0.0
        p1 = max(target_upper, min_ok)
        p2 = max(target_mid,   min_ok)

        # Анти-коллизия (и лёгкое разведение цен)
        p1 = avoid_collision_price(sessT, p1)
        p2 = avoid_collision_price(sessT, p2 if abs(p2 - p1) > 1e-12 else (p2 + SELL_MICROSHIFT))

        # Разбиение объёма
        r = max(0.0, min(1.0, SELL_SPLIT))
        q1 = floor6(avail_qty * r)
        q2 = floor6(avail_qty - q1)

        placed_any = False
        if q1 > 0 and q1 * p1 >= MIN_ORDER_USD:
            placed_any |= place_limit_sell(cli, sessT, p1, q1)
        if q2 > 0 and q2 * p2 >= MIN_ORDER_USD:
            placed_any |= place_limit_sell(cli, sessT, p2, q2)

        # Если дробление не прошло по мин. сумме — пробуем одним ордером на p1
        if (not placed_any) and (avail_qty * p1 >= MIN_ORDER_USD):
            place_limit_sell(cli, sessT, p1, avail_qty)

    except Exception as e:
        try: send_error("sell.place", e)
        except: pass
        raise
    finally:
        sessT.close()

if __name__ == "__main__":
    place_sell_orders()

# === shim: ensure ebot can call sell.main() ===
def main():
    try:
        place_sell_orders()
    except Exception as e:
        try:
            send_error("sell.tick", e)
        except Exception:
            pass
        raise
