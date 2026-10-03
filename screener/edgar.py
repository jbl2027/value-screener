"""Fetch and cache EDGAR companyfacts JSON locally (cache is never committed or sent to Supabase).

The cache lives outside the repo (the repo sits in OneDrive; ~500 raw files would be >2 GB)
and is gzip-compressed. Override the location with VALUE_SCREENER_CACHE.
"""
import gzip
import json
import os
import time
from pathlib import Path

from screener.sec_client import SECClient

_default_cache = Path(os.getenv("LOCALAPPDATA") or Path.home() / ".cache") / "value-screener"
CACHE_DIR = Path(os.getenv("VALUE_SCREENER_CACHE") or _default_cache) / "companyfacts"
CACHE_MAX_AGE_SECONDS = 6 * 24 * 3600  # refetch weekly

# Current CIK -> predecessor CIKs whose XBRL history belongs to the same company
# (e.g. a new holding company took over the ticker). Facts from all CIKs are merged.
PREDECESSOR_CIKS = {
    2115436: [34088],  # XOM: ExxonMobil Holdings Corp (2026) <- Exxon Mobil Corp
}


def related_ciks(cik: int) -> list[int]:
    return [cik] + PREDECESSOR_CIKS.get(cik, [])


def load_companyfacts(cik: int, sec: SECClient, refresh: bool = False) -> dict:
    path = CACHE_DIR / f"CIK{cik:010d}.json.gz"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE_SECONDS:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    data = sec.companyfacts(cik)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f)
    return data
