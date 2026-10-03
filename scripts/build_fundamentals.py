"""Milestone 3: point-in-time EDGAR fundamentals for the full universe, with a mapping report.

Outputs:
  data/facts.parquet             all point-in-time facts (gitignored; loaded to Supabase in M9)
  data/fundamentals_summary.csv  one row per company: latest 10-K, staleness, fair-value readiness
  data/mapping_report.csv        one row per (company, missing input): what failed to map and why
"""
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.fundamentals import extract_facts, latest_annual_snapshot  # noqa: E402
from screener.rules import (  # noqa: E402
    DEBT, EBIT, METRIC_INPUTS, MODEL_RULES, SHARES, derived_concepts, met_by, missing_for,
)
from screener.sec_client import SECClient  # noqa: E402

WORKERS = 4  # downloads overlap; SECClient's shared throttle still caps requests/sec
STALE_AFTER_DAYS = 455  # latest 10-K fiscal year end older than ~15 months
EBIT_YEARS_NEEDED = 3


def annual_period_ends(facts: pd.DataFrame, concept: str) -> set[str]:
    """Fiscal-year period ends for a concept (10-K filings only; 10-Qs can carry TTM figures)."""
    f = facts[(facts["concept"] == concept) & facts["duration_days"].between(350, 380)
              & facts["form"].str.startswith("10-K")]
    return set(f["period_end"])


def ebit_years(facts: pd.DataFrame) -> int:
    """How many of the last 3 fiscal years have annual EBIT under any of the EBIT alternatives."""
    years = sorted(annual_period_ends(facts, "net_income"))[-EBIT_YEARS_NEEDED:]
    have = {c: annual_period_ends(facts, c) for alt in EBIT for c in alt}
    return sum(1 for y in years if any(all(y in have[c] for c in alt) for alt in EBIT))


def process(cik: int, tickers: list[str], model: str, sec: SECClient) -> tuple[pd.DataFrame, dict, list[dict]]:
    base = {"cik": cik, "tickers": " ".join(tickers), "sector_model": model}
    try:
        facts = extract_facts(cik, sec)
    except Exception as exc:
        return pd.DataFrame(), {**base, "error": f"{type(exc).__name__}: {exc}"[:200]}, []

    info, snap = latest_annual_snapshot(facts)
    values = snap["value"].to_dict() if len(snap) else {}
    available = set(values) | derived_concepts(cik, values)
    rules = MODEL_RULES[model]
    gaps = []

    for scope, metrics in [("metric", rules["metrics"])]:
        for metric in metrics:
            for key, requirement in METRIC_INPUTS[metric].items():
                for concept in missing_for(requirement, available):
                    gaps.append({"cik": cik, "tickers": base["tickers"], "sector_model": model,
                                 "scope": scope, "item": metric, "input": key, "missing": concept})

    fv_missing = []
    for key, requirement in rules["fair_value_inputs"].items():
        for concept in missing_for(requirement, available):
            fv_missing.append(f"{key}:{concept}")
    if model == "standard":
        n = ebit_years(facts)
        if n < EBIT_YEARS_NEEDED:
            fv_missing.append(f"ebit_3y:only_{n}_annual_years")
    for m in fv_missing:
        key, concept = m.split(":", 1)
        gaps.append({"cik": cik, "tickers": base["tickers"], "sector_model": model,
                     "scope": "fair_value", "item": "fair_value", "input": key, "missing": concept})

    fy_end = info.get("fy_end")
    stale = bool(fy_end) and date.fromisoformat(fy_end) < date.today() - timedelta(days=STALE_AFTER_DAYS)
    metrics_blocked = sorted({g["item"] for g in gaps if g["scope"] == "metric"})
    summary = {
        **base, "error": None, "fact_rows": len(facts),
        "has_10k": bool(info), "latest_10k_filed": info.get("filed"), "fy_end": fy_end,
        "latest_10k_accn": info.get("accn"), "stale_10k": stale,
        "fair_value_ready": not fv_missing and bool(info) and not stale,
        "fair_value_missing": "; ".join(fv_missing),
        "metrics_blocked": " ".join(metrics_blocked),
        # Which alternative satisfied each key input (M5 uses these for confidence).
        "ebit_basis": met_by(EBIT, available),
        "debt_basis": met_by(DEBT, available),
        "shares_basis": met_by(SHARES, available),
    }
    return facts, summary, gaps


def main() -> None:
    universe = pd.read_csv(DATA_DIR / "universe.csv")
    companies = (universe.groupby("cik")
                 .agg(tickers=("ticker", list), sector_model=("sector_model", "first"))
                 .reset_index())
    sec = SECClient()

    all_facts, summaries, all_gaps = [], [], []
    print(f"Extracting fundamentals for {len(companies)} companies...")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(process, int(r.cik), r.tickers, r.sector_model, sec)
                   for r in companies.itertuples()]
        for i, fut in enumerate(as_completed(futures), 1):
            facts, summary, gaps = fut.result()
            if len(facts):
                facts.insert(0, "ticker", summary["tickers"].split()[0])
                all_facts.append(facts)
            summaries.append(summary)
            all_gaps += gaps
            if i % 50 == 0:
                print(f"  {i}/{len(companies)}")

    facts_df = pd.concat(all_facts, ignore_index=True)
    facts_df.to_parquet(DATA_DIR / "facts.parquet", index=False)
    summary_df = pd.DataFrame(summaries).sort_values("tickers")
    summary_df.to_csv(DATA_DIR / "fundamentals_summary.csv", index=False)
    gaps_df = pd.DataFrame(all_gaps).sort_values(["tickers", "scope", "item"])
    gaps_df.to_csv(DATA_DIR / "mapping_report.csv", index=False)

    pd.set_option("display.width", 200)
    print(f"\nFact rows: {len(facts_df):,}  ({(DATA_DIR / 'facts.parquet').stat().st_size / 1e6:.1f} MB parquet)")

    errors = summary_df[summary_df["error"].notna()]
    no10k = summary_df[summary_df["error"].isna() & ~summary_df["has_10k"].astype(bool)]
    stale = summary_df[summary_df["stale_10k"] == True]  # noqa: E712
    print(f"\nExtraction errors: {len(errors)}")
    if len(errors):
        print(errors[["tickers", "cik", "error"]].to_string(index=False))
    print(f"No 10-K in companyfacts: {len(no10k)}")
    if len(no10k):
        print(no10k[["tickers", "cik"]].to_string(index=False))
    print(f"Stale latest 10-K (FY end > {STALE_AFTER_DAYS} days ago): {len(stale)}")
    if len(stale):
        print(stale[["tickers", "cik", "fy_end", "latest_10k_filed"]].to_string(index=False))

    print("\n=== Fair-value readiness by model ===")
    ok = summary_df[summary_df["error"].isna()]
    print(ok.groupby("sector_model")["fair_value_ready"].agg(ready="sum", total="count").to_string())

    print("\n=== Inferred inputs (review these) ===")
    for col, pattern in [("debt_basis", "no_long_term_debt"), ("ebit_basis", r"^pretax_income$"),
                         ("shares_basis", "diluted_shares"), ("shares_basis", "eps_diluted")]:
        hit = ok[ok[col].fillna("").str.contains(pattern)]
        print(f"{col} = {pattern.strip('^$')}: {len(hit)}")
        print("   " + " ".join(hit["tickers"]))

    fv = gaps_df[gaps_df["scope"] == "fair_value"]
    print("\n=== Missing fair-value inputs (companies) ===")
    print(fv.groupby(["sector_model", "input", "missing"]).size().rename("companies")
          .sort_values(ascending=False).to_string())

    print("\n=== Metric coverage (companies that can compute, latest 10-K) ===")
    rows = []
    for model, rules in MODEL_RULES.items():
        n = int((ok["sector_model"] == model).sum())
        for metric in rules["metrics"]:
            blocked = gaps_df[(gaps_df["scope"] == "metric") & (gaps_df["sector_model"] == model)
                              & (gaps_df["item"] == metric)]["cik"].nunique()
            rows.append({"model": model, "metric": metric, "computable": n - blocked, "of": n})
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n=== Most common missing concepts for metrics ===")
    m = gaps_df[gaps_df["scope"] == "metric"].drop_duplicates(["cik", "missing"])
    print(m.groupby(["sector_model", "missing"]).size().rename("companies")
          .sort_values(ascending=False).head(25).to_string())


if __name__ == "__main__":
    main()
