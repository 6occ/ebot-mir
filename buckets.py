#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import math
import random
from typing import List, Tuple
from sqlalchemy import and_

from config import (
    PAIR, MIN_ORDER_USD, QUOTE_ASSET,
)
from mexc_client import MexcClient
from models_trading import SessionT, Order, init_trading_db
from notify import send_error

def NOW():
    return int(time.time())

def _floor6(x: float) -> float:
    return math.floor(float(x) * 1_000_000) / 1_000_000

def _micro_shift() -> float:
    # лёгкий микросдвиг, чтобы не налезали друг на друга уровни
    return random.uniform(0.00005, 0.00025)

def usage():
    print(
        "Usage: buckets.py <M_percent:int> <N_start:float> "
        "[K_orders:int=60] [skew:float=1.15] [S_bottom:float=20.0] [S_top:float=1.02]\n"
        "Example: buckets.py 70 0.08 60 1.15 20.0 1.02"
    )

def cancel_all_buys(sess: SessionT, cli: MexcClient) -> int:
    # Биржа
    ex = cli.open_orders(PAIR) or []
    buy_ids = [str(o.get("orderId")) for o in ex if (o.get("side","").upper()=="BUY")]
    # Пытаемся отменить на бирже
    canceled = 0
    for oid in buy_ids:
        try:
            cli.cancel_order(PAIR, oid)
        except Exception:
            pass
        canceled += 1
    # Локально переводим в CANCELED
    stale = (sess.query(Order)
             .filter(and_(Order.pair==PAIR,
                          Order.side=="BUY",
                          Order.status.in_(("NEW","PARTIALLY_FILLED"))))
             .all())
    for o in stale:
        o.status = "CANCELED"
        o.updated = NOW()
        sess.add(o)
    if stale:
        sess.commit()
    # Подождать применения на бирже и освободить баланс
    t0 = time.time()
    while time.time()-t0 < 5.0:
        oo = cli.open_orders(PAIR) or []
        if not any(o.get("side","").upper()=="BUY" for o in oo):
            break
        time.sleep(0.5)
    return canceled

def get_free_usdc(cli: MexcClient) -> float:
    acct = cli.account()
    bal = {b.get("asset"): (float(b.get("free",0.0)), float(b.get("locked",0.0)))
           for b in acct.get("balances", [])}
    free = float(bal.get(QUOTE_ASSET, (0.0, 0.0))[0])
    return max(0.0, free)

def build_grid(N: float, top: float, K: int, skew: float) -> List[float]:
    """
    Почти линейное распределение цен от N до top с мягким перекосом к низу.
    t ∈ [0..1): p = N + (top-N) * (t**skew), skew>1 -> плотнее у N.
    """
    if K <= 0 or top <= N:
        return []
    out = []
    for i in range(K):
        t = (i + 0.5) / K   # центры интервалов
        u = t ** max(1.0, skew)
        p = N + (top - N) * u
        # лёгкий микросдвиг вниз
        p = p * (1.0 - _micro_shift())
        out.append(max(N, min(top, p)))
    # монотонность гарантирована возрастанием i и u
    return out

def build_sizes(K: int, S_bottom: float, S_top: float, skew: float) -> List[float]:
    """
    Размеры почти линейно от S_top (сверху) до S_bottom (снизу),
    с мягким перекосом к низу (чуть быстрее растут к низу).
    """
    if K <= 0:
        return []
    out = []
    for i in range(K):
        t = (i + 0.5) / K  # сверху t ближе к 1
        # хотим сверху S_top, снизу S_bottom:
        # возьмём w = (1 - t)**skew — больше у низа
        w = (1.0 - t) ** max(1.0, skew)
        s = S_top + (S_bottom - S_top) * w
        out.append(max(0.0, s))
    return out

def place_ladder(sess: SessionT, cli: MexcClient,
                 prices: List[float], sizes_usd: List[float],
                 budget_usd: float) -> Tuple[int, float]:
    """
    Ставит лестницу, подгоняя под бюджет.
    Возвращает (сколько поставили, сколько израсходовали).
    """
    if not prices or not sizes_usd or budget_usd <= 0:
        return 0, 0.0
    K = min(len(prices), len(sizes_usd))
    prices = prices[:K]
    sizes  = sizes_usd[:K]

    # Отфильтровать слишком мелкие (< MIN_ORDER_USD)
    filt = [(p,s) for p,s in zip(prices, sizes) if s >= MIN_ORDER_USD]
    if not filt:
        return 0, 0.0

    # Масштабируем под бюджет
    need = sum(s for _,s in filt)
    if need > budget_usd and need > 0:
        k = budget_usd / need
        filt = [(p, s*k) for p,s in filt if s*k >= MIN_ORDER_USD]

    if not filt:
        return 0, 0.0

    placed = 0
    spent  = 0.0
    for price, usd in filt:
        if usd < MIN_ORDER_USD:
            continue
        qty = _floor6(usd / price)
        if qty <= 0:
            continue
        try:
            resp = cli.place_order(PAIR, "BUY", price=price, qty=qty)
            oid  = str(resp.get("orderId") or f"B_{NOW()}")
        except Exception as e:
            try:
                send_error("buckets.place", e)
            except Exception:
                pass
            continue

        o = Order(
            id=oid, pair=PAIR, side="BUY",
            price=float(price), qty=float(qty),
            status="NEW", created=NOW(), updated=NOW(),
            paper=False, reserved=float(usd), filled_qty=0.0, mode="BUCKET",
        )
        sess.merge(o)
        sess.commit()
        placed += 1
        spent += usd

        if spent >= budget_usd * 0.999:  # чуть-чуть запас
            break

    return placed, spent

def main():
    if len(sys.argv) < 3:
        usage(); sys.exit(1)
    try:
        M_percent = float(sys.argv[1])  # доля свободного USDC после отмены BUY (0..100)
        N_start   = float(sys.argv[2])  # нижняя граница цены
        K_orders  = int(sys.argv[3]) if len(sys.argv) > 3 else 60
        skew      = float(sys.argv[4]) if len(sys.argv) > 4 else 1.15
        S_bottom  = float(sys.argv[5]) if len(sys.argv) > 5 else 20.0
        S_top     = float(sys.argv[6]) if len(sys.argv) > 6 else 1.02
    except Exception:
        usage(); sys.exit(1)

    if M_percent <= 0 or K_orders <= 0 or N_start <= 0:
        usage(); sys.exit(1)

    init_trading_db()
    sess = SessionT()
    cli  = MexcClient()
    try:
        # 1) Отменяем все BUY
        canceled = cancel_all_buys(sess, cli)

        # 2) Берём актуальную свободную ликвидность с биржи
        free_usdc = get_free_usdc(cli)
        budget = free_usdc * (M_percent/100.0)

        # 3) Определяем верхнюю границу: last * 0.99
        last = float(cli.price(PAIR))
        top  = last * 0.99
        if top <= N_start:
            # почти нет диапазона — отступим 0.3% вниз от last
            top = last * 0.997
            if top <= N_start:
                # в крайнем случае сдвинем N_start на 0.5% ниже top
                N_start = top * 0.995

        # 4) Строим сетку цен и сайзов
        prices = build_grid(N_start, top, K_orders, skew)
        sizes  = build_sizes(K_orders, S_bottom, S_top, skew)

        # 5) Ставим ордера (под бюджет, с учётом MIN_ORDER_USD)
        placed, spent = place_ladder(sess, cli, prices, sizes, budget)

        print(f"BUCKETS: canceled_BUY={canceled} | free_usdc={free_usdc:.2f} | "
              f"budget(M={M_percent:.1f}%)={budget:.2f} | range=[{N_start:.6f}..{top:.6f}] "
              f"| K={K_orders} skew={skew} | placed={placed} spent={spent:.2f}")
    finally:
        sess.close()

if __name__ == "__main__":
    main()
