"""Milestone 6: fair value, margin of safety, flags, and disqualifications.

Inputs:  data/metrics.csv (compute_metrics.py), data/fundamentals_summary.csv,
         EDGAR submissions (8-K Item 2.01 check), FRED DGS10
Outputs: data/valuations.csv         one row per company: status, fair value, MoS, peers, reasons
         data/valuation_fits.csv     regression fits (bank, insurer) and peer-group medians
         data/corporate_actions.csv  Item 2.01 check per company
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener import rules as R  # noqa: E402
from screener.config import DATA_DIR  # noqa: E402
from screener.corporate_actions import check  # noqa: E402
from screener.fred import latest  # noqa: E402
from screener.fundamentals import extract_facts  # noqa: E402
from screener.gate_rules import restated_10ka  # noqa: E402
from screener.sec_client import SECClient  # noqa: E402
from screener.sensitivities import EBIT_ADJUSTMENTS, apply_ebit_adjustments  # noqa: E402
from screener.series import build_company  # noqa: E402
from screener.llm import PROMPT_VERSION  # noqa: E402
from screener.valuation import apply_qualitative_gate, value_all  # noqa: E402

OUTPUT_COLUMNS = [
    "as_of", "cik", "ticker", "name", "sector_model", "gics_sector", "gics_industry_group",
    "status", "status_reason", "confidence", "confidence_reasons", "warnings", "disqualify_reasons",
    "missing_inputs", "price", "fair_value", "mos", "method", "peer_level", "peer_group",
    "peer_count", "peer_multiple", "peer_n_sub", "peer_w", "peer_median_sub", "peer_median_anchor",
    "peer_anchor_level", "peer_anchor_group", "peer_anchor_n", "peer_blended", "peer_cap_applied",
    "fair_multiple", "company_multiple", "base_value", "base_basis", "ebit_adjustment",
    "ebit_ttm_reported", "ebit_3y_avg_reported",
    "shares", "earnings_yield", "treasury_yield", "treasury_guard_pass", "m_score",
    "m_score_imputed", "acquisitions_to_assets", "f_points", "f_tests", "f_score_scaled",
    "status_without_dq", "beneish_top_index", "captive_finance", "corporate_action_pending",
    "event_filed", "event_source", "first_periodic_after", "corporate_action_materiality",
    "qualitative_gate", "qualitative_detectors", "qualitative_reasons", "restatement", "restatement_evidence",
    "impairment_to_assets",
    "ebit_ttm", "ebit_3y_avg", "ebit_fy", "ebit_fy_prior", "ebit_fy_change",
]
BASELINE = DATA_DIR / "valuations.csv"


def disposed_revenue_share(parent_cik: int, spun_cik: int, sec: SECClient, as_of: str) -> float | None:
    """Spun-off company's revenue / parent's revenue for the parent's latest fiscal year."""
    parent = build_company(parent_cik, extract_facts(parent_cik, sec), as_of)
    spun = build_company(spun_cik, extract_facts(spun_cik, sec), as_of)
    fy_end, parent_rev = parent.fy_end(), parent.fy("revenue")
    if fy_end is None or not parent_rev or spun.annual.empty or "revenue" not in spun.annual:
        return None
    target = pd.Timestamp(fy_end)
    for end, rev in spun.annual["revenue"].dropna().items():
        if abs((pd.Timestamp(end) - target).days) <= 10:
            return float(rev) / parent_rev
    return None


def sensitivities(result: pd.DataFrame) -> pd.DataFrame:
    """Value adjusted companies on their as-reported EBIT (the primary uses adjusted EBIT)."""
    rows = []
    for ticker, s in EBIT_ADJUSTMENTS.items():
        r = result[result["ticker"] == ticker]
        if r.empty:
            continue
        r = r.iloc[0]
        ttm, avg3 = r["ebit_ttm_reported"], r["ebit_3y_avg_reported"]
        base = min(ttm, avg3)
        claims = r["enterprise_value"] - r["market_cap"] - r["net_debt"]
        fv = (r["peer_multiple"] * base - r["net_debt"] - claims) / r["shares"]
        rows.append({
            "ticker": ticker, "primary_basis": s["label"], "sensitivity": "as-reported EBIT",
            "source": s["source"], "ebit_ttm": r["ebit_ttm"], "ebit_ttm_reported": ttm,
            "ebit_3y_avg": r["ebit_3y_avg"], "ebit_3y_avg_reported": avg3,
            "ev_ebit": r["ev_ebit"], "ev_ebit_reported": r["ev_ebit_reported"],
            "fair_multiple": r["peer_multiple"], "fair_value": r["fair_value"],
            "fair_value_reported": fv, "price": r["price"], "mos": r["mos"],
            "mos_reported": (fv - r["price"]) / fv if fv > 0 else None, "status": r["status"],
        })
    return pd.DataFrame(rows)


def main() -> None:
    metrics = apply_ebit_adjustments(pd.read_csv(DATA_DIR / "metrics.csv"))
    summary = pd.read_csv(DATA_DIR / "fundamentals_summary.csv")
    as_of = metrics["as_of"].iloc[0]

    print(f"As of {as_of}. Checking 8-K Item 2.01 filings (last {R.CORPORATE_ACTION_LOOKBACK_DAYS} days)...")
    sec = SECClient()
    manual = {int(metrics.loc[metrics["ticker"] == t, "cik"].iloc[0]): ev
              for t, ev in R.MANUAL_CORPORATE_ACTIONS.items() if (metrics["ticker"] == t).any()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        corp = list(pool.map(lambda c: check(int(c), sec, as_of, R.CORPORATE_ACTION_LOOKBACK_DAYS,
                                             manual.get(int(c))), metrics["cik"]))
    corp = pd.DataFrame(corp)
    # Restatement detector, XBRL part: a 10-K/A with restated financial data since the last 10-K.
    restated = restated_10ka(pd.read_parquet(DATA_DIR / "facts.parquet", columns=["cik", "concept", "form", "filed"]),
                             summary.set_index("cik")["latest_10k_filed"].to_dict())
    corp["restatement"] = corp["restatement"].fillna(False).astype(bool) | corp["cik"].isin(restated)
    corp["restatement_evidence"] = [ev if isinstance(ev, str) else restated.get(c)
                                    for c, ev in zip(corp["cik"], corp["restatement_evidence"])]
    corp["disposed_revenue_share"] = corp["cik"].map(
        lambda c: disposed_revenue_share(c, manual[c][2], sec, as_of) if c in manual and manual[c][2] else None)
    corp.to_csv(DATA_DIR / "corporate_actions.csv", index=False)

    t_date, t_yield = latest(R.TREASURY_SERIES, as_of)
    print(f"10-yr Treasury ({R.TREASURY_SERIES}) {t_yield:.2f}% on {t_date} "
          f"-> standard model needs EV/EBIT < {100 / t_yield:.1f}x to flag")

    result, fits = value_all(metrics, summary, corp, t_yield)
    llm_path = DATA_DIR / f"llm_scores_{PROMPT_VERSION}.csv"
    flags_path = DATA_DIR / "filing_flags.csv"
    result = apply_qualitative_gate(result, pd.read_csv(llm_path) if llm_path.exists() else None,
                                    pd.read_csv(flags_path) if flags_path.exists() else None)
    gated = result[result["qualitative_gate"]]
    print(f"Qualitative gate fired for {len(gated)} companies: "
          + (gated["qualitative_detectors"].str.split(", ").explode().value_counts().to_dict().__repr__()
             if len(gated) else "none"))
    baseline = pd.read_csv(BASELINE) if BASELINE.exists() else None
    sens = sensitivities(result)
    sens.to_csv(DATA_DIR / "sensitivities.csv", index=False)
    result[OUTPUT_COLUMNS].sort_values(["status", "mos"], ascending=[True, False]).to_csv(
        DATA_DIR / "valuations.csv", index=False)

    peer_rows = (result[result["sector_model"].isin(["standard", "reit"])]
                 .groupby(["sector_model", "peer_level", "peer_group"])
                 .agg(peer_count=("peer_count", "first"), peer_multiple=("peer_multiple", "first"),
                      companies=("ticker", "count")).reset_index())
    fit_rows = pd.DataFrame(fits).assign(peer_level="regression")
    pd.concat([fit_rows, peer_rows], ignore_index=True).assign(as_of=as_of).to_csv(
        DATA_DIR / "valuation_fits.csv", index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 70)
    print("\n=== Status by model ===")
    print(pd.crosstab(result["sector_model"], result["status"], margins=True).to_string())

    print("\n=== Regression fits ===")
    for f in fits:
        if f.get("r2") is None:
            print(f"  {f['model']}: insufficient data (n={f['n']})")
        else:
            print(f"  {f['model']}: {f['y']} = {f['intercept']:.3f} + {f['slope']:.3f} x {f['x']}   "
                  f"R2={f['r2']:.3f}  n={f['n']}")

    print("\n=== Peer groups used ===")
    print(peer_rows.round(2).to_string(index=False))

    def show(status, cols, title):
        sub = result[result["status"] == status]
        print(f"\n=== {title} ({len(sub)}) ===")
        if len(sub):
            print(sub.sort_values("mos", ascending=False)[cols].round(3).to_string(index=False))

    show(R.STATUS_FLAG, ["ticker", "sector_model", "price", "fair_value", "mos", "company_multiple",
                         "peer_multiple", "fair_multiple", "base_basis", "warnings"], "FLAGS")
    show(R.STATUS_WATCH, ["ticker", "sector_model", "price", "fair_value", "mos", "status_reason",
                          "confidence_reasons", "warnings"], "WATCH")
    show(R.STATUS_DISQUALIFIED, ["ticker", "sector_model", "mos", "status_without_dq", "disqualify_reasons",
                                 "beneish_top_index"], "Disqualified")
    show(R.STATUS_NOT_APPLICABLE, ["ticker", "sector_model", "status_reason"], "Not applicable")
    show(R.STATUS_CORPORATE_ACTION, ["ticker", "event_filed", "status_reason"], "Corporate action pending")
    window = result[result["corporate_action_materiality"].notna()]
    post = window[window["corporate_action_materiality"].str.startswith("material")]
    print(f"\n=== Corporate actions after latest fiscal year end: {len(window)}; "
          f"material -> TTM-only regime: {len(post)} ===")
    print(window.sort_values("corporate_action_materiality")[
        ["ticker", "status", "mos", "event_filed", "event_source", "corporate_action_materiality"]]
        .round(3).to_string(index=False))
    show(R.STATUS_EXCLUDED, ["ticker", "status_reason"], "Excluded")
    show(R.STATUS_INSUFFICIENT, ["ticker", "sector_model", "missing_inputs"], "Insufficient data")

    guard = result[(result["status"] == R.STATUS_NO_FLAG) & (result["mos"] >= R.MOS_FLAG_THRESHOLD)]
    print(f"\nBlocked by the Treasury guard (MoS >= 30% but EBIT/EV <= {t_yield:.2f}%): "
          + (" ".join(guard["ticker"]) or "none"))
    review = result[result["warnings"].str.contains("beneish review", na=False)]
    print("Beneish M&A review warnings: " + (" ".join(review["ticker"]) or "none"))

    if baseline is not None:
        print("\n=== Change vs previous run ===")
        before = baseline["status"].value_counts().to_dict()
        after = result["status"].value_counts().to_dict()
        for s in sorted(set(before) | set(after)):
            print(f"  {s:26s} {before.get(s, 0):4d} -> {after.get(s, 0):4d}")
        prev = baseline.set_index("ticker")
        cur = result.set_index("ticker")
        top_before = baseline[baseline["status"] == R.STATUS_FLAG].nlargest(10, "mos")
        top_after = result[result["status"].isin([R.STATUS_FLAG, R.STATUS_WATCH])].nlargest(10, "mos")
        print("\nTop-10 MoS before (flags):")
        print(top_before[["ticker", "mos"]].assign(now_status=lambda d: d["ticker"].map(cur["status"]),
                                                   now_mos=lambda d: d["ticker"].map(cur["mos"]))
              .round(3).to_string(index=False))
        print("\nTop-10 MoS now (flag + watch):")
        print(top_after[["ticker", "status", "mos", "peer_level", "peer_group", "peer_multiple"]]
              .assign(prev_mos=lambda d: d["ticker"].map(prev["mos"]),
                      prev_status=lambda d: d["ticker"].map(prev["status"]))
              .round(3).to_string(index=False))
        track = ["UAL", "UHS", "ZTS", "EIX"]
        print("\nTracked tickers (before -> now):")
        t = pd.DataFrame({
            "prev_status": prev.reindex(track)["status"], "prev_mos": prev.reindex(track)["mos"],
            "prev_peer": prev.reindex(track)["peer_group"], "prev_peer_multiple": prev.reindex(track)["peer_multiple"],
            "status": cur.reindex(track)["status"], "mos": cur.reindex(track)["mos"],
            "peer": cur.reindex(track)["peer_group"], "peer_count": cur.reindex(track)["peer_count"],
            "peer_multiple": cur.reindex(track)["peer_multiple"],
        })
        print(t.round(3).to_string())

    if len(sens):
        print("\n=== EBIT adjustments: primary vs as-reported sensitivity ===")

        def pct(v):
            return f"{v:.0%}" if v is not None and v == v else "n/a (fair value <= 0)"

        for s in sens.itertuples():
            print(f"{s.ticker}: primary = {s.primary_basis} ({s.source}); status {s.status}")
            print(f"  TTM EBIT primary {s.ebit_ttm / 1e6:,.0f}M vs reported {s.ebit_ttm_reported / 1e6:,.0f}M; "
                  f"3-yr avg {s.ebit_3y_avg / 1e6:,.0f}M vs {s.ebit_3y_avg_reported / 1e6:,.0f}M")
            print(f"  Fair EV/EBIT {s.fair_multiple:.1f}x; fair value ${s.fair_value:,.2f} (MoS {pct(s.mos)}) vs "
                  f"as-reported ${s.fair_value_reported:,.2f} (MoS {pct(s.mos_reported)}); price ${s.price:,.2f}")


if __name__ == "__main__":
    main()
