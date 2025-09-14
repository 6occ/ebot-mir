#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, time, random, subprocess, traceback
from datetime import datetime

from config import (
    # toggles & intervals
    ENABLE_SYNC, ENABLE_BUY, ENABLE_SELL, ENABLE_REPORT,
    EBOT_SYNC_INTERVAL_SEC, EBOT_BUY_INTERVAL_SEC, EBOT_SELL_INTERVAL_SEC, EBOT_REPORT_INTERVAL_SEC,
    # consolidator
    ENABLE_CONSOLIDATE, CONSOLIDATE_CHECK_EVERY_SEC,
    # jitter
    SCHEDULER_JITTER_MAX_SEC,
)

# уведомления — мягкий импорт
try:
    from notify import send_error, send_message
except Exception:
    def send_error(where, e):  # noqa: D401
        print(f"[ERROR] {where}: {e}")
    def send_message(text):
        print(f"[MSG] {text}")

ROOT = "/opt/Ebot"
PYBIN = os.path.join(ROOT, "venv", "bin", "python3")

def _jitter(seconds: int) -> float:
    """Возвращает секунды с джиттером ±SCHEDULER_JITTER_MAX_SEC."""
    j = random.uniform(-float(SCHEDULER_JITTER_MAX_SEC), float(SCHEDULER_JITTER_MAX_SEC))
    return max(1.0, float(seconds) + j)

def run_cmd(name: str, argv: list[str]) -> None:
    """Запуск подпроцесса с логом и обработкой ошибок."""
    try:
        t0 = time.time()
        res = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=None)
        dt = time.time() - t0
        if res.returncode != 0:
            msg = f"{name} exit={res.returncode} in {dt:.2f}s\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
            send_error(name, msg)
        else:
            if res.stdout.strip():
                print(f"[{name}] {dt:.2f}s STDOUT:\n{res.stdout.strip()}")
            if res.stderr.strip():
                print(f"[{name}] {dt:.2f}s STDERR:\n{res.stderr.strip()}")
    except Exception as e:
        send_error(name, f"{e}\n{traceback.format_exc()}")

def task_sync():
    run_cmd("sync", [PYBIN, os.path.join(ROOT, "sync.py")])

def task_buy():
    run_cmd("buy", [PYBIN, os.path.join(ROOT, "buy.py")])

def task_sell():
    run_cmd("sell", [PYBIN, os.path.join(ROOT, "sell.py")])

def task_report():
    run_cmd("report", [PYBIN, os.path.join(ROOT, "report.py")])

def task_consolidate():
    # Без аргументов — вся логика и лимиты внутри consolidate.py / config.py
    run_cmd("consolidate", [PYBIN, os.path.join(ROOT, "consolidate.py")])

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def main():
    # стартовое сообщение
    try:
        send_message(f"▶️ ebot started | sync={ENABLE_SYNC} buy={ENABLE_BUY} sell={ENABLE_SELL} report={ENABLE_REPORT} consolidate={ENABLE_CONSOLIDATE} | {_now()}")
    except Exception:
        pass

    next_run = {}

    # инициализация расписания
    t = time.time()
    if ENABLE_SYNC:
        next_run["sync"] = t + _jitter(EBOT_SYNC_INTERVAL_SEC)
    if ENABLE_BUY:
        next_run["buy"] = t + _jitter(EBOT_BUY_INTERVAL_SEC)
    if ENABLE_SELL:
        next_run["sell"] = t + _jitter(EBOT_SELL_INTERVAL_SEC)
    if ENABLE_REPORT:
        next_run["report"] = t + _jitter(EBOT_REPORT_INTERVAL_SEC)
    if ENABLE_CONSOLIDATE:
        next_run["consolidate"] = t + _jitter(CONSOLIDATE_CHECK_EVERY_SEC)

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
    main()
