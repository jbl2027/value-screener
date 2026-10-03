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
- `python scripts/explore_tags.py PATTERN TICKER...`: dev helper listing which XBRL tags a company uses.
- `python scripts/extract_research.py`: regenerates `docs/research.md` from the research PDF.

## Point-in-time facts
Each fact keeps `period_start`, `period_end`, `fy`, `fp`, `form`, `filed`, and `accn`. A value is stored once, from the first filing that reported it; a later filing reporting a different value for the same period is kept as a separate row (a restatement). Query "as of" a date with `filed <= date`, taking the latest-filed row per period.
