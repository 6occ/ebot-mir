#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import hmac
import hashlib
import random
import requests
from urllib.parse import urlencode

from config import MEXC_API_URL, API_KEY, API_SECRET, PAIR

# --- настройки HTTP/Retry (можно вынести в config при желании) ---
HTTP_TIMEOUT_SEC      = 15          # базовый timeout одного запроса
RETRY_MAX_ATTEMPTS    = 3           # всего попыток (1 + 2 повтора)
RETRY_BACKOFF_BASE    = 0.6         # старт задержки перед повтором, сек
RETRY_BACKOFF_JITTER  = 0.25        # случайная примесь к задержке

class MexcHTTPError(Exception):
    """Исключение уровня транспорта/HTTP/API."""
    pass

def _now_ms() -> int:
    return int(time.time() * 1000)

def _fmt_num(x: float) -> str:
    # нормализуем числа для API (без экспоненты, с обрезанными нулями)
    s = f"{float(x):.12f}"
    s = s.rstrip("0").rstrip(".")
    return s if s else "0"

def _is_retriable_status(status_code: int) -> bool:
    # 5xx — временные; 429 — rate limit
    return status_code >= 500 or status_code == 429

def _is_retriable_api_payload(payload) -> bool:
    """
    Иногда API возвращает JSON с code/msg и HTTP 200, но это 'временная' ошибка.
    На практике MEXC бизнес-ошибки (30005 Oversold и пр.) ретраить нельзя.
    Если потребуется, добавим сюда распознавание временных кодов.
    """
    return False

class MexcClient:
    def __init__(self, api_key: str = None, api_secret: str = None, base_url: str = None,
                 timeout: int = HTTP_TIMEOUT_SEC):
        self.base_url   = (base_url or MEXC_API_URL).rstrip("/")
        self.api_key    = api_key   or API_KEY
        self.api_secret = api_secret or API_SECRET
        self.timeout    = int(timeout) if timeout else HTTP_TIMEOUT_SEC

        if not self.base_url.startswith("http"):
            raise ValueError("MEXC base url is invalid")

        # Держим сессию для keep-alive и минимизации накладных расходов
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json;charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "Ebot/1.0",
        })
        # Ключ добавляем динамически только для signed-запросов

    # ---------------- low-level ----------------
    def _sign(self, params: dict) -> str:
        # Подпись HMAC SHA256 по QUERYSTRING
        query = urlencode(params, doseq=True)
        return hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()

    def _headers(self, signed: bool = False) -> dict:
        h = {}
        if signed:
            if not self.api_key:
                raise MexcHTTPError("Signed request requires API_KEY")
            h["X-MEXC-APIKEY"] = self.api_key
        return h

    def _sleep_backoff(self, attempt: int):
        # экспоненциальный бэкофф + лёгкий джиттер
        delay = (RETRY_BACKOFF_BASE * (2 ** (attempt - 1))) + random.uniform(0, RETRY_BACKOFF_JITTER)
        time.sleep(delay)

    def _request(self, method: str, path: str, params: dict = None, json: dict = None, signed: bool = False):
        url = f"{self.base_url}{path}"
        params = dict(params or {})

        if signed:
            if "timestamp" not in params:
                params["timestamp"] = _now_ms()
            if "recvWindow" not in params:
                params["recvWindow"] = 60_000
            params["signature"] = self._sign(params)

        last_exc = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                r = self._session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=self._headers(signed),
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                # сетевые/таймаут — пробуем повторить
                last_exc = e
                if attempt < RETRY_MAX_ATTEMPTS:
                    self._sleep_backoff(attempt)
                    continue
                raise MexcHTTPError(f"{method} {path} request failed: {e}")

            # Пытаемся распарсить JSON
            try:
                data = r.json()
            except ValueError:
                data = {"raw": r.text}

            # HTTP слой
            if r.status_code != 200:
                # если ретраибельно — повторим
                if _is_retriable_status(r.status_code) and attempt < RETRY_MAX_ATTEMPTS:
                    self._sleep_backoff(attempt)
                    continue
                raise MexcHTTPError(f"{method} {path} {r.status_code}: {data}")

            # Уровень API (200 OK, но ошибка в теле)
            if isinstance(data, dict) and "code" in data and data["code"] not in (0, "0"):
                # бизнес-ошибки НЕ ретраим (например Oversold/30005)
                # но если захотим ловить временные — используем _is_retriable_api_payload
                if _is_retriable_api_payload(data) and attempt < RETRY_MAX_ATTEMPTS:
                    self._sleep_backoff(attempt)
                    continue
                raise MexcHTTPError(f"{method} {path} API error: {data}")

            return data

        # сюда не дойдём (return или raise раньше), но на всякий случай:
        if last_exc:
            raise MexcHTTPError(f"{method} {path} failed: {last_exc}")
        raise MexcHTTPError(f"{method} {path} failed: unknown error")

    # ---------------- public ----------------
    def price(self, symbol: str) -> float:
        data = self._request("GET", "/api/v3/ticker/price", params={"symbol": symbol}, signed=False)
        # Ответ вида {'symbol': 'KASUSDC', 'price': '0.086142'}
        return float(data.get("price", 0.0))

    def exchange_info(self, symbol: str = None) -> dict:
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/api/v3/exchangeInfo", params=params, signed=False)

    # ---------------- signed ----------------
    def account(self) -> dict:
        # Балансы/лимиты
        return self._request("GET", "/api/v3/account", params={}, signed=True)

    def open_orders(self, symbol: str, limit: int = None) -> list:
        params = {"symbol": symbol}
        if limit:
            params["limit"] = int(limit)
        data = self._request("GET", "/api/v3/openOrders", params=params, signed=True)
        return data if isinstance(data, list) else []

    def my_trades(self, symbol: str, startTime: int = None, endTime: int = None, limit: int = 1000) -> list:
        params = {"symbol": symbol, "limit": int(limit)}
        if startTime:
            params["startTime"] = int(startTime)
        if endTime:
            params["endTime"] = int(endTime)
        data = self._request("GET", "/api/v3/myTrades", params=params, signed=True)
        return data if isinstance(data, list) else []

    def place_order(self, symbol: str, side: str, price: float, qty: float, tif: str = "GTC") -> dict:
        """
        Лимитный ордер (как мы используем в боте):
        - type=LIMIT
        - timeInForce: GTC
        """
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "LIMIT",
            "timeInForce": tif,
            "price": _fmt_num(price),
            "quantity": _fmt_num(qty),
        }
        return self._request("POST", "/api/v3/order", params=params, signed=True)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        params = {"symbol": symbol, "orderId": order_id}
        return self._request("DELETE", "/api/v3/order", params=params, signed=True)


# Быстрая проверка модуля (локально)
if __name__ == "__main__":
    c = MexcClient()
    print("LAST:", c.price(PAIR))
    try:
        a = c.account()
        usdc = next((float(b.get("free", 0)) for b in a.get("balances", []) if b.get("asset") == "USDC"), 0.0)
        print("USDC balance:", usdc)
        print("Keys OK")
    except Exception as e:
        print("Account error:", e)
