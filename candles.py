import json
import time
import threading
import logging
from logging.handlers import TimedRotatingFileHandler
import requests
import signal
import websocket
from datetime import datetime, timezone

from config import (
    PAIR, MEXC_HTTP_URL, MEXC_WS_URL,
    MAX_CANDLE_GAP, HTTP_TIMEOUT
)
from models import SessionLocal, MinMax, init_db
from notify import send_error

# --- keepalive ---
SOCKET_PING_INTERVAL = 15
SOCKET_PING_TIMEOUT  = 10
APP_PING_INTERVAL    = 20

# --- logging ---
logger = logging.getLogger("candles")
logger.setLevel(logging.INFO)
fh = TimedRotatingFileHandler("/opt/Ebot/logs/candles.log", when="W0", backupCount=8, encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(fh)
logger.addHandler(sh)

init_db()

# ================== БАЗА ДАННЫХ ==================

def insert_candles_rest(rows):
    """
    rows: [[openTime(ms), open, high, low, close, volume, closeTime, ...], ...]
    """
    s = SessionLocal()
    try:
        for r in rows:
            t = int(r[0]) // 1000
            o, h, l, c = map(float, (r[1], r[2], r[3], r[4]))
            s.merge(MinMax(pair=PAIR, time=t, min=l, max=h, mid=(h+l)/2, open=o, close=c))
        # удалить старше 24ч
        expire = int(time.time()) - 86400
        s.query(MinMax).filter(MinMax.time < expire).delete()
        s.commit()
    finally:
        s.close()

def last_saved_time():
    s = SessionLocal()
    try:
        row = s.query(MinMax).filter(MinMax.pair == PAIR).order_by(MinMax.time.desc()).first()
        return row.time if row else 0
    finally:
        s.close()

# ================== ДОЗАГРУЗКА ПРОПУСКОВ ==================

def fetch_missing(pair, last_t):
    now_aligned = int(time.time() // 60 * 60)
    gap = (now_aligned - last_t) // 60
    if gap <= 0:
        return
    if gap > MAX_CANDLE_GAP:
        gap = MAX_CANDLE_GAP
    logger.info(f"[SYNC] Fetching {gap} missing candles via HTTP...")
    try:
        r = requests.get(MEXC_HTTP_URL, params={"symbol": pair.upper(), "interval": "1m", "limit": gap}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        insert_candles_rest(r.json())
    except Exception as e:
        logger.error("HTTP fetch error: %r", e)
        try: send_error("candles HTTP fetch", e)
        except Exception: pass

def fetch_last_closed(pair):
    """Подтягивает последнюю закрытую 1m свечу (limit=1) и сохраняет её."""
    try:
        r = requests.get(MEXC_HTTP_URL, params={"symbol": pair.upper(), "interval": "1m", "limit": 1}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            insert_candles_rest(data)
            t = int(data[-1][0]) // 1000
            ts = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[RECV] {PAIR} {ts} stored last closed candle")
    except Exception as e:
        logger.error("fetch_last_closed error: %r", e)
        try: send_error("candles last_closed", e)
        except Exception: pass

# ================== МИНУТНЫЙ ТИКЕР ==================

_tick_stop = threading.Event()

def minute_tick_loop():
    """Каждую новую минуту подтягиваем последнюю закрытую свечу по HTTP."""
    now = time.time()
    next_minute = int(now // 60 + 1) * 60
    delay = max(0.5, next_minute - now + 0.2)
    _tick_stop.wait(delay)

    last_min = int(time.time() // 60)
    logger.info("Minute ticker started")
    while not _tick_stop.is_set():
        cur_min = int(time.time() // 60)
        if cur_min != last_min:
            fetch_last_closed(PAIR)
            last_min = cur_min
        _tick_stop.wait(0.5)

# ================== WS HEARTBEAT ==================

_ws = None
_hb_stop = threading.Event()

def hb_loop():
    while not _hb_stop.is_set():
        try:
            if _ws:
                _ws.send(json.dumps({"method": "PING", "id": 999}))
        except Exception:
            pass
        _hb_stop.wait(APP_PING_INTERVAL)

def maybe_pong(ws, payload):
    if not isinstance(payload, dict):
        return False
    if payload.get("method", "").upper() == "PING":
        ws.send(json.dumps({"method": "PONG", "id": payload.get("id", 1)})); return True
    if payload.get("op", "").lower() == "ping":
        ws.send(json.dumps({"op": "pong", "ts": payload.get("ts")})); return True
    if "ping" in payload:
        ws.send(json.dumps({"pong": payload["ping"]})); return True
    return False

# ================== WS CALLBACKS ==================

def on_open(ws):
    logger.info("Connected to WebSocket")
    sub = {
        "method": "SUBSCRIPTION",
        "params": [f"spot@public.kline.v3.api.pb@{PAIR.upper()}@Min1"],  # protobuf канал
        "id": 1
    }
    ws.send(json.dumps(sub))
    _hb_stop.clear()
    threading.Thread(target=hb_loop, daemon=True).start()

def on_message(ws, message):
    # .pb канал отдаёт бинарь; текстом прилетает только служебный JSON
    if isinstance(message, (bytes, bytearray)):
        tstr = datetime.now(timezone.utc).strftime("%H:%M:%S")
        logger.info(f"[PB] frame {len(message)} bytes @ {tstr}")
        return
    try:
        payload = json.loads(message)
    except Exception:
        return
    if maybe_pong(ws, payload):
        return
    if isinstance(payload, dict) and payload.get("code") == 0 and payload.get("id") == 1:
        logger.info("SUB ACK")

def on_close(ws, *_):
    logger.info("WebSocket closed")
    _hb_stop.set()

def on_error(ws, error):
    logger.error("WS error: %r", error)
    try: send_error("candles WS error", error)
    except Exception: pass

# ================== MAIN ==================

def _handle_term(signum, frame):
    try:
        _hb_stop.set(); _tick_stop.set()
        ws = _ws
        if ws: ws.close()
    finally:
        sys.exit(0)

def run():
    signal.signal(signal.SIGTERM, _handle_term)
    global _ws
    # стартуем минутный тикер
    _tick_stop.clear()
    threading.Thread(target=minute_tick_loop, daemon=True).start()

    # дозагружаем пропуски при старте
    fetch_missing(PAIR, last_saved_time())

    while True:
        ws = websocket.WebSocketApp(
            MEXC_WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        _ws = ws
        try:
            ws.run_forever(ping_interval=SOCKET_PING_INTERVAL, ping_timeout=SOCKET_PING_TIMEOUT)
        except KeyboardInterrupt:
            logger.info("Interrupted by user — exiting")
            _hb_stop.set()
            _tick_stop.set()
            try:
                ws.close()
            finally:
                break
        except Exception as e:
            logger.error("WS crash: %r", e)
            try: send_error("candles crash", e)
            except Exception: pass
        time.sleep(5)

if __name__ == "__main__":
    run()
