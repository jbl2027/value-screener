"""Milestone 1: build the S&P 500 universe with CIK and SIC codes.

Constituents come from Wikipedia's S&P 500 list (free, updated on index changes).
CIKs come from SEC company_tickers.json; SIC codes from the EDGAR submissions API.

Output: data/universe.csv
"""
import io
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.sec_client import SECClient  # noqa: E402
from screener.sectors import sector_model, sic_division  # noqa: E402

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
OUT_PATH = DATA_DIR / "universe.csv"


def normalize_ticker(ticker: str) -> str:
    # Wikipedia uses BRK.B; SEC and Tiingo use BRK-B.
    return ticker.strip().upper().replace(".", "-")


def fetch_sp500() -> pd.DataFrame:
    resp = requests.get(WIKI_URL, headers={"User-Agent": "value-screener/0.1 (personal research)"}, timeout=30)
    resp.raise_for_status()
    table = pd.read_html(io.StringIO(resp.text), attrs={"id": "constituents"})[0]
    df = pd.DataFrame({
        "ticker": table["Symbol"].map(normalize_ticker),
        "name": table["Security"],
        "gics_sector": table["GICS Sector"],
        "gics_sub_industry": table["GICS Sub-Industry"],
        "wiki_cik": pd.to_numeric(table["CIK"], errors="coerce").astype("Int64"),
    })
    return df


def map_ciks(df: pd.DataFrame, sec: SECClient) -> pd.DataFrame:
    tickers = sec.company_tickers()
    sec_map = {normalize_ticker(row["ticker"]): int(row["cik_str"]) for row in tickers.values()}
    df["sec_cik"] = df["ticker"].map(sec_map).astype("Int64")
    df["cik"] = df["sec_cik"].fillna(df["wiki_cik"])
    df["cik_source"] = df["sec_cik"].notna().map({True: "sec_company_tickers", False: "wikipedia"})
    df.loc[df["cik"].isna(), "cik_source"] = "unmapped"
    df["cik_mismatch"] = df["sec_cik"].notna() & df["wiki_cik"].notna() & (df["sec_cik"] != df["wiki_cik"])
    return df


def add_sic(df: pd.DataFrame, sec: SECClient) -> pd.DataFrame:
    records = {}
    ciks = df["cik"].dropna().astype(int).unique()
    for i, cik in enumerate(ciks, 1):
        try:
            sub = sec.submissions(cik)
            sic = int(sub["sic"]) if sub.get("sic") else None
            records[cik] = {
                "sec_name": sub.get("name"),
                "sic": sic,
                "sic_description": sub.get("sicDescription"),
                "fiscal_year_end": sub.get("fiscalYearEnd"),
                "submissions_error": None,
            }
        except Exception as exc:  # keep going; failures are reported in the CSV
            records[cik] = {"submissions_error": type(exc).__name__}
        if i % 50 == 0:
            print(f"  submissions: {i}/{len(ciks)}")
    sic_df = pd.DataFrame.from_dict(records, orient="index")
    sic_df.index.name = "cik"
    df = df.merge(sic_df.reset_index(), on="cik", how="left")
    df["sic"] = df["sic"].astype("Int64")
    sics = [None if pd.isna(s) else int(s) for s in df["sic"]]
    df["sic_division"] = [sic_division(s) for s in sics]
    models = [sector_model(t, s) for t, s in zip(df["ticker"], sics)]
    df["sector_model"] = [m for m, _ in models]
    df["sector_model_source"] = [src for _, src in models]
    return df


def main() -> None:
    sec = SECClient()
    print("Fetching S&P 500 constituents...")
    df = fetch_sp500()
    print(f"  {len(df)} tickers")

    print("Mapping tickers to CIK via SEC company_tickers.json...")
    df = map_ciks(df, sec)

    print("Fetching SIC codes from EDGAR submissions API...")
    df = add_sic(df, sec)

    cols = ["ticker", "cik", "name", "sec_name", "sic", "sic_description", "sic_division",
            "sector_model", "sector_model_source", "gics_sector", "gics_sub_industry", "fiscal_year_end",
            "cik_source", "cik_mismatch", "submissions_error"]
    df = df[cols].sort_values("ticker").reset_index(drop=True)

    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {len(df)} rows to {OUT_PATH.relative_to(DATA_DIR.parent)}")

    print("\n=== Summary ===")
    print(f"Tickers:               {len(df)}")
    print(f"Unique CIKs:           {df['cik'].nunique()}")
    print(f"CIK source:            {df['cik_source'].value_counts().to_dict()}")
    print(f"CIK mismatches (SEC vs Wikipedia): {int(df['cik_mismatch'].sum())}")
    print(f"Missing SIC:           {int(df['sic'].isna().sum())}")
    print(f"Sector model:          {df['sector_model'].value_counts().to_dict()}")
    print("\nNon-standard sector models:")
    for model in ["bank", "reit", "insurer"]:
        sub = df[df["sector_model"] == model]
        tickers = [t + ("*" if s == "override" else "") for t, s in zip(sub["ticker"], sub["sector_model_source"])]
        print(f"  {model:8s} ({len(sub)}): {' '.join(tickers)}")
    print("  (* = manual override)")
    print("\nSIC division counts:")
    print(df["sic_division"].value_counts(dropna=False).to_string())

    issues = df[(df["cik_source"] != "sec_company_tickers") | df["cik_mismatch"]
                | df["sic"].isna() | df["submissions_error"].notna()]
    if not issues.empty:
        print("\nRows needing review:")
        print(issues[["ticker", "name", "cik", "cik_source", "cik_mismatch", "sic", "submissions_error"]].to_string(index=False))

    dupes = df[df.duplicated("cik", keep=False)].sort_values("cik")
    if not dupes.empty:
        print("\nMulti-class share tickers (same CIK):")
        print(dupes[["ticker", "cik", "name"]].to_string(index=False))


if __name__ == "__main__":
    main()
