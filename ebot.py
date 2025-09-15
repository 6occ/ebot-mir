#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import time
import random
import subprocess
import traceback
import shlex
import sys
import atexit
import logging
from datetime import datetime

PID_FILE = "/tmp/ebot.pid"

def check_singleton():
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            os.kill(old_pid, 0)
            print(f"ebot уже запущен (PID={old_pid})", file=sys.stderr)
            sys.exit(1)
        except Exception:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.remove(PID_FILE) if os.path.exists(PID_FILE) else None)

# === CONFIG (с мягкими дефолтами) ===
try:
    from config import (
        ENABLE_SYNC, ENABLE_BUY, ENABLE_SELL, ENABLE_REPORT,
        EBOT_SYNC_INTERVAL_SEC, EBOT_BUY_INTERVAL_SEC, EBOT_SELL_INTERVAL_SEC, EBOT_REPORT_INTERVAL_SEC,
        ENABLE_CONSOLIDATE, CONSOLIDATE_CHECK_EVERY_SEC,
    )
except Exception:
    ENABLE_SYNC = True
    ENABLE_BUY = True
    ENABLE_SELL = True
    ENABLE_REPORT = True
    EBOT_SYNC_INTERVAL_SEC = 60
    EBOT_BUY_INTERVAL_SEC = 30
    EBOT_SELL_INTERVAL_SEC = 30
    EBOT_REPORT_INTERVAL_SEC = 1800
    ENABLE_CONSOLIDATE = True
    CONSOLIDATE_CHECK_EVERY_SEC = 300

_CONS_BUY_LIMIT = None
_CONS_SELL_LIMIT = None
_CONS_DRY_RUN   = False
try:
    from config import CONSOLIDATE_BUY_LIMIT_OVER as _CONS_BUY_LIMIT
except Exception: pass
try:
    from config import CONSOLIDATE_SELL_LIMIT_OVER as _CONS_SELL_LIMIT
except Exception: pass
try:
    from config import CONSOLIDATE_DRY_RUN as _CONS_DRY_RUN
except Exception: pass

def _send_error(where, e):
    try:
        from notify import send_error
        send_error(where, str(e))
    except Exception:
        print(f"[ERROR] {where}: {e}")

def _send_message(text):
    try:
        from notify import send_message
        send_message(text)
    except Exception:
        print(f"[MSG] {text}")

ROOT = "/opt/Ebot"
PYBIN = os.path.join(ROOT, "venv", "bin", "python3")
if not os.path.exists(PYBIN):
    PYBIN = "python3"

def _jitter(seconds: int) -> float:
    j = max(1.0, float(seconds) * 0.1)
    return max(1.0, float(seconds) + random.uniform(-j, j))

def run_cmd(name: str, argv: list[str]) -> None:
    try:
        t0 = time.time()
        res = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=None)
        dt = time.time() - t0
        if res.returncode != 0:
            msg = f"{name} exit={res.returncode} in {dt:.2f}s\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
            _send_error(name, msg)
        else:
            out = (res.stdout or "").strip()
            err = (res.stderr or "").strip()
            if out:
                print(f"[{name}] {dt:.2f}s STDOUT:\n{out}")
            if err:
                print(f"[{name}] {dt:.2f}s STDERR:\n{err}")
    except Exception as e:
        _send_error(name, f"{e}\n{traceback.format_exc()}")

def task_sync(): run_cmd("sync", [PYBIN, os.path.join(ROOT, "sync.py")])
def task_buy(): run_cmd("buy", [PYBIN, os.path.join(ROOT, "buy.py")])
def task_sell(): run_cmd("sell", [PYBIN, os.path.join(ROOT, "sell.py")])
def task_report(): run_cmd("report", [PYBIN, os.path.join(ROOT, "report.py")])

def _build_cons_argv() -> list[str]:
    cons_path = os.path.join(ROOT, "cons")
    argv = [cons_path]
    if _CONS_DRY_RUN:
        argv.append("--dry-run")
    if _CONS_BUY_LIMIT is not None or _CONS_SELL_LIMIT is not None:
        argv.append(str(_CONS_BUY_LIMIT if _CONS_BUY_LIMIT is not None else ""))
        argv.append(str(_CONS_SELL_LIMIT if _CONS_SELL_LIMIT is not None else ""))
    return argv

def task_consolidate():
    argv = _build_cons_argv()
    print(f"[consolidate] run: {shlex.join(argv)}")
    run_cmd("consolidate", argv)

def _now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def main():
    try:
        _send_message(
            f"▶️ ebot started | sync={ENABLE_SYNC} buy={ENABLE_BUY} sell={ENABLE_SELL} "
            f"report={ENABLE_REPORT} consolidate={ENABLE_CONSOLIDATE} | {_now()}"
        )
    except Exception: pass

    next_run = {}
    t = time.time()
    if ENABLE_SYNC: next_run["sync"] = t + _jitter(EBOT_SYNC_INTERVAL_SEC)
    if ENABLE_BUY: next_run["buy"] = t + _jitter(EBOT_BUY_INTERVAL_SEC)
    if ENABLE_SELL: next_run["sell"] = t + _jitter(EBOT_SELL_INTERVAL_SEC)
    if ENABLE_REPORT: next_run["report"] = t + _jitter(EBOT_REPORT_INTERVAL_SEC)
    if ENABLE_CONSOLIDATE: next_run["consolidate"] = t + _jitter(CONSOLIDATE_CHECK_EVERY_SEC)

    while True:
        now = time.time()
        if ENABLE_SYNC and now >= next_run.get("sync", now + 1e9):
            task_sync()
            next_run["sync"] = time.time() + _jitter(EBOT_SYNC_INTERVAL_SEC)
        if ENABLE_BUY and now >= next_run.get("buy", now + 1e9):
            task_buy()
            next_run["buy"] = time.time() + _jitter(EBOT_BUY_INTERVAL_SEC)
        if ENABLE_SELL and now >= next_run.get("sell", now + 1e9):
            task_sell()
            next_run["sell"] = time.time() + _jitter(EBOT_SELL_INTERVAL_SEC)
        if ENABLE_REPORT and now >= next_run.get("report", now + 1e9):
            task_report()
            next_run["report"] = time.time() + _jitter(EBOT_REPORT_INTERVAL_SEC)
        if ENABLE_CONSOLIDATE and now >= next_run.get("consolidate", now + 1e9):
            task_consolidate()
            next_run["consolidate"] = time.time() + _jitter(CONSOLIDATE_CHECK_EVERY_SEC)
        time.sleep(1)

if __name__ == "__main__":
    try:
        from logging_config import setup_logging
        setup_logging()
    except Exception:
        pass
    logging.getLogger(__name__).info("ebot starting up")
    check_singleton()
    main()
