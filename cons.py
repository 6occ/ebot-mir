#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, glob, sqlite3, argparse, re, types
from importlib.machinery import SourceFileLoader
from typing import Optional, Tuple

try:
    from config import DB_PATH, PAIR
except Exception:
    DB_PATH = "/opt/Ebot/ebot.db"
    PAIR = "KASUSDC"

STATUS_OPEN = ("NEW", "PARTIALLY_FILLED")

def _load_impl_module():
    candidates = sorted(glob.glob("/opt/Ebot/consolidate.py.bak.*"))
    if not candidates:
        fallback = "/opt/Ebot/consolidate.py"
        if os.path.exists(fallback):
            candidates = [fallback]
        else:
            raise RuntimeError("Нет исходника: /opt/Ebot/consolidate.py(.bak.*)")
    src = candidates[-1]
    mod = SourceFileLoader("cons_impl", src).load_module()
    for name in ("init_trading_db","SessionT","MexcClient","consolidate_buys","consolidate_sells"):
        if not hasattr(mod, name):
            raise RuntimeError(f"В {src} нет символа: {name}")
    return mod

def _count_open_orders(db_path: str, pair: str, side: str) -> int:
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM orders WHERE pair=? AND side=? AND status IN ({','.join('?'*len(STATUS_OPEN))})",
            (pair, side, *STATUS_OPEN),
        )
        n = int(cur.fetchone()[0] or 0)
        conn.close()
        return n
    except Exception:
        return 0

def _fmt_decision(side: str, open_cnt: int, limit: Optional[int], reason: Optional[str]) -> str:
    if limit is None:
        return f"{side}: limit=None — skip"
    if limit == 0:
        return f"{side}: limit=0 — skip (по запросу)"
    if reason:
        return f"{side}: open={open_cnt} <= limit({limit}) — skip ({reason})"
    return f"{side}: open={open_cnt} > limit({limit}) — run"

def _override_limits(impl: types.ModuleType, buy_limit: Optional[int], sell_limit: Optional[int]) -> list[str]:
    changed = []
    # дефолты из исходника
    if buy_limit is None and hasattr(impl, "CONSOLIDATE_BUY_LIMIT_OVER"):
        buy_limit = getattr(impl, "CONSOLIDATE_BUY_LIMIT_OVER")
    if sell_limit is None and hasattr(impl, "CONSOLIDATE_SELL_LIMIT_OVER"):
        sell_limit = getattr(impl, "CONSOLIDATE_SELL_LIMIT_OVER")
    # переопределение
    if buy_limit is not None and hasattr(impl, "CONSOLIDATE_BUY_LIMIT_OVER"):
        setattr(impl, "CONSOLIDATE_BUY_LIMIT_OVER", int(buy_limit))
        changed.append(f"CONSOLIDATE_BUY_LIMIT_OVER={buy_limit}")
    if sell_limit is not None and hasattr(impl, "CONSOLIDATE_SELL_LIMIT_OVER"):
        setattr(impl, "CONSOLIDATE_SELL_LIMIT_OVER", int(sell_limit))
        changed.append(f"CONSOLIDATE_SELL_LIMIT_OVER={sell_limit}")
    return changed, buy_limit, sell_limit

def run(buy_limit: Optional[int], sell_limit: Optional[int], dry_run: bool) -> int:
    impl = _load_impl_module()
    touched, buy_limit, sell_limit = _override_limits(impl, buy_limit, sell_limit)
    if touched:
        print("[override] " + ", ".join(touched))

    impl.init_trading_db()
    sess = impl.SessionT()
    cli  = impl.MexcClient()

    rc = 0
    try:
        buy_open = _count_open_orders(DB_PATH, PAIR, "BUY")
        if buy_limit is None or buy_limit == 0:
            buy_msg = _fmt_decision("BUY", buy_open, buy_limit, None)
        elif buy_open > buy_limit:
            try:
                r = impl.consolidate_buys(cli, sess, dry_run); print(r)
            except Exception as e:
                print(f"BUY error: {e}", file=sys.stderr); rc |= 1
            buy_msg = _fmt_decision("BUY", buy_open, buy_limit, None)
        else:
            buy_msg = _fmt_decision("BUY", buy_open, buy_limit, "недостаточно открытых")

        sell_open = _count_open_orders(DB_PATH, PAIR, "SELL")
        if sell_limit is None or sell_limit == 0:
            sell_msg = _fmt_decision("SELL", sell_open, sell_limit, None)
        elif sell_open > sell_limit:
            try:
                r = impl.consolidate_sells(cli, sess, dry_run); print(r)
            except Exception as e:
                print(f"SELL error: {e}", file=sys.stderr); rc |= 2
            sell_msg = _fmt_decision("SELL", sell_open, sell_limit, None)
        else:
            sell_msg = _fmt_decision("SELL", sell_open, sell_limit, "недостаточно открытых")

        print(f"[PAIR={PAIR}] {buy_msg} | {sell_msg} | dry={dry_run}")
        return rc
    finally:
        try: sess.close()
        except Exception: pass

def parse_args(argv) -> Tuple[Optional[int], Optional[int], bool]:
    ap = argparse.ArgumentParser(description="Консолидация ордеров. 0 = пропустить. Без параметров = из конфига.")
    ap.add_argument("--dry-run", action="store_true", help="Диагностика без реальных отмен/постановки")
    ap.add_argument("BUY_LIMIT",  nargs="?", type=int, default=None, help="Лимит на BUY (0=skip, None=из конфига)")
    ap.add_argument("SELL_LIMIT", nargs="?", type=int, default=None, help="Лимит на SELL (0=skip, None=из конфига)")
    a = ap.parse_args(argv)
    return a.BUY_LIMIT, a.SELL_LIMIT, a.dry_run

def main():
    buy_limit, sell_limit, dry = parse_args(sys.argv[1:])
    sys.exit(run(buy_limit, sell_limit, dry))

if __name__ == "__main__":
    main()
