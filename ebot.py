#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time
import traceback
from datetime import datetime, timezone, timedelta

from config import (
    PAUSE_TRADING,
    EBOT_SYNC_INTERVAL_SEC,
    EBOT_BUY_INTERVAL_SEC,
    EBOT_SELL_INTERVAL_SEC,
    EBOT_REPORT_INTERVAL_SEC,
)
from notify import send_error, send_message

# шаговые модули (каждый обязан иметь main())
import sync as sync_mod
import buy as buy_mod
import sell as sell_mod
import report as report_mod

MSK = timezone(timedelta(hours=3))
now = lambda: time.time()


def safe_call(tag, fn):
    try:
        return fn()
    except Exception as e:
        try:
            send_error(tag, e)
        except Exception:
            traceback.print_exc()


def main():
    t0 = now()
    next_sync   = t0 + EBOT_SYNC_INTERVAL_SEC
    next_buy    = t0 + EBOT_BUY_INTERVAL_SEC
    next_sell   = t0 + EBOT_SELL_INTERVAL_SEC + 2  # небольшой сдвиг, чтобы не пересекалось с buy
    next_report = t0 + EBOT_REPORT_INTERVAL_SEC

    try:
        send_message(f"▶️ ebot started | trading={'OFF' if PAUSE_TRADING else 'ON'} | {datetime.now(MSK):%Y-%m-%d %H:%M:%S %Z}")
    except Exception:
        pass

    while True:
        t = now()
        if t >= next_sync:
            safe_call("sync.tick",   lambda: sync_mod.main())
            while next_sync <= t:
                next_sync += EBOT_SYNC_INTERVAL_SEC

        if not PAUSE_TRADING and t >= next_buy:
            safe_call("buy.tick",    lambda: buy_mod.main())
            while next_buy <= t:
                next_buy += EBOT_BUY_INTERVAL_SEC

        if not PAUSE_TRADING and t >= next_sell:
            safe_call("sell.tick",   lambda: sell_mod.main())
            while next_sell <= t:
                next_sell += EBOT_SELL_INTERVAL_SEC

        if t >= next_report:
            safe_call("report.tick", lambda: report_mod.main())
            while next_report <= t:
                next_report += EBOT_REPORT_INTERVAL_SEC

        t = now()
        sleep_for = min(
            max(0.05, next_sync   - t),
            max(0.05, next_buy    - t) if not PAUSE_TRADING else 99999,
            max(0.05, next_sell   - t) if not PAUSE_TRADING else 99999,
            max(0.05, next_report - t),
        )
        time.sleep(min(1.0, sleep_for))


if __name__ == "__main__":
    main()
