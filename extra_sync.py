#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from mexc_client import MexcClient
from models_trading import SessionT, init_trading_db
from sync import sync_trades, sync_open_orders, sync_balance, recompute_position

def run(window_min: int):
    """
    Дополнительный «глубокий» синк:
      1) myTrades за окно window_min минут (upsert в fills)
      2) openOrders (upsert/закрытие в orders)
      3) баланс (capital.available_usd)
      4) пересчёт позиции из всех fills (position.qty/avg)
    """
    init_trading_db()
    sess = SessionT()
    cli  = MexcClient()
    try:
        inserted = sync_trades(sess, cli, window_min)
        sync_open_orders(sess, cli, window_min)
        sync_balance(sess, cli)
        qty, avg = recompute_position(sess)
        print(f"RESYNC: window={window_min}m | trades+{inserted} | pos qty={qty:.6f} avg={avg}")
    finally:
        sess.close()

if __name__ == "__main__":
    win = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run(win)
