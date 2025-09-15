#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тихий синк раз в минуту:
1) Импорт последних сделок (SYNC_WINDOW_MIN) -> fills (без дублей)
2) Обновление открытых ордеров -> orders (upsert + filled_qty)
3) Баланс биржи -> capital.available_usd (limit_usd не трогаем)
4) Пересчёт позиции qty/avg из всех fills -> position
Никаких сообщений в TG. Только БД.
"""
import time
import math
import re
import logging
from typing import Dict, List, Tuple
from sqlalchemy import and_, func
from models_trading import (
    SessionT, Fill, Order, Position, Capital, init_trading_db
)
from mexc_client import MexcClient
from config import (
    PAIR, QUOTE_ASSET,
    SYNC_WINDOW_MIN, SYNC_OPEN_LIMIT,
)

def now_s():
    return int(time.time())
def now_ms():
    return int(time.time()*1000)

def _to_float(x):
    try: return float(x)
    except: return 0.0

def floor6(x: float) -> float:
    return math.floor(float(x) * 1_000_000) / 1_000_000

def _index_by(items: List[dict], key: str) -> Dict[str, dict]:
    out = {}
    for it in items or []:
        k = str(it.get(key, ""))
        if k: out[k] = it
    return out

# -------- TRADES -> fills --------
def sync_trades(sess: SessionT, cli: MexcClient, window_min: int) -> int:
    end   = now_ms()
    start = end - window_min*60*1000
    trades = cli.my_trades(PAIR, start, end, 1000) or []
    inserted = 0
    for t in trades:
        fid = str(t.get("id") or t.get("tradeId") or t.get("orderId") or "")
        if not fid:
            continue
        if sess.query(Fill).filter(Fill.id == fid).first():
            continue
        side  = str(t.get("side","")).upper() or ("BUY" if bool(t.get("isBuyer")) else "SELL")
        price = _to_float(t.get("price") or t.get("p"))
        qty   = _to_float(t.get("qty")   or t.get("q"))
        fee   = _to_float(t.get("commission") or 0.0)
        ts_ms = int(t.get("time") or t.get("T") or end)
        row = Fill(
            id=fid,
            order_id=str(t.get("orderId") or ""),
            pair=PAIR,
            side=side,
            price=price,
            qty=qty,
            fee=fee,
            ts=ts_ms//1000,
            mode=""
        )
        sess.add(row)
        inserted += 1
    if inserted:
        sess.commit()
    return inserted

# ---- OPEN ORDERS -> orders (upsert + filled_qty) ----
def _fills_by_order(sess: SessionT) -> Dict[str, Dict[str, float]]:
    rows = (sess.query(Fill.order_id, Fill.side, func.sum(Fill.qty))
                .filter(and_(Fill.pair == PAIR, Fill.order_id is not None))
                .group_by(Fill.order_id, Fill.side)
                .all())
    agg: Dict[str, Dict[str, float]] = {}
    for oid, side, s in rows:
        if not oid:
            continue
        d = agg.setdefault(str(oid), {"BUY":0.0, "SELL":0.0})
        d[side] = float(s or 0.0)
    return agg

def sync_open_orders(sess: SessionT, cli: MexcClient, limit: int) -> None:
    data = cli.open_orders(PAIR, limit) or []
    ex_by_id = _index_by(data, "orderId")

    local_open = (sess.query(Order)
                    .filter(and_(Order.pair == PAIR,
                                 Order.status.in_(("NEW","PARTIALLY_FILLED"))))
                    .all())

    # upsert по бирже
    for it in data:
        oid   = str(it.get("orderId"))
        side  = str(it.get("side","")).upper()
        price = _to_float(it.get("price"))
        qty   = _to_float(it.get("origQty") or it.get("orig_qty") or it.get("origQuantity"))
        fqty  = _to_float(it.get("executedQty") or 0.0)
        status= str(it.get("status","NEW"))
        created = int(it.get("time") or it.get("transactTime") or now_ms())//1000
        updated = int(it.get("updateTime") or now_ms())//1000
        o = sess.query(Order).filter(Order.id == oid).first()
        if not o:
            o = Order(
                id=oid, pair=PAIR, side=side, price=price, qty=qty,
                status=status, created=created, updated=updated,
                paper=False, reserved=0.0, filled_qty=fqty, mode=""
            )
            sess.add(o)
        else:
            o.price = price; o.qty = qty
            o.status = status
            o.updated = max(o.updated or 0, updated)
            o.filled_qty = fqty
    sess.commit()

    # reconcile filled_qty из fills
    fagg = _fills_by_order(sess)
    touched = False
    for oid, sums in fagg.items():
        o = sess.query(Order).filter(Order.id == oid).first()
        if not o:
            continue
        exp = sums.get(o.side, 0.0)
        if abs((o.filled_qty or 0.0) - exp) > 1e-12:
            o.filled_qty = exp
            o.updated = now_s()
            touched = True
    if touched:
        sess.commit()

    # закрыть локально те, которых нет на бирже
    still_open_ids = set(ex_by_id.keys())
    for o in local_open:
        if o.id not in still_open_ids:
            rem = max(0.0, (o.qty or 0.0) - (o.filled_qty or 0.0))
            o.status = "FILLED" if rem <= 1e-12 else "CANCELED"
            o.updated = now_s()
    sess.commit()

# -------- BALANCE -> capital.available_usd --------
def sync_balance(sess: SessionT, cli: MexcClient) -> None:
    acct = cli.account() or {}
    bals = acct.get("balances") or []
    by_asset = {b.get("asset"): b for b in bals}
    usdc = by_asset.get(QUOTE_ASSET, {})
    avail_usd = _to_float(usdc.get("free"))
    cap = sess.query(Capital).filter(Capital.pair == PAIR).first()
    if not cap:
        cap = Capital(pair=PAIR, limit_usd=1000.0, available_usd=avail_usd,
                      realized_pnl=0.0, updated=now_s())
        sess.add(cap)
    else:
        cap.available_usd = avail_usd
        cap.updated = now_s()
    sess.commit()

# -------- Recompute position from fills --------
def recompute_position(sess: SessionT) -> Tuple[float, float]:
    fills = (sess.query(Fill)
                .filter(Fill.pair == PAIR)
                .order_by(Fill.ts.asc(), Fill.id.asc())
                .all())
    qty = 0.0
    cost = 0.0
    for f in fills:
        if f.side == "BUY":
            qty  += f.qty
            cost += f.qty * f.price + (f.fee or 0.0)
        else:
            if qty <= 0:
                qty = 0.0; cost = 0.0
                continue
            sell_q = min(qty, f.qty)
            avg = (cost/qty) if qty > 1e-12 else 0.0
            cost -= avg * sell_q
            qty  -= sell_q
    avg = (cost/qty) if qty > 1e-12 else 0.0
    p = sess.query(Position).filter(Position.pair == PAIR).first()
    if not p:
        p = Position(pair=PAIR, qty=qty, avg=avg, updated=now_s())
        sess.add(p)
    else:
        p.qty = qty; p.avg = avg; p.updated = now_s()
    sess.commit()
    return qty, avg

def main(window_min: int = SYNC_WINDOW_MIN, open_limit: int = SYNC_OPEN_LIMIT):
    try:
        from logging_config import setup_logging
        setup_logging()
    except Exception:
        pass
    log = logging.getLogger(__name__)
    log.info("sync start window_min=%s open_limit=%s", window_min, open_limit)
    init_trading_db()
    sess = SessionT()
    cli  = MexcClient()
    try:
        sync_trades(sess, cli, window_min)
        sync_open_orders(sess, cli, open_limit)
        sync_balance(sess, cli)
        recompute_position(sess)
    finally:
        sess.close()

def _parse_window_to_minutes(val) -> int:
    """
    Принимает человеко-читаемый формат: 90s, 30m, 2h, 1d, либо число (минуты).
    Возвращает минуты (целое), границы: 1..1440.
    """
    if val is None:
        return int(SYNC_WINDOW_MIN)
    try:
        # если пришло число — трактуем как минуты (обратная совместимость)
        m = int(val)
        return max(1, min(1440, m))
    except Exception:
        pass
    s = str(val).strip().lower()
    m = re.fullmatch(r"(\d+)([smhd]?)", s)
    if not m:
        return max(1, min(1440, int(SYNC_WINDOW_MIN)))
    num = int(m.group(1))
    unit = m.group(2) or "m"
    if unit == "s":
        minutes = (num + 59) // 60  # округляем вверх до минуты
    elif unit == "m":
        minutes = num
    elif unit == "h":
        minutes = num * 60
    else:  # 'd'
        minutes = num * 1440
    return max(1, min(1440, int(minutes)))

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Quiet sync: trades/orders/balance -> DB")
    ap.add_argument(
        "--window",
        default=None,
        help="Окно импорта сделок: число (минуты) или 90s/30m/2h/1d. По умолчанию config.SYNC_WINDOW_MIN",
    )
    ap.add_argument(
        "--open-limit",
        type=int,
        default=SYNC_OPEN_LIMIT,
        help="Максимум открытых ордеров за раз (по умолчанию из config.SYNC_OPEN_LIMIT)",
    )
    args = ap.parse_args()
    window_min = _parse_window_to_minutes(args.window)
    open_limit = max(10, min(2000, int(args.open_limit)))
    main(window_min=window_min, open_limit=open_limit)
