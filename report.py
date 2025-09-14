#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, sqlite3, math, time
from statistics import mean
from datetime import datetime, timezone, timedelta

# --- конфиг ---
try:
    from config import DB_PATH, PAIR, START_CAPITAL_USD
except Exception:
    DB_PATH = "/opt/Ebot/ebot.db"
    PAIR = "KASUSDC"
    START_CAPITAL_USD = 1000.0

# --- уведомления ---
def _send(text: str) -> None:
    try:
        from notify import send_message
        send_message(text)
    except Exception:
        # не роняем отчёт, просто молча продолжаем
        pass

def _fmt_usd(x: float) -> str:
    s = f"{x:,.2f}"
    return s.replace(",", " ")

def _fmt_pct(x: float) -> str:
    return f"{x:.1f}%"

def _row_to_dict(cur, row):
    return {d[0]: row[i] for i, d in enumerate(cur.description)}

def _now_utc_ts() -> int:
    return int(time.time())

def _msk_now_str() -> str:
    # визуальная метка отчёта в MSK
    return datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")

def fetch_candles(conn, pair: str, since_sec: int):
    cur = conn.cursor()
    cur.execute("""
        SELECT time, min, max, mid, open, close
        FROM minmax
        WHERE pair=? AND time>=?
        ORDER BY time ASC
    """, (pair, since_sec))
    rows = cur.fetchall()
    return rows

def fetch_last_close(conn, pair: str):
    cur = conn.cursor()
    cur.execute("""
        SELECT time, close FROM minmax
        WHERE pair=? ORDER BY time DESC LIMIT 1
    """, (pair,))
    r = cur.fetchone()
    return (r[0], float(r[1])) if r else (None, None)

def fetch_close_at_or_before(conn, pair: str, ts: int):
    cur = conn.cursor()
    cur.execute("""
        SELECT time, close FROM minmax
        WHERE pair=? AND time<=?
        ORDER BY time DESC LIMIT 1
    """, (pair, ts))
    r = cur.fetchone()
    return (r[0], float(r[1])) if r else (None, None)

def fetch_position(conn, pair: str):
    # простая позиция (qty, avg) — берём последнюю запись
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT qty, avg FROM positions
            WHERE pair=? ORDER BY updated DESC LIMIT 1
        """, (pair,))
    except Exception:
        # fallback: некоторые схемы называют updated как 'time'
        cur.execute("""
            SELECT qty, avg FROM positions
            WHERE pair=? ORDER BY time DESC LIMIT 1
        """, (pair,))
    r = cur.fetchone()
    if not r:
        return 0.0, 0.0
    qty = float(r[0] or 0.0)
    avg = float(r[1] or 0.0)
    return qty, avg

def fetch_open_buy_reserve(conn, pair: str):
    # резерв по открытым BUY = сумма price*qty
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT COALESCE(SUM(price*qty),0)
            FROM orders
            WHERE pair=? AND side='BUY' AND status IN ('NEW','PARTIALLY_FILLED')
        """, (pair,))
        r = cur.fetchone()
        return float(r[0] or 0.0)
    except Exception:
        return 0.0

def fetch_recent_orders_preview(conn, pair: str, limit: int = 10):
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT time, side, price, qty, status
            FROM orders
            WHERE pair=?
            ORDER BY time DESC
            LIMIT ?
        """, (pair, limit))
    except Exception:
        # альтернативные поля created/updated
        cur.execute("""
            SELECT COALESCE(updated,created) AS time, side, price, qty, status
            FROM orders
            WHERE pair=?
            ORDER BY COALESCE(updated,created) DESC
            LIMIT ?
        """, (pair, limit))
    out = []
    for t, side, price, qty, status in cur.fetchall():
        dt = datetime.utcfromtimestamp(int(t)).strftime("%H:%M:%S") if t else "--:--:--"
        out.append(f"{dt} | {side} @ {float(price):.6f} | qty={float(qty):g} | {status}")
    return out

def compute_channel_24h(candles):
    if not candles:
        return None
    mins = [float(r[1]) for r in candles if r[1] is not None]
    maxs = [float(r[2]) for r in candles if r[2] is not None]
    mids = [float(r[3]) for r in candles if r[3] is not None]
    if not mins or not maxs:
        return None
    mn = min(mins); mx = max(maxs)
    mid_avg = mean(mids) if mids else (mn + mx) / 2.0
    spread = max(0.0, mx - mn)
    lower = max(0.0, mid_avg - spread / 4.0)
    upper = max(0.0, mid_avg + spread / 4.0)
    return dict(lower=lower, upper=upper, mid=mid_avg, spread=spread)

def calc_pnl_blocks(last_px: float, qty: float, avg: float, px_1h: float|None, px_24h: float|None):
    # 1) краткосрочный PnL считаем по изменению цены * текущий qty
    def _pnl_win(p0):
        if p0 is None or last_px is None:
            return (None, None)
        abs_usd = (last_px - p0) * qty
        base = max(1e-9, last_px * qty)
        pct = (abs_usd / base) * 100.0
        return (abs_usd, pct)

    pnl1_abs, pnl1_pct = _pnl_win(px_1h)
    pnl24_abs, pnl24_pct = _pnl_win(px_24h)

    # 2) «Всего» — нереализованный против стартового капитала:
    #    (last - avg)*qty относительно START_CAPITAL_USD
    total_abs = (last_px - avg) * qty if (last_px is not None and avg) else 0.0
    total_pct = (total_abs / max(1e-9, START_CAPITAL_USD)) * 100.0

    return (pnl1_abs, pnl1_pct, pnl24_abs, pnl24_pct, total_abs, total_pct)

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    now = _now_utc_ts()
    rows24 = fetch_candles(conn, PAIR, now - 24*3600)
    _, last_px = fetch_last_close(conn, PAIR)
    t1h = now - 3600
    t24h = now - 24*3600
    _, px_1h = fetch_close_at_or_before(conn, PAIR, t1h)
    _, px_24h = fetch_close_at_or_before(conn, PAIR, t24h)

    ch = compute_channel_24h(rows24)
    qty, avg = fetch_position(conn, PAIR)
    reserve_usd = fetch_open_buy_reserve(conn, PAIR)

    position_val = (last_px or 0.0) * qty
    total_equity_est = START_CAPITAL_USD + (last_px - avg) * qty if avg and last_px else START_CAPITAL_USD
    # доступный кэш оценочно как equity - позиция - резерв (не идеально, но безопасно)
    cash_est = max(0.0, total_equity_est - position_val - reserve_usd)

    pnl1_abs, pnl1_pct, pnl24_abs, pnl24_pct, total_abs, total_pct = calc_pnl_blocks(
        last_px or 0.0, qty, avg, px_1h, px_24h
    )

    # сборка отчёта
    lines = []
    lines.append(f"🧾 Отчёт по {PAIR} (MSK) {_msk_now_str()}")
    lines.append("Период: 30 мин")
    lines.append(f"Цена: {last_px:.6f}" if last_px is not None else "Цена: n/a")
    if ch:
        lines.append(f"Канал: [{ch['lower']:.6f}..{ch['upper']:.6f}]")
    else:
        lines.append("Канал: n/a")

    lines.append("💼 Баланс и позиция")
    lines.append("Торговля: ON")
    lines.append(f"Итого: ${_fmt_usd(total_equity_est)}")
    lines.append(f"Доступно: ${_fmt_usd(cash_est)}")
    lines.append(f"В ордерах (BUY, резерв): ${_fmt_usd(reserve_usd)}")
    lines.append(f"Позиция: {qty:g} {PAIR.replace('USDC','')} AVG: {avg:.6f}")
    lines.append(f"Стоимость позиции: ${_fmt_usd(position_val)}")

    # PNL блок
    def _fmt_pnl(name, a, p):
        if a is None or p is None:
            return f"{name}: n/a"
        sign = "+" if a >= 0 else ""
        return f"{name}: {sign}{a:.2f}$ ({sign}{_fmt_pct(p)})"

    lines.append("PNL")
    lines.append(_fmt_pnl("1 час", pnl1_abs, pnl1_pct))
    lines.append(_fmt_pnl("24 часа", pnl24_abs, pnl24_pct))
    lines.append(_fmt_pnl("Всего", total_abs, total_pct))

    # немного статистики по ордерам (не критично)
    try:
        preview = fetch_recent_orders_preview(conn, PAIR, 10)
        # быстрые счётчики по открытым
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM orders WHERE pair=? AND side='BUY'", (PAIR,))
        buy_cnt = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM orders WHERE pair=? AND side='SELL'", (PAIR,))
        sell_cnt = int(cur.fetchone()[0])
        lines.append("📊 Статистика")
        lines.append(f"BUY={buy_cnt} | SELL={sell_cnt}")
        lines.append("10 последних ордеров:")
        lines.extend(preview)
    except Exception:
        pass

    out = "\n".join(lines)

    # отправка в ТГ + вывод в stdout
    _send(out)
    print(out)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # не проглатываем полностью — покажем в stdout/stderr
        import traceback
        msg = f"[report] ERROR: {e}\n{traceback.format_exc()}"
        try:
            from notify import send_error
            send_error("report", msg)
        except Exception:
            pass
        print(msg, file=sys.stderr)
        sys.exit(1)
