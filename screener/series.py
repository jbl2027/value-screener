"""Assemble point-in-time company data from long-format facts.

Given all facts for one company and an as-of date, CompanyData exposes:
  annual        fiscal-year table (index = fiscal year end, columns = concepts); durations are
                ~1-year periods and instants are balance-sheet values at the fiscal year end
  ttm(c)        trailing twelve months for a duration concept: last FY + current YTD - prior YTD
  latest_bs     instant concepts at the latest balance-sheet date (aligned to total_assets)
Only facts filed on or before as_of are used; per period, the latest-filed value wins.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from screener.tag_map import TAG_MAP

ANNUAL_DAYS = (350, 380)
YTD_DAYS = (80, 300)
TOLERANCE = timedelta(days=10)
RESTATEMENT_TOLERANCE = 0.02  # prior-year YTD restated by more than 2%: rebuild TTM on new basis
DURATION_CONCEPTS = {c for c, (_, ptype, _) in TAG_MAP.items() if ptype == "duration"}
INSTANT_CONCEPTS = {c for c, (_, ptype, _) in TAG_MAP.items() if ptype == "instant"}


@dataclass
class TTM:
    value: float | None
    period_end: str | None
    basis: str  # "ttm", "ttm_restated" (rebuilt on the restated basis), "fy" (no later 10-Q), "missing"
    restatement_ratio: float | None = None  # restated / originally reported prior-year YTD


@dataclass
class CompanyData:
    cik: int
    as_of: str
    facts: pd.DataFrame                  # latest-known value per (concept, period)
    all_facts: pd.DataFrame              # every version filed on or before as_of (originals too)
    annual: pd.DataFrame                 # index fy_end (ascending), columns concepts
    latest_bs_date: str | None
    latest_bs: dict[str, float] = field(default_factory=dict)
    shares_date: str | None = None

    # ---- annual helpers ----
    def fy(self, concept: str, back: int = 0) -> float | None:
        """Annual value `back` fiscal years before the latest (0 = latest)."""
        if concept not in self.annual.columns or len(self.annual) <= back:
            return None
        v = self.annual[concept].iloc[-1 - back]
        return None if pd.isna(v) else float(v)

    def fy_end(self, back: int = 0) -> str | None:
        return self.annual.index[-1 - back] if len(self.annual) > back else None

    # ---- trailing twelve months ----
    def ttm(self, concept: str) -> TTM:
        # Anchor to the company's latest fiscal year; an older value would be stale.
        if concept not in self.annual.columns or not len(self.annual) or pd.isna(self.annual[concept].iloc[-1]):
            return TTM(None, None, "missing")
        fy_end, fy_val = self.annual.index[-1], float(self.annual[concept].iloc[-1])
        f = self.facts[(self.facts["concept"] == concept) & self.facts["duration_days"].between(*YTD_DAYS)]
        start_target = date.fromisoformat(fy_end) + timedelta(days=1)
        starts = pd.to_datetime(f["period_start"]).dt.date
        ytd = f[(starts >= start_target - TOLERANCE) & (starts <= start_target + TOLERANCE)]
        if ytd.empty:
            return TTM(fy_val, fy_end, "fy")
        cur = ytd.sort_values(["period_end", "duration_days"]).iloc[-1]
        prior_end = date.fromisoformat(cur["period_end"]) - timedelta(days=365)
        ends = pd.to_datetime(f["period_end"]).dt.date
        prior = f[(ends >= prior_end - TOLERANCE) & (ends <= prior_end + TOLERANCE)
                  & ((f["duration_days"] - cur["duration_days"]).abs() <= 10)]
        if prior.empty:
            return TTM(fy_val, fy_end, "fy")
        p = prior.iloc[-1]
        # If the prior-year YTD was restated (e.g. discontinued operations after a spin-off), the
        # fiscal-year base from the last 10-K is on the old basis: the TTM mixes bases.
        originals = self.all_facts[(self.all_facts["concept"] == concept)
                                   & (self.all_facts["period_start"] == p["period_start"])
                                   & (self.all_facts["period_end"] == p["period_end"])]
        first = float(originals.sort_values("filed").iloc[0]["value"]) if len(originals) else float(p["value"])
        restated_prior = float(p["value"])
        ratio = restated_prior / first if first else None
        if abs(first - restated_prior) <= RESTATEMENT_TOLERANCE * max(abs(first), 1.0):
            return TTM(fy_val + float(cur["value"]) - restated_prior, cur["period_end"], "ttm", ratio)
        # Restated (e.g. discontinued operations after a spin-off): the fiscal-year value is on
        # the old basis. Estimate the restated remainder of last year by scaling the original
        # remainder (FY - original prior YTD) by the restatement ratio of the prior YTD.
        if first == 0:
            return TTM(None, None, "missing")
        remainder = (fy_val - first) * (restated_prior / first)
        return TTM(float(cur["value"]) + remainder, cur["period_end"], "ttm_restated", ratio)

    def instant_one_year_before(self, concept: str, ref_date: str) -> float | None:
        target = date.fromisoformat(ref_date) - timedelta(days=365)
        f = self.facts[(self.facts["concept"] == concept) & self.facts["duration_days"].isna()]
        ends = pd.to_datetime(f["period_end"]).dt.date
        hit = f[(ends >= target - TOLERANCE) & (ends <= target + TOLERANCE)]
        return float(hit.iloc[-1]["value"]) if len(hit) else None


def build_company(cik: int, facts: pd.DataFrame, as_of: str) -> CompanyData:
    all_facts = facts[facts["filed"] <= as_of].copy()
    # Latest-known value per period (a restatement filed later replaces the original).
    f = (all_facts.sort_values(["filed", "accn"])
          .groupby(["concept", "period_start", "period_end"], dropna=False, sort=False).tail(1))

    annual_dur = f[f["duration_days"].between(*ANNUAL_DAYS)]
    # Fiscal years come from 10-K filings only (some filers report TTM figures in 10-Qs).
    fy_ends = set(annual_dur.loc[annual_dur["form"].str.startswith("10-K")
                                 & annual_dur["concept"].isin(["net_income", "revenue"]), "period_end"])
    fy_ends = sorted(fy_ends)

    cols = {}
    for concept in TAG_MAP:
        if concept in DURATION_CONCEPTS:
            src = annual_dur[annual_dur["concept"] == concept]
        else:
            src = f[(f["concept"] == concept) & f["duration_days"].isna()]
        src = src[src["period_end"].isin(fy_ends)].groupby("period_end")["value"].last()
        if len(src):
            cols[concept] = src
    annual = pd.DataFrame(cols).reindex(fy_ends)
    annual.index.name = "fy_end"

    inst = f[f["duration_days"].isna()]
    assets = inst[inst["concept"] == "total_assets"]
    latest_bs_date = assets["period_end"].max() if len(assets) else None
    latest_bs = {}
    if latest_bs_date:
        at = inst[inst["period_end"] == latest_bs_date]
        latest_bs = at.groupby("concept")["value"].last().astype(float).to_dict()
    # Cover-page share count is dated near the filing date, not a balance-sheet date.
    so = inst[inst["concept"] == "shares_outstanding"].sort_values("period_end")
    shares_date = None
    if len(so):
        latest_bs["shares_outstanding"] = float(so.iloc[-1]["value"])
        shares_date = so.iloc[-1]["period_end"]
    return CompanyData(cik=cik, as_of=as_of, facts=f, all_facts=all_facts, annual=annual,
                       latest_bs_date=latest_bs_date, latest_bs=latest_bs, shares_date=shares_date)
