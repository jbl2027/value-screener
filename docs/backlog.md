# Backlog

Items deferred to after the milestone noted.

## After Milestone 5
- **Fill custom-tag gaps from the SEC Financial Statement Data Sets** (free quarterly bulk
  files at https://www.sec.gov/dera/data/financial-statement-data-sets). Unlike the
  companyfacts API, they include company extension tags. Known gaps: NEE capex and interest
  expense, CAT current long-term debt, PLD gains on property sales (post-2018). Milestone 3's
  `data/mapping_report.csv` lists the rest.

## Rules decided, implemented in Milestones 5–6 (data designed in `screener/rules.py`)
1. Banks and insurers: no Sloan accruals, Piotroski, or Beneish. The Beneish disqualifier
   applies to standard and REIT models only.
2. Data confidence: if any fair-value / margin-of-safety input is missing, status is
   `insufficient_data` (never flagged), with the missing inputs logged.
3. Captive finance (CAT, DE, GM, F): flags marked low confidence.

## Open assumptions to confirm
- REIT fair value = sector-median P/FFO × FFO per share.
- Insurer fair value = sector-median P/B × book value per share.
