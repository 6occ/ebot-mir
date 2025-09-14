# -*- coding: utf-8 -*-
import os, json, time, hashlib, threading, html
from pathlib import Path
from typing import Optional, Tuple
import requests

# --- config bindings (robust) ---
try:
    import config as CFG
except Exception:
    class _C: pass
    CFG = _C()

TOKEN  = getattr(CFG, "TELEGRAM_TOKEN",  getattr(CFG, "TG_BOT_TOKEN",  os.getenv("TELEGRAM_TOKEN", "")))
CHATID = getattr(CFG, "TELEGRAM_CHAT_ID",getattr(CFG, "TG_CHAT_ID",    os.getenv("TELEGRAM_CHAT_ID", "")))
PMODE  = getattr(CFG, "TG_PARSE_MODE", "HTML")      # 'HTML' по умолчанию (менее хрупко чем MarkdownV2)
TG_SILENT_DEFAULT = bool(getattr(CFG, "TG_DISABLE_NOTIFICATION", False))

ERR_COOLDOWN_MIN = int(getattr(CFG, "ERROR_COOLDOWN_MIN", 10))  # мин между повторными ОДИНАКОВЫМИ ошибками
STATE_PATH = Path(getattr(CFG, "NOTIFY_STATE_PATH", "/opt/Ebot/tmp/notify_state.json"))

HTTP_TIMEOUT = float(getattr(CFG, "TG_HTTP_TIMEOUT", 5.0))
RETRIES = int(getattr(CFG, "TG_RETRIES", 2))  # доп. попытки (итого 1+RETRIES)
BACKOFF_SEC = float(getattr(CFG, "TG_BACKOFF_SEC", 1.5))

TELEGRAM_API = None
if TOKEN:
    TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

_lock = threading.RLock()

# --- utils ---
def _ensure_state_dir():
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def _read_state() -> dict:
    _ensure_state_dir()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_state(d: dict):
    _ensure_state_dir()
    tmp = STATE_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass

def _chunk(text: str, limit: int = 3900):
    # запас ниже лимита TG (4096), чтобы влезал служебный хедер и HTML-теги
    s = str(text)
    while s:
        yield s[:limit]
        s = s[limit:]

def _post(payload: dict) -> Tuple[bool, Optional[str]]:
    if not TELEGRAM_API or not CHATID:
        return False, "Telegram not configured (TOKEN/CHAT_ID missing)"
    last_err = None
    for attempt in range(1 + RETRIES):
        try:
            r = requests.post(
                TELEGRAM_API,
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            if r.ok:
                jr = r.json()
                if jr.get("ok"):
                    return True, None
                last_err = f"TG API not ok: {jr}"
            else:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = repr(e)
        # бэк-офф
        if attempt < RETRIES:
            time.sleep(BACKOFF_SEC * (attempt + 1))
    return False, last_err

def _send_text(text: str, silent: Optional[bool] = None, parse_mode: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    if silent is None:
        silent = TG_SILENT_DEFAULT
    if parse_mode is None:
        parse_mode = PMODE

    ok_all = True
    last_err = None
    for part in _chunk(text):
        payload = {
            "chat_id": CHATID,
            "text": part,
            "disable_notification": bool(silent),
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        ok, err = _post(payload)
        if not ok:
            ok_all = False
            last_err = err
    return ok_all, last_err

# --- public API ---
def send_message(text: str, silent: Optional[bool] = None, parse_mode: Optional[str] = None) -> bool:
    """Простая отправка (без кулдауна). Возвращает True/False."""
    try:
        with _lock:
            ok, err = _send_text(text, silent=silent, parse_mode=parse_mode)
        if not ok:
            print(f"[notify] send_message failed: {err}")
        return ok
    except Exception as e:
        print(f"[notify] exception in send_message: {repr(e)}")
        return False

def _err_signature(ctx: str, exc: Exception, details: Optional[str]) -> str:
    base = f"{ctx}|{type(exc).__name__}|{str(exc)}|{details or ''}"
    return hashlib.sha256(base.encode("utf-8", "ignore")).hexdigest()

def _cooldown_passed(sig: str) -> bool:
    st = _read_state()
    now = int(time.time())
    entry = st.get("errors", {}).get(sig)
    if not entry:
        return True
    last = int(entry.get("ts", 0))
    return (now - last) >= ERR_COOLDOWN_MIN * 60

def _mark_sent(sig: str):
    st = _read_state()
    now = int(time.time())
    st.setdefault("errors", {})[sig] = {"ts": now}
    _write_state(st)

def send_error(context: str, exc: Exception, details: Optional[str] = None, silent: Optional[bool] = None) -> bool:
    """
    Отправка ошибки с анти-спамом:
    - одинаковые сигнатуры не шлём чаще, чем раз в ERR_COOLDOWN_MIN минут;
    - новая сигнатура — сразу.
    """
    try:
        sig = _err_signature(context, exc, details)
        with _lock:
            if _cooldown_passed(sig):
                msg = (
                    f"❌ <b>ERROR</b>\n"
                    f"<b>Where:</b> {context}\n"
                    f"<b>Type:</b> {type(exc).__name__}\n"
                    f"<b>Text:</b> {str(exc)}"
                )
                if details:
                    # короткий трейл деталей
                    d = html.escape(details.strip())
                    if len(d) > 800:
                        d = d[:800] + " …"
                    msg += f"\n<pre>{d}</pre>"
                ok, err = _send_text(msg, silent=(True if silent is None else silent), parse_mode="HTML")
                if ok:
                    _mark_sent(sig)
                else:
                    print(f"[notify] send_error failed: {err}")
                return ok
            else:
                # заглушим повтор в кулдауне
                return True
    except Exception as e:
        print(f"[notify] exception in send_error: {repr(e)}")
        return False

# CLI quick test: python3 -m notify "hello"
if __name__ == "__main__":
    import sys
    txt = sys.argv[1] if len(sys.argv) > 1 else "ping"
    ok = send_message(f"🧪 {txt}")
    print("OK" if ok else "ERR")
