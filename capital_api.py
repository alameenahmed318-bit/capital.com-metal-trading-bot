import requests
import config
import time
from collections import deque

SESSION_URL = "/api/v1/session"


class CapitalAPI:
    def __init__(self):
        self.base_url = config.CAPITAL_BASE_URL.rstrip("/")
        self.cst = None
        self.security_token = None
        self.account_id = None
        # Capital.com applies the REST limit per user/account, not per
        # GitHub Actions process. Four Capital strategy workflows can run
        # concurrently, so each process is capped at 2 req/s (8 req/s worst
        # case across V1-V4), leaving headroom below the documented 10 req/s
        # account-wide ceiling.
        self._request_times = deque()
        self._max_requests_per_second = 2

    def _throttle(self):
        now = time.monotonic()
        while self._request_times and now - self._request_times[0] >= 1.0:
            self._request_times.popleft()
        if len(self._request_times) >= self._max_requests_per_second:
            wait = max(0.0, 1.0 - (now - self._request_times[0]) + 0.01)
            time.sleep(wait)
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 1.0:
                self._request_times.popleft()
        self._request_times.append(time.monotonic())

    def _headers(self):
        return {
            "X-CAP-API-KEY": config.CAPITAL_API_KEY,
            "Content-Type": "application/json",
            "CST": self.cst or "",
            "X-SECURITY-TOKEN": self.security_token or "",
        }

    def login(self):
        self._throttle()
        resp = requests.post(
            f"{self.base_url}{SESSION_URL}",
            headers={
                "X-CAP-API-KEY": config.CAPITAL_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "identifier": config.CAPITAL_EMAIL,
                "password": config.CAPITAL_PASSWORD,
                "encryptedPassword": False,
            },
            timeout=20,
        )

        resp.raise_for_status()

        self.cst = resp.headers["CST"]
        self.security_token = resp.headers["X-SECURITY-TOKEN"]

        accounts = self.get_accounts()

        if not accounts:
            raise RuntimeError("No Capital.com accounts were returned.")

        active_accounts = [
            account
            for account in accounts
            if account.get("preferred") is True
        ]

        if active_accounts:
            self.account_id = active_accounts[0]["accountId"]
        else:
            self.account_id = accounts[0]["accountId"]

        print(f"Using Capital.com account: {self.account_id}")

        return resp.json()

    def _request(self, method, path, **kwargs):
        # Retry once after refreshing the 10-minute Capital.com session.
        # A bounded retry prevents recursive login loops on persistent 401s.
        for attempt in range(2):
            self._throttle()
            resp = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                timeout=20,
                **kwargs,
            )
            if resp.status_code != 401:
                break
            if attempt == 1:
                break
            self.login()

        resp.raise_for_status()

        if not resp.content:
            return {}

        return resp.json()

    def get_accounts(self):
        self._throttle()
        resp = requests.get(
            f"{self.base_url}/api/v1/accounts",
            headers=self._headers(),
            timeout=20,
        )

        resp.raise_for_status()

        return resp.json()["accounts"]

    def get_account_currency(self):
        """Return the selected account's base currency (e.g. USD or AED)."""
        accounts = self.get_accounts()
        account = next(
            (a for a in accounts if a["accountId"] == self.account_id),
            None,
        )
        if account is None:
            raise RuntimeError(f"Selected account {self.account_id} was not found.")
        currency = account.get("balance", {}).get("currency") or account.get("currency")
        if not currency:
            raise RuntimeError("Capital.com did not return the account currency.")
        return str(currency).upper()

    def get_balance(self):
        accounts = self.get_accounts()

        account = next(
            (a for a in accounts if a["accountId"] == self.account_id),
            None,
        )

        if account is None:
            raise RuntimeError(
                f"Selected account {self.account_id} was not found."
            )

        return account["balance"]["balance"]

    def get_market(self, epic):
        return self._request(
            "GET",
            f"/api/v1/markets/{epic}",
        )

    def get_candles(
        self,
        epic,
        resolution=None,
        max_candles=None,
        from_date=None,
        to_date=None,
    ):
        resolution = resolution or config.RESOLUTION
        max_candles = max_candles or config.CANDLE_COUNT

        params = {
            "resolution": resolution,
            "max": max_candles,
        }

        if from_date:
            params["from"] = from_date

        if to_date:
            params["to"] = to_date

        return self._request(
            "GET",
            f"/api/v1/prices/{epic}",
            params=params,
        )

    def get_open_positions(self):
        return self._request(
            "GET",
            "/api/v1/positions",
        )["positions"]

    def place_working_order(self, epic, direction, size, level, stop_level=None, profit_level=None):
        payload = {"epic": epic, "direction": direction, "size": size, "type": "STOP", "level": level, "guaranteedStop": False}
        if stop_level is not None: payload["stopLevel"] = stop_level
        if profit_level is not None: payload["profitLevel"] = profit_level
        return self._request("POST", "/api/v1/workingorders", json=payload)

    def place_order(
        self,
        direction,
        size,
        stop_level,
        profit_level,
        epic,
    ):
        payload = {
            "epic": epic,
            "direction": direction,
            "size": size,
            "guaranteedStop": False,
            "stopLevel": stop_level,
        }
        if profit_level is not None:
            payload["profitLevel"] = profit_level
        return self._request("POST", "/api/v1/positions", json=payload)

    def modify_position(
        self,
        deal_id,
        stop_level=None,
        profit_level=None,
    ):
        """
        Modify an existing Capital.com position.

        Only values supplied by the caller are changed.
        This is used by the trailing-stop manager.
        """

        payload = {}

        if stop_level is not None:
            payload["stopLevel"] = stop_level

        if profit_level is not None:
            payload["profitLevel"] = profit_level

        if not payload:
            raise ValueError("Nothing to modify.")

        return self._request(
            "PUT",
            f"/api/v1/positions/{deal_id}",
            json=payload,
        )

    def close_position(self, deal_id):
        return self._request(
            "DELETE",
            f"/api/v1/positions/{deal_id}",
        )

    def get_confirmation(self, deal_reference):
        return self._request(
            "GET",
            f"/api/v1/confirms/{deal_reference}",
        )

    def get_deal_activity(self, deal_id):
        return self._request(
            "GET",
            "/api/v1/history/activity",
            params={
                "dealId": deal_id,
                "detailed": "true",
                "lastPeriod": 86400,
            },
        ).get("activities", [])

    def get_deal_activity_window(self, from_date, to_date):
        """Return detailed account activity for a maximum one-day window."""
        return self._request(
            "GET",
            "/api/v1/history/activity",
            params={
                "from": from_date,
                "to": to_date,
                "detailed": "true",
            },
        ).get("activities", [])

    def get_transactions(
        self,
        from_date,
        to_date,
        tx_type=None,
    ):
        params = {
            "from": from_date,
            "to": to_date,
        }

        if tx_type:
            params["type"] = tx_type

        return self._request(
            "GET",
            "/api/v1/history/transactions",
            params=params,
        ).get("transactions", [])


if __name__ == "__main__":
    api = CapitalAPI()

    print("Logging in...")
    print(api.login())

    print("Balance:", api.get_balance())

    print(
        "Candles:",
        api.get_candles(config.EPICS[0]),
    )
