"""Tiingo client (free Starter tier: 50 requests/hour, 1,000/day, 500 unique symbols/month,
1 GB/month bandwidth, internal use only).

Daily prices come from the bulk IEX snapshot: one request returns the latest top-of-book for
every ticker (~37k, ~11 MB). After the close its `tngoLast` equals the official closing price
(verified against the EOD endpoint). The per-ticker EOD endpoint counts against the unique
symbol cap, so it is only used for spot checks and targeted adjusted-price history.
"""
from datetime import date

import requests

from screener.config import require

BASE = "https://api.tiingo.com"


class TiingoClient:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Token {require('TIINGO_KEY')}",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip, deflate",
        })

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        resp = self._session.get(f"{BASE}{path}", params=params, timeout=120)
        if resp.status_code == 429:
            raise RuntimeError("Tiingo rate limit reached (free tier: 50/hour, 1,000/day)")
        resp.raise_for_status()
        return resp.json()

    def iex_snapshot(self) -> list[dict]:
        """Latest top-of-book/last for all tickers in one request."""
        return self._get("/iex")

    def eod_prices(self, ticker: str, start: date, end: date | None = None) -> list[dict]:
        """Daily OHLCV with adjusted fields, splitFactor, and divCash. Uses one unique symbol."""
        params = {"startDate": start.isoformat()}
        if end:
            params["endDate"] = end.isoformat()
        return self._get(f"/tiingo/daily/{ticker}/prices", params)
