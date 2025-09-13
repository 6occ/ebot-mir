#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import math, time, random
from datetime import timezone, timedelta
from statistics import mean
from sqlalchemy import and_

from config import (
    PAIR,
    MIN_ORDER_USD,

    # логика BUY
    BUY_BELOW_OFFSETS,          # напр. [0.005, 0.010, 0.015]
    BUY_INCHANNEL_LEVELS,       # напр. [5, 10, 15]
    BUY_SIZE_BELOW_FIXED_USD,   # $ для «ниже канала»
    BUY_SIZE_INCH_MIN_USD,      # $ у верха канала
    BUY_SIZE_INCH_MAX_USD,      # $ у низа канала
    BUY_SIZE_ABOVE_FIXED_USD,   # $ «выше канала» (на КАЖДЫЙ ордер)
    MICRO_OFFSET_MIN,
    MICRO_OFFSET_MAX,
)

from models import SessionLocal, MinMax
from models_trading import SessionT, Order, Capital, init_trading_db
from mexc_client import MexcClient
from notify import send_error

MSK = timezone(timedelta(hours=3))
now_ts = lambda: int(time.time())

def _floor6(x: float) -> float:
    return math.floor(float(x) * 1_000_000) / 1_000_000

def micro_shift() -> float:
    return random.uniform(MICRO_OFFSET_MIN, MICRO_OFFSET_MAX)

def last_price(cli: MexcClient) -> float:
    return float(cli.price(PAIR))

def channel_24h() -> tuple[float, float, float]:
    """Возвращает (lower, upper, mid24) как mid±spread/4 за 24ч по MinMax."""
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

def get_capital(sessT: SessionT) -> float:
    cap = sessT.query(Capital).filter(Capital.pair == PAIR).first()
    return float(cap.available_usd) if cap else 0.0

def place_limit_buy(cli: MexcClient, sessT: SessionT, price: float, usd_size: float):
    """Отправляет лимитную покупку и фиксирует в локальной БД."""
    price = max(0.0, float(price))
    usd_size = max(0.0, float(usd_size))
    if price <= 0 or usd_size < MIN_ORDER_USD:
        return False

    qty = _floor6(usd_size / price)
    if qty <= 0:
        return False

    resp = cli.place_order(PAIR, "BUY", price=price, qty=qty)
    oid = str(resp.get("orderId") or f"BUY_{now_ts()}")

    o = Order(
        id=oid, pair=PAIR, side="BUY",
        price=float(price), qty=float(qty),
        status="NEW", created=now_ts(), updated=now_ts(),
        paper=False, reserved=float(usd_size), filled_qty=0.0, mode="GRID",
    )
    sessT.merge(o)
    sessT.commit()
    return True

# === Построение цен ===

def build_orders_above(last: float, upper: float) -> list[tuple[float, float]]:
    """
    Над каналом — 2 ордера:
      1) точно на upper
      2) на середину между last и upper: mid = (last + upper) / 2
    Размер каждого — BUY_SIZE_ABOVE_FIXED_USD.
    Без любых «зажимов» к upper и без микросдвига.
    """
    if not (last > 0 and upper > 0):
        return []
    p_upper = float(upper)
    p_mid   = (float(last) + float(upper)) / 2.0
    sz      = float(BUY_SIZE_ABOVE_FIXED_USD)
    return [(p_mid,   sz),
            (p_upper, sz)]

def build_orders_inchannel(last: float) -> list[tuple[float, float]]:
    """
    Внутри канала — 3 ордера по BUY_INCHANNEL_LEVELS.
    Цены — ступеньками ниже last (по 0.1% * lvl) плюс микро-смещение.
    Сайз — линейно от MAX (внизу) к MIN (вверху).
    """
    if not BUY_INCHANNEL_LEVELS:
        return []
    max_lvl = max(BUY_INCHANNEL_LEVELS) or 1
    out = []
    for lvl in BUY_INCHANNEL_LEVELS:
        target = last * (1.0 - 0.001 * float(lvl)) * (1.0 - micro_shift())
        frac   = float(lvl) / float(max_lvl)
        size   = BUY_SIZE_INCH_MAX_USD - (BUY_SIZE_INCH_MAX_USD - BUY_SIZE_INCH_MIN_USD) * frac
        out.append((target, max(0.0, size)))
    return out

def build_orders_below(last: float) -> list[tuple[float, float]]:
    """Ниже канала — 3 ордера по фиксированным offset’ам от last, одинаковый сайз."""
    out = []
    for off in (BUY_BELOW_OFFSETS or []):
        target = last * (1.0 - float(off)) * (1.0 - micro_shift())
        out.append((target, BUY_SIZE_BELOW_FIXED_USD))
    return out

def main():
    init_trading_db()
    cli = MexcClient()
    sessT = SessionT()

    try:
        last = last_price(cli)
        lower, upper, _ = channel_24h()

        # 1) Формируем набор согласно положению цены
        if lower > 0 and upper > 0:
            if last > upper:
                orders = build_orders_above(last, upper)    # 2 ордера (mid и upper)
            elif last < lower:
                orders = build_orders_below(last)            # 3 ордера
            else:
                orders = build_orders_inchannel(last)        # 3 ордера
        else:
            orders = build_orders_inchannel(last)

        # 2) Бюджет
        avail_usd = get_capital(sessT)
        if avail_usd <= 0:
            return

        # выбросим совсем мелкие
        orders = [(p, sz) for (p, sz) in orders if sz >= MIN_ORDER_USD]
        if not orders:
            return

        need = sum(sz for _, sz in orders)

        # Особое правило для ветки "над каналом":
        # если денег меньше, чем на 2 ордера, но ≥ MIN_ORDER_USD — ставим один ордер на mid и всем бюджетом.
        if (lower > 0 and upper > 0 and last > upper) and (avail_usd < need) and (avail_usd >= MIN_ORDER_USD):
            p_mid = (last + upper) / 2.0
            place_limit_buy(cli, sessT, p_mid, avail_usd)
            return

        # Иначе — просто пропорционально ужимаем до доступного кэша
        if need > avail_usd:
            k = avail_usd / need if need > 0 else 0.0
            orders = [(p, sz*k) for (p, sz) in orders if sz*k >= MIN_ORDER_USD]

        if not orders:
            return

        # 3) Ставим
        for price, usd in orders:
            try:
                place_limit_buy(cli, sessT, price, usd)
            except Exception as e:
                try:
                    send_error("buy.place_order", e)
                except Exception:
                    pass

    except Exception as e:
        try:
            send_error("buy.tick", e)
        except Exception:
            pass
        raise
    finally:
        sessT.close()

if __name__ == "__main__":
    main()
