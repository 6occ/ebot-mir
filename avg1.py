#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time, math
from sqlalchemy import and_

from config import PAIR, BASE_ASSET, MIN_ORDER_USD
from mexc_client import MexcClient
from models_trading import SessionT, Order, Position, init_trading_db

TS = lambda: int(time.time())

def floor6(x: float) -> float:
    return math.floor(float(x) * 1_000_000) / 1_000_000

def main():
    init_trading_db()
    cli  = MexcClient()
    sess = SessionT()
    try:
        # --- позиция (avg, qty) ---
        pos = sess.query(Position).filter(Position.pair==PAIR).first()
        pos_qty = float(pos.qty) if pos else 0.0
        pos_avg = float(pos.avg) if pos else 0.0
        if pos_qty <= 0 or pos_avg <= 0:
            print(f"[avg1] skip: no position for {PAIR} (qty={pos_qty}, avg={pos_avg})")
            return

        target_price = round(pos_avg * 1.01, 6)  # AVG + 1%

        # --- 1) Снять ВСЕ SELL на бирже ---
        ex_open = cli.open_orders(PAIR) or []
        for o in ex_open:
            if str(o.get("symbol")) != PAIR: 
                continue
            if (o.get("side","").upper() == "SELL") and o.get("orderId"):
                try:
                    cli.cancel_order(PAIR, str(o["orderId"]))
                except Exception:
                    pass  # если уже снят — ладно

        # --- 1b) Отметить SELL как CANCELED в БД ---
        opened_sells = (sess.query(Order)
                          .filter(and_(Order.pair==PAIR,
                                       Order.side=="SELL",
                                       Order.status.in_(("NEW","PARTIALLY_FILLED"))))
                          .all())
        for o in opened_sells:
            o.status  = "CANCELED"
            o.updated = TS()
            sess.add(o)
        if opened_sells:
            sess.commit()

        # --- 2) Рассчитать доступное кол-во KAS для продажи ---
        # берём минимум из позиции и свободного KAS на бирже
        acct = cli.account()
        bal  = { b.get("asset"): (float(b.get("free",0)), float(b.get("locked",0)))
                 for b in acct.get("balances", []) }
        free_kas = float(bal.get(BASE_ASSET, (0.0,0.0))[0])

        qty = min(pos_qty, free_kas)
        qty = floor6(qty)
        if qty <= 0:
            print(f"[avg1] skip: no free qty (pos_qty={pos_qty}, free_kas={free_kas})")
            return

        # минимум к исполнению по сумме (1 USDC и т.п.)
        if qty * target_price < float(MIN_ORDER_USD):
            # попробуем поднять до минимума, но не превышая доступное
            need_qty = floor6(float(MIN_ORDER_USD) / target_price)
            qty = min(qty, need_qty)
            if qty * target_price < float(MIN_ORDER_USD):
                print(f"[avg1] skip: below exchange min (qty={qty}, price={target_price}, min_usd={MIN_ORDER_USD})")
                return

        # --- 3) Ставим один SELL @ avg*1.01 ---
        resp = cli.place_order(PAIR, "SELL", price=target_price, qty=qty)
        oid  = str(resp.get("orderId") or f"SELL_{TS()}")

        o = Order(
            id=oid, pair=PAIR, side="SELL",
            price=float(target_price), qty=float(qty),
            status="NEW", created=TS(), updated=TS(),
            paper=False, reserved=0.0, filled_qty=0.0, mode="MANUAL_AVG1",
        )
        sess.merge(o)
        sess.commit()

        print(f"[avg1] placed SELL {PAIR} @ {target_price:.6f} qty={qty:.6f} (oid={oid})")

    finally:
        sess.close()

if __name__ == "__main__":
    main()
