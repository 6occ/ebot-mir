#!/usr/bin/env python3
import subprocess, sys, runpy, os

def run(cmd, shell=False):
    print(f"\n=== RUN: {cmd if shell else ' '.join(cmd)} ===")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, shell=shell)
        print(res.stdout.strip() or "(no stdout)")
        if res.stderr.strip():
            print("STDERR:", res.stderr.strip())
        print("rc=", res.returncode)
    except Exception as e:
        print("ERROR:", e)

# 1) Явно без буферизации
os.environ["PYTHONUNBUFFERED"] = "1"
run(["/opt/Ebot/venv/bin/python3", "-u", "/opt/Ebot/consolidate.py", "--dry-run"])

# 2) Запуск через runpy с faulthandler
print("\n=== RUN via runpy ===")
import faulthandler; faulthandler.enable()
sys.argv = ["consolidate.py", "--dry-run"]
try:
    runpy.run_path("/opt/Ebot/consolidate.py", run_name="__main__")
    print(">>> finished consolidate.py (runpy)")
except Exception as e:
    print("EXC:", e)

# 3) STDOUT / STDERR раздельно
print("\n=== RUN with redirected IO ===")
subprocess.run("/opt/Ebot/venv/bin/python3 /opt/Ebot/consolidate.py --dry-run 1>/tmp/cons.out 2>/tmp/cons.err", shell=True)
print("--- STDOUT ---")
print(open("/tmp/cons.out").read())
print("--- STDERR ---")
print(open("/tmp/cons.err").read())

# 4) Конфиг
print("\n=== CONFIG FLAGS ===")
from config import (
    CONSOLIDATE_BUY_ENABLED, CONSOLIDATE_SELL_ENABLED,
    CONSOLIDATE_BUY_LIMIT_OVER, CONSOLIDATE_SELL_LIMIT_OVER,
    CONSOLIDATE_BUY_TO_CANCEL, CONSOLIDATE_SELL_TO_CANCEL,
    CONSOLIDATE_BUY_PLACE_COUNT, CONSOLIDATE_SELL_PLACE_COUNT
)
print("BUY_ENABLED =", CONSOLIDATE_BUY_ENABLED, "SELL_ENABLED =", CONSOLIDATE_SELL_ENABLED)
print("BUY limits:", CONSOLIDATE_BUY_LIMIT_OVER, CONSOLIDATE_BUY_TO_CANCEL, CONSOLIDATE_BUY_PLACE_COUNT)
print("SELL limits:", CONSOLIDATE_SELL_LIMIT_OVER, CONSOLIDATE_SELL_TO_CANCEL, CONSOLIDATE_SELL_PLACE_COUNT)

# 5) Журнал оркестратора
run("journalctl -u ebot.service -n 100 --no-pager | egrep -i 'consolidate|ERROR|STDOUT|STDERR'", shell=True)
