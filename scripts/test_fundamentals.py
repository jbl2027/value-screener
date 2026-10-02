"""Milestone 2: EDGAR fundamentals for 10 test companies across sectors.

Prints the values from each company's latest 10-K (in $ millions; shares in millions) so they
can be tied out to the filed documents, plus which XBRL tag supplied each value.

Outputs:
  data/test_facts.csv       all point-in-time facts for the test companies
  data/test_comparison.csv  latest-10-K snapshot (value, tag, period, accession) per concept
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.fundamentals import extract_facts, latest_annual_snapshot  # noqa: E402
from screener.sec_client import SECClient  # noqa: E402
from screener.tag_map import TAG_MAP  # noqa: E402

TEST_TICKERS = [
    "JPM",   # bank
    "PLD",   # REIT
    "MSFT",  # software (June FY)
    "CAT",   # industrial
    "AAPL",  # hardware (September FY)
    "XOM",   # energy (2026 holding-company CIK change)
    "JNJ",   # pharma (52/53-week year)
    "WMT",   # retail (January FY)
    "NEE",   # utility
    "TRV",   # P&C insurer
]

# Tags shown abbreviated in the tag table.
TAG_ABBREV = [
    ("RevenueFromContractWithCustomer", "RevFromContract"),
    ("IncomeLossFromContinuingOperationsBeforeIncomeTaxes", "PretaxContOps"),
    ("NetCashProvidedByUsedIn", "NetCash"),
    ("PropertyPlantAndEquipment", "PPE"),
    ("CashAndCashEquivalents", "CashEq"),
    ("EntityCommonStockSharesOutstanding", "dei:SharesOut"),
]


def abbreviate(tag: str) -> str:
    for long, short in TAG_ABBREV:
        tag = tag.replace(long, short)
    return tag if len(tag) <= 34 else tag[:31] + "..."


def main() -> None:
    universe = pd.read_csv(DATA_DIR / "universe.csv").set_index("ticker")
    sec = SECClient()

    all_facts, snap_rows, values, tags, headers = [], [], {}, {}, {}
    for ticker in TEST_TICKERS:
        cik = int(universe.at[ticker, "cik"])
        print(f"Extracting {ticker} (CIK {cik})...")
        facts = extract_facts(cik, sec)
        facts.insert(0, "ticker", ticker)
        all_facts.append(facts)

        info, snap = latest_annual_snapshot(facts)
        headers[ticker] = info
        values[ticker] = {}
        tags[ticker] = {}
        for concept, (unit, _, _) in TAG_MAP.items():
            if concept in snap.index:
                row = snap.loc[concept]
                values[ticker][concept] = row["value"] / 1e6
                tags[ticker][concept] = abbreviate(row["tag"])
                snap_rows.append({"ticker": ticker, "concept": concept, "value": row["value"],
                                  "unit": unit, "tag": row["tag"], "period_start": row["period_start"],
                                  "period_end": row["period_end"], "form": row["form"],
                                  "filed": row["filed"], "accn": row["accn"]})
            else:
                values[ticker][concept] = None
                tags[ticker][concept] = "-"

    facts_df = pd.concat(all_facts, ignore_index=True)
    facts_df.to_csv(DATA_DIR / "test_facts.csv", index=False)
    pd.DataFrame(snap_rows).to_csv(DATA_DIR / "test_comparison.csv", index=False)

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.max_colwidth", 36)

    print("\n=== Latest 10-K used for each company ===")
    for t, info in headers.items():
        model = universe.at[t, "sector_model"]
        print(f"  {t:5s} {model:8s} FY end {info.get('fy_end')}  filed {info.get('filed')}  accn {info.get('accn')}")

    val_df = pd.DataFrame(values)
    print("\n=== Values from latest 10-K ($ millions; shares in millions) ===")
    print(val_df.map(lambda v: "-" if pd.isna(v) else f"{v:,.0f}").to_string())

    print("\n=== XBRL tag used ===")
    print(pd.DataFrame(tags).to_string())

    print("\n=== Point-in-time history ===")
    print(f"Total fact rows: {len(facts_df):,}")
    restated = facts_df.duplicated(["ticker", "concept", "period_start", "period_end"], keep=False)
    print(f"Rows that are restatements or the originals they replaced: {int(restated.sum()):,}")
    print(facts_df.groupby("ticker").size().rename("rows").to_string())


if __name__ == "__main__":
    main()
