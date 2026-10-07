"""Milestone 9: load local pipeline outputs into Supabase Postgres.

Usage: python scripts/load_supabase.py [universe] [facts] [prices] [results]   (default: all)
  universe  data/universe.csv                -> universe (upsert; removed tickers deleted)
  facts     data/facts.parquet, data/fundamentals_summary.csv
                                             -> facts (replaced atomically, with superseded_date),
                                                fundamentals_summary (replaced)
  prices    data/prices.parquet              -> prices (upsert)
  results   data/metrics.csv, valuations.csv, scores.csv, valuation_fits.csv
                                             -> *_latest (replaced), screen_history (upsert for as_of)
Each invocation is logged in the runs table.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.db import connect  # noqa: E402


def clean(v):
    """Python value suitable for Postgres: NaN/NaT -> None, numpy scalars -> Python."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def as_bool(v):
    v = clean(v)
    return None if v is None else str(v).strip().lower() == "true"


def detail(row: pd.Series) -> Jsonb:
    return Jsonb({k: clean(v) for k, v in row.items()}, dumps=lambda o: json.dumps(o, default=str))


def copy_rows(cur, table: str, cols: list[str], rows) -> int:
    n = 0
    with cur.copy(f"copy {table} ({', '.join(cols)}) from stdin") as cp:
        for r in rows:
            cp.write_row([clean(v) for v in r])
            n += 1
    return n


# ---------- universe ----------
def load_universe(conn) -> dict:
    u = pd.read_csv(DATA_DIR / "universe.csv")
    cols = ["ticker", "cik", "name", "sec_name", "sic", "sic_description", "sic_division", "sector_model",
            "sector_model_source", "captive_finance", "gics_sector", "gics_industry_group", "gics_sub_industry",
            "fiscal_year_end"]
    u["captive_finance"] = u["captive_finance"].map(as_bool)
    u["fiscal_year_end"] = u["fiscal_year_end"].map(lambda x: None if pd.isna(x) else f"{int(x):04d}")
    with conn.cursor() as cur:
        cur.execute("create temp table stage (like universe including defaults) on commit drop")
        copy_rows(cur, "stage", cols, u[cols].itertuples(index=False))
        cur.execute(f"""insert into universe ({', '.join(cols)})
                        select {', '.join(cols)} from stage
                        on conflict (ticker) do update set
                        {', '.join(f'{c} = excluded.{c}' for c in cols[1:])}, updated_at = now()""")
        cur.execute("delete from universe where ticker not in (select ticker from stage)")
        removed = cur.rowcount
    return {"universe_rows": len(u), "universe_removed": removed}


# ---------- facts ----------
FACT_COLS = ["cik", "source_cik", "concept", "tag", "value", "period_start", "period_end", "duration_days",
             "fy", "fp", "form", "filed", "accn", "superseded_date"]


def with_superseded(f: pd.DataFrame) -> pd.DataFrame:
    """superseded_date = filing date of the next different value reported for the same period."""
    f = f.sort_values(["cik", "concept", "period_start", "period_end", "filed", "accn"]).copy()
    key = ["cik", "concept", "period_start", "period_end"]
    f["superseded_date"] = f.groupby(key, dropna=False)["filed"].shift(-1)
    return f


def load_facts(conn) -> dict:
    f = with_superseded(pd.read_parquet(DATA_DIR / "facts.parquet"))
    for c in ("duration_days", "fy"):
        f[c] = f[c].astype("Int64")
    s = pd.read_csv(DATA_DIR / "fundamentals_summary.csv")
    s_cols = ["cik", "tickers", "sector_model", "has_10k", "latest_10k_filed", "fy_end", "latest_10k_accn",
              "stale_10k", "fair_value_ready", "fair_value_missing"]
    for c in ("has_10k", "stale_10k", "fair_value_ready"):
        s[c] = s[c].map(as_bool)
    with conn.cursor() as cur:
        cur.execute("create temp table stage (like facts) on commit drop")
        n = copy_rows(cur, "stage", FACT_COLS, f[FACT_COLS].itertuples(index=False))
        cur.execute("truncate facts")
        cur.execute(f"insert into facts ({', '.join(FACT_COLS)}) select {', '.join(FACT_COLS)} from stage")
        cur.execute("truncate fundamentals_summary")
        for _, r in s.iterrows():
            cur.execute(f"insert into fundamentals_summary ({', '.join(s_cols)}, detail) "
                        f"values ({', '.join(['%s'] * (len(s_cols) + 1))})",
                        [clean(r[c]) for c in s_cols] + [detail(r)])
    return {"facts_rows": n, "facts_restated": int(f["superseded_date"].notna().sum()), "summary_rows": len(s)}


# ---------- prices ----------
PRICE_COLS = ["ticker", "date", "close", "open", "high", "low", "volume", "prev_close", "source", "fetched_at"]


def load_prices(conn) -> dict:
    p = pd.read_parquet(DATA_DIR / "prices.parquet")
    p["volume"] = p["volume"].astype("Int64")
    with conn.cursor() as cur:
        cur.execute("create temp table stage (like prices) on commit drop")
        n = copy_rows(cur, "stage", PRICE_COLS, p[PRICE_COLS].itertuples(index=False))
        cur.execute(f"""insert into prices ({', '.join(PRICE_COLS)}) select {', '.join(PRICE_COLS)} from stage
                        on conflict (ticker, date) do update set
                        {', '.join(f'{c} = excluded.{c}' for c in PRICE_COLS[2:])}""")
    return {"price_rows": n, "price_sessions": int(p["date"].nunique()), "latest_session": p["date"].max()}


# ---------- results ----------
VAL_COLS = ["cik", "ticker", "as_of", "name", "sector_model", "gics_sector", "gics_industry_group", "status",
            "status_reason", "confidence", "confidence_reasons", "warnings", "disqualify_reasons", "missing_inputs",
            "price", "fair_value", "mos", "method", "company_multiple", "peer_multiple", "fair_multiple",
            "peer_group", "peer_count", "base_value", "base_basis", "earnings_yield", "treasury_yield",
            "qualitative_gate", "qualitative_reasons", "qualitative_detectors"]
SCORE_COLS = ["cik", "ticker", "as_of", "composite", "composite_confidence", "composite_rank",
              "components_available", "components_missing", "composite_note", "valuation_score",
              "productivity_score", "earnings_quality_score", "momentum_score", "llm_score", "growth_score",
              "shareholder_yield_score"]


def replace_table(cur, table: str, cols: list[str], df: pd.DataFrame) -> None:
    cur.execute(f"truncate {table}")
    sql = f"insert into {table} ({', '.join(cols)}, detail) values ({', '.join(['%s'] * (len(cols) + 1))})"
    cur.executemany(sql, [[clean(r[c]) if c in r else None for c in cols] + [detail(r)] for _, r in df.iterrows()])


def load_results(conn) -> dict:
    m = pd.read_csv(DATA_DIR / "metrics.csv")
    v = pd.read_csv(DATA_DIR / "valuations.csv")
    s = pd.read_csv(DATA_DIR / "scores.csv")
    as_of = str(v["as_of"].iloc[0])
    v["qualitative_gate"] = v["qualitative_gate"].map(as_bool)
    for c in ("peer_count", "composite_rank", "components_available"):
        for df in (v, s):
            if c in df:
                df[c] = df[c].astype("Int64")
    with conn.cursor() as cur:
        replace_table(cur, "metrics_latest", ["cik", "ticker", "as_of", "sector_model"], m)
        replace_table(cur, "valuations_latest", VAL_COLS, v)
        replace_table(cur, "scores_latest", SCORE_COLS, s)
        hist = v[["as_of", "cik", "ticker", "status", "confidence", "price", "fair_value", "mos"]].merge(
            s[["cik", "composite"]], on="cik", how="left")
        cur.execute("delete from screen_history where as_of = %s", (as_of,))
        copy_rows(cur, "screen_history", list(hist.columns), hist.itertuples(index=False))
    fits = pd.read_csv(DATA_DIR / "valuation_fits.csv")
    regressions = fits[fits["peer_level"] == "regression"][["model", "r2", "n", "slope", "intercept"]]
    return {"as_of": as_of, "status_counts": v["status"].value_counts().to_dict(),
            "composites": int(s["composite"].notna().sum()),
            "regressions": [{k: clean(x) for k, x in r.items()} for r in regressions.to_dict("records")]}


LOADERS = {"universe": load_universe, "facts": load_facts, "prices": load_prices, "results": load_results}


def main() -> None:
    targets = sys.argv[1:] or list(LOADERS)
    unknown = set(targets) - set(LOADERS)
    if unknown:
        sys.exit(f"unknown targets: {sorted(unknown)}; choose from {list(LOADERS)}")
    with connect() as conn:
        run_id = conn.execute("insert into runs (job) values (%s) returning id",
                              (f"load:{'+'.join(targets)}",)).fetchone()[0]
        conn.commit()
        summary = {}
        try:
            for t in targets:
                with conn.transaction():
                    summary[t] = LOADERS[t](conn)
                print(f"loaded {t}: {summary[t]}", flush=True)
            summary["db_size"] = conn.execute("select pg_size_pretty(pg_database_size('postgres'))").fetchone()[0]
            as_of = summary.get("results", {}).get("as_of")
            conn.execute("update runs set status = 'ok', finished_at = now(), as_of = %s, summary = %s where id = %s",
                         (as_of, Jsonb(summary, dumps=lambda o: json.dumps(o, default=str)), run_id))
            conn.commit()
            print(f"database size: {summary['db_size']}")
        except Exception as exc:
            conn.rollback()
            conn.execute("update runs set status = 'failed', finished_at = now(), summary = %s where id = %s",
                         (Jsonb({"error": f"{type(exc).__name__}: {exc}"[:500], **summary},
                                dumps=lambda o: json.dumps(o, default=str)), run_id))
            conn.commit()
            raise


if __name__ == "__main__":
    main()
