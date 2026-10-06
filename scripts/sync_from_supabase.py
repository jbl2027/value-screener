"""Rebuild local pipeline inputs from Supabase (used by GitHub Actions, whose runners start empty).

Writes data/universe.csv, data/facts.parquet, data/fundamentals_summary.csv, data/prices.parquet
in the same shapes the pipeline scripts produce, so they run unchanged.

Usage: python scripts/sync_from_supabase.py [universe] [facts] [prices] [llm]   (default: all)
"""
import io
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.db import connect  # noqa: E402
from screener.llm import MODEL, PROMPT_VERSION  # noqa: E402
from screener.llm_store import latest_scores  # noqa: E402


def query_csv(conn, sql: str) -> pd.DataFrame:
    """COPY a query out as CSV (fast for ~1M rows) and read it with pandas."""
    buf = io.BytesIO()
    with conn.cursor() as cur:
        with cur.copy(f"copy ({sql}) to stdout with (format csv, header true)") as cp:
            for chunk in cp:
                buf.write(chunk)
    buf.seek(0)
    return pd.read_csv(buf, dtype={"period_start": str, "period_end": str, "filed": str, "date": str,
                                   "fp": str, "form": str, "accn": str, "fetched_at": str})


def sync_universe(conn) -> int:
    u = query_csv(conn, "select ticker, cik, name, sec_name, sic, sic_description, sic_division, sector_model, "
                        "sector_model_source, captive_finance, gics_sector, gics_industry_group, "
                        "gics_sub_industry, fiscal_year_end from universe order by ticker")
    u["captive_finance"] = u["captive_finance"].map({"t": True, "f": False, True: True, False: False})
    u.to_csv(DATA_DIR / "universe.csv", index=False)
    return len(u)


def sync_facts(conn) -> int:
    f = query_csv(conn, "select cik, source_cik, concept, tag, value, period_start, period_end, duration_days, "
                        "fy, fp, form, filed, accn from facts")
    f.to_parquet(DATA_DIR / "facts.parquet", index=False)
    s = pd.DataFrame([r[0] for r in conn.execute("select detail from fundamentals_summary").fetchall()])
    s.to_csv(DATA_DIR / "fundamentals_summary.csv", index=False)
    return len(f)


def sync_prices(conn) -> int:
    p = query_csv(conn, "select ticker, date, close, open, high, low, volume, prev_close, source, fetched_at "
                        "from prices order by ticker, date")
    p.to_parquet(DATA_DIR / "prices.parquet", index=False)
    return len(p)


def sync_llm(conn) -> int:
    """Latest Gemini score per company for the current model and prompt version (feeds the
    qualitative gate in compute_valuations.py and, once approved, the composite)."""
    df = latest_scores(conn, MODEL, PROMPT_VERSION)
    df.to_csv(DATA_DIR / f"llm_scores_{PROMPT_VERSION}.csv", index=False)
    return len(df)


SYNCS = {"universe": sync_universe, "facts": sync_facts, "prices": sync_prices, "llm": sync_llm}


def main() -> None:
    targets = sys.argv[1:] or list(SYNCS)
    DATA_DIR.mkdir(exist_ok=True)
    with connect() as conn:
        for t in targets:
            print(f"synced {t}: {SYNCS[t](conn):,} rows", flush=True)


if __name__ == "__main__":
    main()
