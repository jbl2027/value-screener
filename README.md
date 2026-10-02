# value-screener
Personal value investing screener for the S&P 500, using free data only. Research and screening only; no trade execution.

## Setup
```
python -m pip install -r requirements.txt
```
Secrets live in `.env` (gitignored).

## Scripts
- `python scripts/build_universe.py`: S&P 500 tickers to CIK (SEC `company_tickers.json`) and SIC (EDGAR submissions API). Writes `data/universe.csv`.
