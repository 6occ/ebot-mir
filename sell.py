#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import time
from statistics import mean
from datetime import timezone, timedelta
from sqlalchemy import and_

from config import (
    PAIR,
    MIN_ORDER_USD,

    # SELL strategy
    SELL_SPLIT,        # доля на upper (0..1)
    SELL_MIN_GAIN,     # минимум avg + gain
    SELL_MICROSHIFT,   # микросдвиг, если уровень занят

    # опционально (не критично для логики, но полезно)
    # BASE_ASSET, QUOTE_ASSET
)

from models import SessionLocal, MinMax
from models_trading import (
    SessionT, Order, Position, init_trading_db
)
from mexc_client import MexcClient
from notify import send_error

MSK = timezone(timedelta(hours=3))
def now_ts():
    return int(time.time())

def _floor6(x: float) -> float:
    return math.floor(float(x) * 1_000_000) / 1_000_000

def last_price(cli: MexcClient) -> float:
    return float(cli.price(PAIR))

def channel_24h() -> tuple[float, float, float]:
    """
    Возвращает (lower, upper, mid24) как mid±spread/4 по таблице minmax за 24ч.
    Если данных нет — (0,0,0).
    """
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
        spread = max(0.0, max24 - min24)
        lower = max(0.0, mid24 - spread/4.0)
        upper = max(0.0, mid24 + spread/4.0)
        return lower, upper, mid24
    finally:
        s.close()

def get_position(sessT: SessionT) -> tuple[float, float]:
    pos = sessT.query(Position).filter(Position.pair == PAIR).first()
    if not pos:
        return 0.0, 0.0
    return float(pos.qty or 0.0), float(pos.avg or 0.0)

def get_open_sells(sessT: SessionT) -> list[Order]:
    return (sessT.query(Order)
            .filter(and_(Order.pair == PAIR,
                         Order.side == "SELL",
                         Order.status.in_(("NEW", "PARTIALLY_FILLED"))))
            .all())

def exchange_free_base(cli: MexcClient) -> float:
    """
    Возвращает свободный базовый ассет (KAS) по данным биржи.
    """
    acct = cli.account() or {}
    for b in (acct.get("balances") or []):
        # Некоторые клиенты отдают как {'asset': 'KAS', 'free': '...', 'locked': '...'}
        if str(b.get("asset")).upper() in ("KAS",):
            try:
                return float(b.get("free") or 0.0)
            except Exception:
                return 0.0
    return 0.0

def place_limit_sell(cli: MexcClient, sessT: SessionT, price: float, qty: float) -> bool:
    """
    Отправляет лимитную продажу и фиксирует в локальной БД.
    """
    price = max(0.0, float(price))
    qty = max(0.0, float(qty))
    if price <= 0 or qty <= 0:
        return False

    # проверка минимального номинала
    notional = price * qty
    if notional < MIN_ORDER_USD:
        return False

    # квантование количества (6 знаков)
    qty = _floor6(qty)
    if qty <= 0:
        return False

    resp = cli.place_order(PAIR, "SELL", price=price, qty=qty)
    oid = str(resp.get("orderId") or f"SELL_{now_ts()}")

    o = Order(
        id=oid, pair=PAIR, side="SELL",
        price=float(price), qty=float(qty),
        status="NEW", created=now_ts(), updated=now_ts(),
        paper=False, reserved=0.0, filled_qty=0.0, mode="GRID",
    )
    sessT.merge(o)
    sessT.commit()
    return True

def build_sell_prices(pos_avg: float, last: float, upper: float) -> tuple[float, float]:
    """
    Возвращает (p_mid, p_upper):
      - p_mid  = середина между last и upper
      - p_upper= сам upper
    Обе цены поджимаются снизу на минимум avg*(1+SELL_MIN_GAIN).
    """
    # если верх канала неизвестен — подстрахуемся
    if not upper or upper <= 0.0:
        upper = last

    p_mid = (last + upper) / 2.0
    floor_price = pos_avg * (1.0 + float(SELL_MIN_GAIN))

    p_mid   = max(p_mid, floor_price)
    p_upper = max(upper, floor_price)
    return p_mid, p_upper

def main():
    init_trading_db()
    cli   = MexcClient()
    sessT = SessionT()

    try:
        last = last_price(cli)
        lower, upper, _ = channel_24h()

        pos_qty, pos_avg = get_position(sessT)
        if pos_qty <= 0:
            return  # нечего продавать

        # берём реальный свободный KAS на бирже (защита от "Oversold")
        free_kas = exchange_free_base(cli)
        if free_kas <= 0:
            return

        sellable_qty = min(pos_qty, free_kas)

        # считаем целевые цены
        p_mid, p_upper = build_sell_prices(pos_avg, last, upper)

        # микросдвиг, если уже есть SELL на этих уровнях
        open_sells = get_open_sells(sessT)
        open_prices = {round(float(o.price or 0.0), 6) for o in open_sells}

        # подвинем на SELL_MICROSHIFT, если уровень занят
        def shift_if_taken(price: float) -> float:
            p = float(price)
            # ограничим число сдвигов, чтобы не уйти далеко
            for _ in range(3):
                r = round(p, 6)
                if r not in open_prices:
                    return p
                p += float(SELL_MICROSHIFT)
            return p

        p_mid   = shift_if_taken(p_mid)
        p_upper = shift_if_taken(p_upper)

        # делим количество на две части
        split = min(max(float(SELL_SPLIT), 0.0), 1.0)
        q_upper = _floor6(sellable_qty * split)
        q_mid   = _floor6(sellable_qty - q_upper)

        # проверка минимального номинала для каждой заявки
        def notional_ok(p, q) -> bool:
            return (p * q) >= MIN_ORDER_USD and q > 0

        # если обе не проходят — попробуем объединить в одну заявку на p_mid (ближе к рынку)
        both_fail = (not notional_ok(p_mid, q_mid)) and (not notional_ok(p_upper, q_upper))
        if both_fail:
            q_one = _floor6(sellable_qty)
            if notional_ok(p_mid, q_one):
                try:
                    place_limit_sell(cli, sessT, p_mid, q_one)
                except Exception as e:
                    try:
                        send_error("sell.place", e)
                    except Exception:
                        pass
            return

        # если одна не проходит — переложим объём во вторую
        if not notional_ok(p_mid, q_mid) and notional_ok(p_upper, q_upper):
            q_upper = _floor6(q_upper + q_mid)
            q_mid   = 0.0
        elif not notional_ok(p_upper, q_upper) and notional_ok(p_mid, q_mid):
            q_mid   = _floor6(q_mid + q_upper)
            q_upper = 0.0

        # ставим, что получилось
        for price, qty in [(p_upper, q_upper), (p_mid, q_mid)]:
            if notional_ok(price, qty):
                try:
                    placed = place_limit_sell(cli, sessT, price, qty)
                    if not placed:
                        continue
                except Exception as e:
                    try:
                        send_error("sell.place", e)
                    except Exception:
                        pass

    except Exception as e:
        try:
            send_error("sell.tick", e)
        except Exception:
            pass
        raise
    finally:
        sessT.close()

if __name__ == "__main__":
    main()
