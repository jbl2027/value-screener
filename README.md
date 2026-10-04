# value-screener
Personal value investing screener for the S&P 500, using free data only. Research and screening only; no trade execution.

## Setup
```
python -m pip install -r requirements.txt
```
Secrets live in `.env` (gitignored).

## Scripts
- `python scripts/build_universe.py`: S&P 500 tickers to CIK (SEC `company_tickers.json`) and SIC (EDGAR submissions API). Writes `data/universe.csv`.
- `python scripts/test_fundamentals.py`: point-in-time EDGAR fundamentals for 10 test companies via the tag fallback map in `screener/tag_map.py`. Prints a latest-10-K tie-out table; writes `data/test_comparison.csv` and `data/test_facts.csv`.
- `python scripts/build_fundamentals.py`: point-in-time fundamentals for the full universe. Writes `data/facts.parquet` (gitignored), `data/fundamentals_summary.csv` (per-company readiness and input bases), and `data/mapping_report.csv` (every missing input, by metric and fair value). Rules live in `screener/rules.py`.
- `python scripts/update_prices.py [--verify N]`: daily closes for the universe from one Tiingo bulk IEX request (run after the US close). Appends to `data/prices.parquet` (gitignored). `--verify N` spot-checks N closes against the EOD endpoint.
- `python scripts/compute_metrics.py [--as-of YYYY-MM-DD]`: Milestone 5 metrics for every company, point-in-time as of the latest price date. Writes `data/metrics.csv` and `data/metrics_notes.csv` (why any metric is missing). Definitions are documented at the top of `screener/metrics.py`.
- `python scripts/compute_valuations.py`: Milestone 6 fair value, margin of safety, and status (flag / no_flag / disqualified / insufficient_data / not_applicable / corporate_action_pending / excluded) with confidence and reasons. Writes `data/valuations.csv`, `data/valuation_fits.csv` (regression R2 and peer medians), and `data/corporate_actions.csv`. Methods are documented at the top of `screener/valuation.py`.
- `python scripts/compute_scores.py`: Milestone 7 composite score (0-100) with the report's weights and sector-neutral percentile ranks (2-digit SIC major group). Writes `data/scores.csv`. Method documented at the top of `screener/scoring.py`.
- `python -u scripts/scan_spinoffs.py [--days 365]`: scans Item 8.01 8-Ks for completed spin-offs/separations and writes candidates to `data/spinoff_candidates.csv` for manual review (approved events go in `rules.MANUAL_CORPORATE_ACTIONS`). Resumable.
- `python scripts/explore_tags.py PATTERN TICKER...`: dev helper listing which XBRL tags a company uses.
- `python scripts/extract_research.py`: regenerates `docs/research.md` from the research PDF.

## Point-in-time facts
Each fact keeps `period_start`, `period_end`, `fy`, `fp`, `form`, `filed`, and `accn`. A value is stored once, from the first filing that reported it; a later filing reporting a different value for the same period is kept as a separate row (a restatement). Query "as of" a date with `filed <= date`, taking the latest-filed row per period.
