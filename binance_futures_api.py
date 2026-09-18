import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import requests


class BinanceFuturesAPI:
    """Small USD-M Futures REST client.

    Testnet is the default. API credentials are read only from environment
    variables and are never logged.
    """

    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.testnet = str(os.getenv("BINANCE_TESTNET", "true")).lower() in {"1", "true", "yes"}
        self.base_url = (
            "https://testnet.binancefuture.com"
            if self.testnet
            else "https://fapi.binance.com"
        )
        self.recv_window = 5000
        self.timeout = 20

    def enabled(self):
        return bool(self.api_key and self.api_secret)

    def _request(self, method, path, params=None, signed=False):
        params = dict(params or {})
        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window
            query = urlencode(params, doseq=True)
            signature = hmac.new(
                self.api_secret.encode("utf-8"),
                query.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            params["signature"] = signature

        response = requests.request(
            method,
            f"{self.base_url}{path}",
            params=params,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def ping(self):
        return self._request("GET", "/fapi/v1/ping")

    def exchange_info(self):
        return self._request("GET", "/fapi/v1/exchangeInfo")

    def klines(self, symbol, interval="15m", limit=300):
        return self._request(
            "GET",
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    def mark_price(self, symbol):
        return self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})

    def book_ticker(self, symbol):
        return self._request("GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol})

    def balance(self):
        return self._request("GET", "/fapi/v3/balance", signed=True)

    def positions(self, symbol=None):
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/fapi/v3/positionRisk", params, signed=True)

    def open_orders(self, symbol=None):
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/fapi/v1/openOrders", params, signed=True)

    def cancel_all_orders(self, symbol):
        return self._request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            {"symbol": symbol},
            signed=True,
        )

    def set_leverage(self, symbol, leverage=1):
        return self._request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": int(leverage)},
            signed=True,
        )

    def market_order(self, symbol, side, quantity):
        return self._request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "RESULT",
            },
            signed=True,
        )

    def protective_algo_order(self, symbol, side, trigger_price, close_position=True):
        return self._request(
            "POST",
            "/fapi/v1/algoOrder",
            {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET" if side in {"SELL", "BUY"} else "STOP_MARKET",
                "triggerPrice": trigger_price,
                "closePosition": "true" if close_position else "false",
                "workingType": "MARK_PRICE",
            },
            signed=True,
        )
