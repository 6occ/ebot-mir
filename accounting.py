# -*- coding: utf-8 -*-
from typing import Iterable, Tuple, Optional, Dict
def compute_position_from_fills(fills: Iterable[Dict]) -> Tuple[float, float]:
    qty = 0.0; cost = 0.0
    for f in fills:
        side = str(f.get("side","")).upper()
        q = float(f.get("qty") or 0.0); p = float(f.get("price") or 0.0); fee = float(f.get("fee") or 0.0)
        if side == "BUY":
            qty += q; cost += q * p + fee
        else:
            if qty <= 0.0: qty = 0.0; cost = 0.0; continue
            sell_q = min(qty, q); avg = (cost/qty) if qty > 1e-12 else 0.0
            cost -= avg * sell_q; qty -= sell_q
    avg = (cost/qty) if qty > 1e-12 else 0.0
    return qty, avg
def estimate_equity_usd(start_capital_usd: float, last_price: Optional[float], qty: float, avg: float, reserve_usd: float = 0.0) -> Tuple[float, float]:
    last = float(last_price or 0.0); position_val = last * qty
    total_equity_est = start_capital_usd + (last - avg) * qty if avg and last else start_capital_usd
    cash_est = max(0.0, total_equity_est - position_val - float(reserve_usd or 0.0))
    return total_equity_est, cash_est
def compute_pnl(last_price: float, qty: float, ref_price: Optional[float]):
    if ref_price is None: return None, None
    abs_usd = (float(last_price) - float(ref_price)) * float(qty)
    base = max(1e-9, float(last_price) * float(qty))
    pct = (abs_usd / base) * 100.0
    return abs_usd, pct
