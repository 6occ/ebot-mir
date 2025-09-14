#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import time
import argparse
from typing import List, Tuple

from config import (
    PAIR, MIN_ORDER_USD, SELL_MIN_GAIN,
    CONSOLIDATE_BUY_ENABLED,  CONSOLIDATE_BUY_LIMIT_OVER,  CONSOLIDATE_BUY_TO_CANCEL,  CONSOLIDATE_BUY_PLACE_COUNT,
    CONSOLIDATE_SELL_ENABLED, CONSOLIDATE_SELL_LIMIT_OVER, CONSOLIDATE_SELL_TO_CANCEL, CONSOLIDATE_SELL_PLACE_COUNT,
)
from mexc_client import MexcClient
from models_trading import SessionT, init_trading_db, Order, Position

STATUS_OPEN = ("NEW", "PARTIALLY_FILLED")

def now_ts() -> int:
    return int(time.time())

def _floor6(x: float) -> float:
    return math.floor(float(x) * 1_000_000) / 1_000_000

def _pairwise(items: List[Order]) -> List[Tuple[Order, Order]]:
    out = []
    it = iter(items)
    for a in it:
        try:
            b = next(it)
        except StopIteration:
            break
        out.append((a, b))
    return out

def _load_open(sess: SessionT, side: str) -> List[Order]:
    q = (sess.query(Order)
         .filter(Order.pair == PAIR,
                 Order.side == side,
                 Order.status.in_(STATUS_OPEN)))
    # BUY: дальние = самые низкие цены; SELL: дальние = самые высокие
    if side == "BUY":
        q = q.order_by(Order.price.asc())
    else:
        q = q.order_by(Order.price.desc())
    return q.all()

def _cancel_orders(cli: MexcClient, sess: SessionT, orders: List[Order], dry: bool) -> None:
    for o in orders:
        if not dry:
            try:
                cli.cancel_order(PAIR, o.id)
            except Exception:
                pass
        # локально
        db = sess.query(Order).get(o.id)
        if db and db.status in STATUS_OPEN:
            db.status = "CANCELED"
            db.updated = now_ts()
            sess.add(db)
    sess.commit()

def _place_buy(cli: MexcClient, sess: SessionT, price: float, usd_size: float, dry: bool) -> bool:
    price = float(price)
    usd_size = float(usd_size)
    if usd_size < MIN_ORDER_USD or price <= 0:
        return False
    qty = _floor6(usd_size / price)
    if qty <= 0:
        return False
    if not dry:
        resp = cli.place_order(PAIR, "BUY", price=price, qty=qty)
        oid = str(resp.get("orderId") or f"BUY_{now_ts()}")
    else:
        oid = f"DRY_BUY_{now_ts()}"
    o = Order(
        id=oid, pair=PAIR, side="BUY",
        price=price, qty=qty, reserved=usd_size,
        status="NEW", created=now_ts(), updated=now_ts(),
        paper=False, filled_qty=0.0, mode="CONSOLIDATE",
    )
    sess.merge(o); sess.commit()
    return True

def _place_sell(cli: MexcClient, sess: SessionT, price: float, qty: float, dry: bool) -> bool:
    price = float(price); qty = _floor6(float(qty))
    if qty <= 0 or price <= 0:
        return False
    if not dry:
        resp = cli.place_order(PAIR, "SELL", price=price, qty=qty)
        oid = str(resp.get("orderId") or f"SELL_{now_ts()}")
    else:
        oid = f"DRY_SELL_{now_ts()}"
    o = Order(
        id=oid, pair=PAIR, side="SELL",
        price=price, qty=qty, reserved=0.0,
        status="NEW", created=now_ts(), updated=now_ts(),
        paper=False, filled_qty=0.0, mode="CONSOLIDATE",
    )
    sess.merge(o); sess.commit()
    return True

def _nearest_existing_at_price(sess: SessionT, price: float, tol: float = 1e-6) -> List[Order]:
    low = price * (1.0 - tol)
    high = price * (1.0 + tol)
    return (sess.query(Order)
            .filter(Order.pair == PAIR,
                    Order.side == "SELL",
                    Order.status.in_(STATUS_OPEN),
                    Order.price >= low,
                    Order.price <= high)
            .all())

def consolidate_buys(cli: MexcClient, sess: SessionT, dry: bool) -> str:
    if not CONSOLIDATE_BUY_ENABLED:
        return "BUY: disabled"
    opens = _load_open(sess, "BUY")
    n = len(opens)
    if n <= CONSOLIDATE_BUY_LIMIT_OVER:
        return f"BUY: open={n} <= limit({CONSOLIDATE_BUY_LIMIT_OVER}) — skip"

    to_cancel = min(CONSOLIDATE_BUY_TO_CANCEL, n)
    place_cnt = min(CONSOLIDATE_BUY_PLACE_COUNT, to_cancel // 2)

    far = opens[:to_cancel]  # уже asc
    _cancel_orders(cli, sess, far, dry)

    total_usd = sum(float(o.price) * float(o.qty) for o in far)
    per_order_usd = total_usd / place_cnt if place_cnt > 0 else 0.0

    placed = 0
    pairs = _pairwise(far)[:place_cnt]
    for a, b in pairs:
        mid_price = (float(a.price) + float(b.price)) / 2.0
        if _place_buy(cli, sess, mid_price, per_order_usd, dry):
            placed += 1

    return f"BUY: canceled={len(far)} pairs={len(pairs)} placed={placed} per_order_usd={per_order_usd:.4f} dry={dry}"

def consolidate_sells(cli: MexcClient, sess: SessionT, dry: bool) -> str:
    if not CONSOLIDATE_SELL_ENABLED:
        return "SELL: disabled"

    pos = sess.query(Position).filter(Position.pair == PAIR).first()
    avg = float(pos.avg) if pos else 0.0
    min_sell_price = avg * (1.0 + float(SELL_MIN_GAIN)) if avg > 0 else 0.0

    opens = _load_open(sess, "SELL")
    n = len(opens)
    if n <= CONSOLIDATE_SELL_LIMIT_OVER:
        return f"SELL: open={n} <= limit({CONSOLIDATE_SELL_LIMIT_OVER}) — skip"

    to_cancel = min(CONSOLIDATE_SELL_TO_CANCEL, n)
    place_cnt = min(CONSOLIDATE_SELL_PLACE_COUNT, to_cancel // 2)

    far = opens[:to_cancel]  # уже desc
    _cancel_orders(cli, sess, far, dry)

    placed = 0
    pairs = _pairwise(far)[:place_cnt]
    for a, b in pairs:
        target = (float(a.price) + float(b.price)) / 2.0
        if target < min_sell_price:
            target = min_sell_price

        dups = _nearest_existing_at_price(sess, target)
        extra_qty = 0.0
        for d in dups:
            if not dry:
                try:
                    MexcClient().cancel_order(PAIR, d.id)
                except Exception:
                    pass
            if d.status in STATUS_OPEN:
                extra_qty += float(d.qty)
                d.status = "CANCELED"
                d.updated = now_ts()
                sess.add(d)
        sess.commit()

        qty = _floor6(float(a.qty) + float(b.qty) + extra_qty)
        if qty <= 0:
            continue

        if _place_sell(cli, sess, target, qty, dry):
            placed += 1

    return f"SELL: canceled={len(far)} pairs={len(pairs)} placed={placed} min_guard={min_sell_price:.6f} dry={dry}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Диагностика без реальных отмен/постановки")
    args = ap.parse_args()

    init_trading_db()
    sess = SessionT()
    cli = MexcClient()
    try:
        r1 = consolidate_buys(cli, sess, args.dry_run)
        r2 = consolidate_sells(cli, sess, args.dry_run)
        print(f"{r1} | {r2}")
    finally:
        sess.close()

if __name__ == "__main__":
    main()
