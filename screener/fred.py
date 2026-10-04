"""FRED client: latest observation of a series (e.g. DGS10, the 10-year Treasury yield, %)."""
import requests

from screener.config import require

BASE = "https://api.stlouisfed.org/fred/series/observations"


def latest(series_id: str, as_of: str | None = None) -> tuple[str, float]:
    """(date, value) of the most recent non-missing observation on or before as_of."""
    params = {"series_id": series_id, "api_key": require("FRED_KEY"), "file_type": "json",
              "sort_order": "desc", "limit": 30}
    if as_of:
        params["observation_end"] = as_of
    resp = requests.get(BASE, params=params, timeout=30)
    resp.raise_for_status()
    for obs in resp.json()["observations"]:
        if obs["value"] != ".":  # FRED marks holidays/missing as "."
            return obs["date"], float(obs["value"])
    raise RuntimeError(f"No recent observations for FRED series {series_id}")
