"""Fetch and cache EDGAR companyfacts JSON locally (cache is gitignored, never sent to Supabase)."""
import json
import time

from screener.config import DATA_DIR
from screener.sec_client import SECClient

CACHE_DIR = DATA_DIR / "cache" / "companyfacts"
CACHE_MAX_AGE_SECONDS = 6 * 24 * 3600  # refetch weekly

# Current CIK -> predecessor CIKs whose XBRL history belongs to the same company
# (e.g. a new holding company took over the ticker). Facts from all CIKs are merged.
PREDECESSOR_CIKS = {
    2115436: [34088],  # XOM: ExxonMobil Holdings Corp (2026) <- Exxon Mobil Corp
}


def related_ciks(cik: int) -> list[int]:
    return [cik] + PREDECESSOR_CIKS.get(cik, [])


def load_companyfacts(cik: int, sec: SECClient, refresh: bool = False) -> dict:
    path = CACHE_DIR / f"CIK{cik:010d}.json"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE_SECONDS:
        return json.loads(path.read_text(encoding="utf-8"))
    data = sec.companyfacts(cik)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data
