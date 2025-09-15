# -*- coding: utf-8 -*-
import sqlite3
import time
from typing import List, Tuple, Optional, Dict
from statistics import mean
from datetime import datetime, timezone, timedelta

try:
    from config import DB_PATH, PAIR, START_CAPITAL_USD
except Exception:
    DB_PATH = "/opt/Ebot/ebot.db"
    PAIR = "KASUSDC"
    START_CAPITAL_USD = 1000.0

def _now_utc_ts() -> int:
    return int(time.time())

def _msk_now_str() -> str:
    return datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")

def _fmt_usd(x: float) -> str:
    try:
        s = f"{x:,.2f}"
        return s.replace(",", " ")
    except Exception:
        return f"{x:.2f}"

def _fmt_pct(x: float) -> str:
    return f"{x:.1f}%"

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,))
        return cur.fetchone() is not None
    except Exception:
        return False

def fetch_candles(conn, pair: str, since_sec: int):
    if not _table_exists(conn, "minmax"):
        return []
    cur = conn.cursor()
    cur.execute("""
        SELECT time, min, max, mid, open, close
        FROM minmax
        WHERE pair=? AND time>=?
        ORDER BY time ASC
    """, (pair, since_sec))
    return cur.fetchall()

def fetch_last_close(conn, pair: str):
    if not _table_exists(conn, "minmax"):
        return (None, None)
    cur = conn.cursor()
    cur.execute("""
        SELECT time, close FROM minmax
        WHERE pair=? ORDER BY time DESC LIMIT 1
    """, (pair,))
    r = cur.fetchone()
    return (r[0], float(r[1])) if r else (None, None)

def fetch_close_at_or_before(conn, pair: str, ts: int):
    if not _table_exists(conn, "minmax"):
        return (None, None)
    cur = conn.cursor()
    cur.execute("""
        SELECT time, close FROM minmax
        WHERE pair=? AND time<=?
        ORDER BY time DESC LIMIT 1
    """, (pair, ts))
    r = cur.fetchone()
    return (r[0], float(r[1])) if r else (None, None)

def fetch_position_from_positions_table(conn, pair: str):
    if not _table_exists(conn, "positions"):
        return None
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT qty, avg FROM positions
            WHERE pair=? ORDER BY updated DESC LIMIT 1
        """, (pair,))
    except Exception:
        cur.execute("""
            SELECT qty, avg FROM positions
            WHERE pair=? ORDER BY time DESC LIMIT 1
        """, (pair,))
    r = cur.fetchone()
    if not r:
        return (0.0, 0.0)
    qty = float(r[0] or 0.0)
    avg = float(r[1] or 0.0)
    return (qty, avg)

def fetch_position_from_orders(conn, pair: str):
    if not _table_exists(conn, "orders"):
        return (0.0, 0.0), False
    cur = conn.cursor()
    cur.execute("""
        SELECT side,
               SUM(COALESCE(filled_qty,
                            CASE WHEN status='FILLED' THEN qty ELSE 0 END)) AS fqty,
               SUM((COALESCE(filled_qty,
                            CASE WHEN status='FILLED' THEN qty ELSE 0 END)) * price) AS fval
        FROM orders
        WHERE pair=? AND status IN ('FILLED','PARTIALLY_FILLED','NEW','CANCELED')
        GROUP BY side
    """, (pair,))
    rows = {row[0]: (float(row[1] or 0.0), float(row[2] or 0.0)) for row in cur.fetchall()}
    b_qty, b_val = rows.get('BUY', (0.0, 0.0))
    s_qty, s_val = rows.get('SELL', (0.0, 0.0))
    qty = b_qty - s_qty
    cost = b_val - s_val
    if qty > 0:
        avg = max(0.0, cost / max(qty, 1e-12))
    else:
        avg = 0.0
        qty = max(0.0, qty)
    return (qty, avg), True

def fetch_open_buy_reserve(conn, pair: str):
    if not _table_exists(conn, "orders"):
        return 0.0
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

def calc_pnl_blocks(last_px: float, qty: float, avg: float, px_1h: Optional[float], px_24h: Optional[float]):
    def _pnl_win(p0):
        if p0 is None or last_px is None:
            return (None, None)
        abs_usd = (last_px - p0) * qty
        base = max(1e-9, last_px * qty)
        pct = (abs_usd / base) * 100.0
        return (abs_usd, pct)

    pnl1_abs, pnl1_pct = _pnl_win(px_1h)
    pnl24_abs, pnl24_pct = _pnl_win(px_24h)
    total_abs = (last_px - avg) * qty if (last_px is not None and avg) else 0.0
    total_pct = (total_abs / max(1e-9, START_CAPITAL_USD)) * 100.0
    return (pnl1_abs, pnl1_pct, pnl24_abs, pnl24_pct, total_abs, total_pct)

def build_report_text(mode: str = "daily") -> str:
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

    pos = fetch_position_from_positions_table(conn, PAIR)
    used_synthetic = False
    if pos is None:
        (qty, avg), used_synthetic = fetch_position_from_orders(conn, PAIR)
    else:
        qty, avg = pos

    reserve_usd = fetch_open_buy_reserve(conn, PAIR)

    position_val = (last_px or 0.0) * qty
    total_equity_est = START_CAPITAL_USD + (last_px - avg) * qty if avg and last_px else START_CAPITAL_USD
    cash_est = max(0.0, total_equity_est - position_val - reserve_usd)

    pnl1_abs, pnl1_pct, pnl24_abs, pnl24_pct, total_abs, total_pct = calc_pnl_blocks(
        last_px or 0.0, qty, avg, px_1h, px_24h
    )

    lines: List[str] = []
    lines.append(f"🧾 Отчёт по {PAIR} (MSK) {_msk_now_str()}")
    lines.append("Период: 30 мин" if mode == "daily" else "Период: 7 дней")
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
    base_sym = PAIR.replace('USDC','')
    src_note = "(из orders)" if used_synthetic else ""
    lines.append(f"Позиция{(' ' + src_note) if src_note else ''}: {qty:g} {base_sym} AVG: {avg:.6f}")
    lines.append(f"Стоимость позиции: ${_fmt_usd(position_val)}")

    def _fmt_pnl(name, a, p):
        if a is None or p is None:
            return f"{name}: n/a"
        sign = "+" if a >= 0 else ""
        return f"{name}: {sign}{a:.2f}$ ({sign}{_fmt_pct(p)})"

    lines.append("PNL")
    lines.append(_fmt_pnl("1 час", pnl1_abs, pnl1_pct))
    lines.append(_fmt_pnl("24 часа", pnl24_abs, pnl24_pct))
    lines.append(_fmt_pnl("Всего", total_abs, total_pct))

    if _table_exists(conn, "orders"):
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM orders WHERE pair=? AND side='BUY'", (PAIR,))
            buy_cnt = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM orders WHERE pair=? AND side='SELL'", (PAIR,))
            sell_cnt = int(cur.fetchone()[0])
            lines.append("📊 Статистика")
            lines.append(f"BUY={buy_cnt} | SELL={sell_cnt}")
        except Exception:
            pass

    return "\n".join(lines)

def run(mode: str = "daily") -> str:
    mode = (mode or "daily").lower().strip()
    if mode not in ("daily", "weekly"):
        mode = "daily"
    return build_report_text(mode=mode)
