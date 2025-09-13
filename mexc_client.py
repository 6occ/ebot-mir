#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from typing import Optional, Dict, Any, List, Tuple

from config import MEXC_API_URL, API_KEY, API_SECRET

class MexcHTTPError(Exception):
    pass

def _now_ms() -> int:
    return int(time.time() * 1000)

def _fmt_num(x: float) -> str:
    # Нормализация чисел для API (без экспоненты, без хвостовых нулей)
    s = f"{float(x):.12f}".rstrip("0").rstrip(".")
    return s if s else "0"

class MexcClient:
    """
    Лёгкий клиент для MEXC:
      - HTTP keep-alive через requests.Session()
      - базовый экспоненциальный ретрай на сетевые ошибки/5xx
      - мини-кэш для ticker/price (2 сек)
    Реализованы только необходимые боту методы.
    """
    def __init__(self,
                 api_key: Optional[str] = None,
                 api_secret: Optional[str] = None,
                 base_url: Optional[str] = None,
                 timeout_sec: float = 15.0,
                 max_retries: int = 2):
        self.base_url   = (base_url or MEXC_API_URL).rstrip("/")
        self.api_key    = api_key    or API_KEY
        self.api_secret = api_secret or API_SECRET
        self.timeout    = float(timeout_sec)
        self.max_retries= int(max_retries)
        self._sess      = requests.Session()

        if not self.base_url.startswith("http"):
            raise ValueError("MEXC base url is invalid")

        # мини-кэш тикера: {symbol: (ts, price)}
        self._price_cache: Dict[str, Tuple[float, float]] = {}

    # ---------------- low-level ----------------
    def _sign(self, params: Dict[str, Any]) -> str:
        query = urlencode(params, doseq=True)
        return hmac.new(self.api_secret.encode("utf-8"),
                        query.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def _headers(self, signed: bool = False) -> Dict[str, str]:
        h = {
            "Accept": "application/json",
            "Content-Type": "application/json;charset=utf-8",
            "User-Agent": "Ebot/1.1",
        }
        if signed:
            if not self.api_key:
                raise MexcHTTPError("Signed request requires API_KEY")
            h["X-MEXC-APIKEY"] = self.api_key
        return h

    def _request(self, method: str, path: str,
                 params: Dict[str, Any] = None,
                 json: Dict[str, Any] = None,
                 signed: bool = False) -> Any:
        url    = f"{self.base_url}{path}"
        params = dict(params or {})

        if signed:
            # добавляем обязательные поля
            params.setdefault("timestamp", _now_ms())
            params.setdefault("recvWindow", 60_000)
            params["signature"] = self._sign(params)

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._sess.request(method, url,
                                       params=params,
                                       json=json,
                                       headers=self._headers(signed),
                                       timeout=self.timeout)
            except requests.RequestException as e:
                last_exc = e
                # backoff: 0.3s, 0.9s...
                time.sleep(0.3 * (3 ** attempt))
                continue

            # parse json
            try:
                data = r.json()
            except ValueError:
                data = {"raw": r.text}

            if r.status_code == 200:
                # some endpoints may return {"code":..,"msg":..} even with 200
                if isinstance(data, dict) and "code" in data and str(data["code"]) not in ("0", 0):
                    # ошибки API (например, 30002 — минимальный объём)
                    raise MexcHTTPError(f"{method} {path} API error: {data}")
                return data

            # 429/5xx — ретраим
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                time.sleep(0.3 * (3 ** attempt))
                continue

            # прочее — сразу ошибка
            raise MexcHTTPError(f"{method} {path} {r.status_code}: {data}")

        # если не вернулись — сеть падала все попытки
        raise MexcHTTPError(f"{method} {path} request failed: {last_exc}")

    # ---------------- public ----------------
    def price(self, symbol: str) -> float:
        # мини-кэш 2 секунды
        now = time.time()
        ts_p = self._price_cache.get(symbol)
        if ts_p and (now - ts_p[0] <= 2.0):
            return ts_p[1]

        data = self._request("GET", "/api/v3/ticker/price", params={"symbol": symbol}, signed=False)
        price = float(data.get("price", 0.0))
        self._price_cache[symbol] = (now, price)
        return price

    # ---------------- signed ----------------
    def account(self) -> dict:
        return self._request("GET", "/api/v3/account", params={}, signed=True)

    def open_orders(self, symbol: str, limit: int = None) -> list:
        params = {"symbol": symbol}
        if limit:
            params["limit"] = int(limit)
        return self._request("GET", "/api/v3/openOrders", params=params, signed=True)

    def my_trades(self, symbol: str, startTime: int = None, endTime: int = None, limit: int = 1000) -> list:
        params = {"symbol": symbol, "limit": int(limit)}
        if startTime:
            params["startTime"] = int(startTime)
        if endTime:
            params["endTime"]   = int(endTime)
        data = self._request("GET", "/api/v3/myTrades", params=params, signed=True)
        return data if isinstance(data, list) else []

    def place_order(self, symbol: str, side: str, price: float, qty: float, tif: str = "GTC") -> dict:
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

if __name__ == "__main__":
    # Быстрая ручная проверка
    from config import PAIR
    c = MexcClient()
    print("LAST:", c.price(PAIR))
    try:
        acct = c.account()
        usdc = next((float(b["free"]) for b in acct.get("balances", []) if b.get("asset") == "USDC"), 0.0)
        print("USDC balance:", usdc)
        print("Keys OK")
    except Exception as e:
        print("Account error:", e)
