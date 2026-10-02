"""Rate-limited HTTP client for SEC EDGAR (limit: 10 requests/sec, User-Agent required)."""
import threading
import time

import requests

from screener.config import require

MAX_REQUESTS_PER_SEC = 8  # stay safely under SEC's 10/sec ceiling
RETRY_STATUSES = {429, 500, 502, 503, 504}


class SECClient:
    def __init__(self, max_per_sec: float = MAX_REQUESTS_PER_SEC, max_retries: int = 5):
        self._min_interval = 1.0 / max_per_sec
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": require("SEC_USER_AGENT"),
            "Accept-Encoding": "gzip, deflate",
        })

    def _throttle(self) -> None:
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def get_json(self, url: str) -> dict:
        for attempt in range(self._max_retries):
            self._throttle()
            resp = self._session.get(url, timeout=30)
            if resp.status_code in RETRY_STATUSES:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        raise RuntimeError(f"SEC request failed after {self._max_retries} attempts: {url}")

    def company_tickers(self) -> dict:
        return self.get_json("https://www.sec.gov/files/company_tickers.json")

    def submissions(self, cik: int) -> dict:
        return self.get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")

    def companyfacts(self, cik: int) -> dict:
        return self.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
