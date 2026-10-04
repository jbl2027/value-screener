"""Milestone 5 metrics. Each metric is None when an input is missing; the reason goes to notes.

Conventions
  t = latest fiscal year, t-1 = prior fiscal year (annual values from 10-Ks, restated values
  preferred). Valuation metrics use TTM flows and the latest balance sheet with today's price.

Definitions
  EBIT               operating income; else pretax + interest expense; else pretax (conservative)
  EV                 market cap + total debt + preferred + minority interest - cash - ST investments
  EV/EBIT            EV / TTM EBIT
  GP/A               gross profit_t / total assets_t                      (Novy-Marx)
  Operating profit.  (EBIT_t + R&D_t) / total assets_t   (Ball, Gerakos, Linnainmaa & Nikolaev 2015)
  Sloan accruals     (NI_t - CFO_t - CFI_t) / average total assets(t, t-1)
  Piotroski F        9 binary tests, t vs t-1 (ROA on beginning assets); full score needs all 9.
                     Margin test uses operating margin when there is no COGS. Scaled score =
                     9 x points / tests run, when at least 7 tests run (else low confidence).
  Beneish M          8-variable model; strict needs all 8 indices
  Shareholder yield  (TTM dividends + TTM buybacks - TTM issuance + debt paydown_t) / market cap
                     where debt paydown_t = total debt_(t-1) - total debt_t (0 for banks)
  ROIC               NOPAT_t / average invested capital(t, t-1)
                     NOPAT = EBIT x (1 - tax rate), tax rate = tax/pretax clipped to [0, 35%]
                     (21% if pretax <= 0); IC = equity + debt + minority - cash - ST investments
  Reinvestment rate  (capex - D&A + change in non-cash working capital) / NOPAT
  Growth             ROIC x reinvestment rate                                (McKinsey)
  R&D capitalized    research asset = sum_k=0..4 R&D_(t-k) x (5-k)/5; amortization =
                     sum_k=1..5 R&D_(t-k) / 5; adjusted EBIT = EBIT + R&D_t - amortization;
                     adjusted IC adds the research asset; adjusted reinvestment adds R&D_t - amort.
  Banks              TBV = equity - preferred - goodwill - intangibles; P/TBV = market cap / TBV;
                     ROTCE = TTM NI / average TCE (now, 1 year ago)
  Insurers           book value = equity - preferred; P/B; ROE = TTM NI / average equity
  REITs              FFO = TTM (NI + D&A - gains on property sales); P/FFO = market cap / FFO;
                     ffo_3y_avg = average of the last 3 fiscal-year FFOs
"""
import math
from datetime import date

from screener.debt import resolve_debt
from screener.rules import ABSENT_MEANS_ZERO, MODEL_RULES, PIOTROSKI_MIN_TESTS
from screener.series import CompanyData

DEFAULT_TAX_RATE = 0.21
MAX_TAX_RATE = 0.35
RD_LIFE_YEARS = 5
BENEISH_MAX_IMPUTED = 2  # imputed variant: up to 2 missing indices set to 1 (neutral)
MAX_SHARES_AGE_DAYS = 400
MIN_SHARES = 1_000_000  # an S&P 500 company with fewer shares is a data error

BENEISH_WEIGHTS = {"dsri": 0.920, "gmi": 0.528, "aqi": 0.404, "sgi": 0.892, "depi": 0.115,
                   "sgai": -0.172, "tata": 4.679, "lvgi": -0.327}
BENEISH_INTERCEPT = -4.84


def _div(a, b):
    if a is None or b is None or b == 0 or (isinstance(b, float) and math.isnan(b)):
        return None
    return a / b


class Calc:
    def __init__(self, cd: CompanyData, model: str, price: float | None, price_date: str | None):
        self.cd, self.model = cd, model
        self.price, self.price_date = price, price_date
        self.notes: list[str] = []
        self.m: dict = {}

    # ---- input helpers ----
    def fy(self, concept: str, back: int = 0) -> float | None:
        v = self.cd.fy(concept, back)
        if v is None and concept in ABSENT_MEANS_ZERO and len(self.cd.annual) > back:
            return 0.0
        return v

    def ttm(self, concept: str) -> float | None:
        t = self.cd.ttm(concept)
        if t.value is None and concept in ABSENT_MEANS_ZERO:
            return 0.0
        return t.value

    def bs(self, concept: str) -> float | None:
        v = self.cd.latest_bs.get(concept)
        if v is None and concept in ABSENT_MEANS_ZERO:
            return 0.0
        return v

    def ebit_fy(self, back: int = 0) -> tuple[float | None, str | None]:
        op = self.cd.fy("operating_income", back)
        if op is not None:
            return op, "operating_income"
        pretax, interest = self.cd.fy("pretax_income", back), self.cd.fy("interest_expense", back)
        if pretax is not None and interest is not None:
            return pretax + interest, "pretax_income+interest_expense"
        return (pretax, "pretax_income") if pretax is not None else (None, None)

    def ebit_ttm(self) -> tuple[float | None, str | None, str]:
        op = self.cd.ttm("operating_income")
        if op.value is not None:
            return op.value, "operating_income", op.basis
        pretax, interest = self.cd.ttm("pretax_income"), self.cd.ttm("interest_expense")
        if pretax.value is not None and interest.value is not None:
            return pretax.value + interest.value, "pretax_income+interest_expense", pretax.basis
        if pretax.value is not None:
            return pretax.value, "pretax_income", pretax.basis
        return None, None, "missing"

    def gross_profit(self, back: int = 0) -> float | None:
        gp = self.cd.fy("gross_profit", back)
        if gp is not None:
            return gp
        rev, cogs = self.cd.fy("revenue", back), self.cd.fy("cost_of_revenue", back)
        return rev - cogs if rev is not None and cogs is not None else None

    def sga(self, back: int = 0) -> float | None:
        v = self.cd.fy("sga_expense", back)
        if v is not None:
            return v
        sm, ga = self.cd.fy("selling_marketing_expense", back), self.cd.fy("g_and_a_expense", back)
        return sm + ga if sm is not None and ga is not None else None

    def annual_values(self, back: int) -> dict:
        if len(self.cd.annual) <= back:
            return {}
        row = self.cd.annual.iloc[-1 - back]
        return {k: float(v) for k, v in row.items() if not (isinstance(v, float) and math.isnan(v))}

    def debt_fy(self, back: int) -> float | None:
        d = resolve_debt(self.cd.cik, self.annual_values(back))
        return d.total if d.ok else None

    # ---- valuation base ----
    def _recent(self, period_end: str | None) -> bool:
        return bool(period_end) and (date.fromisoformat(self.cd.as_of)
                                     - date.fromisoformat(period_end)).days <= MAX_SHARES_AGE_DAYS

    def valuation_base(self) -> None:
        cd, m = self.cd, self.m
        m["price"], m["price_date"] = self.price, self.price_date
        # Share counts must be recent: stale cover-page facts exist (FOX reports 1 share dated
        # 2019; ERIE's last diluted count is a 2021 Class B figure).
        shares, basis = None, None
        so = cd.latest_bs.get("shares_outstanding")
        if so and so >= MIN_SHARES and self._recent(cd.shares_date):
            shares, basis = so, "shares_outstanding"
        if shares is None:
            dil = cd.facts[(cd.facts["concept"] == "diluted_shares")].sort_values(["period_end", "duration_days"])
            keep = [self._recent(p) and v >= MIN_SHARES for p, v in zip(dil["period_end"], dil["value"])]
            dil = dil[keep]
            if len(dil):
                shares, basis = float(dil.iloc[-1]["value"]), "diluted_shares"
        if shares is None and self._recent(cd.fy_end()):
            implied = _div(cd.fy("net_income"), cd.fy("eps_diluted"))
            if implied and implied >= MIN_SHARES:
                shares, basis = implied, "net_income/eps_diluted"
        m["shares"], m["shares_basis"], m["shares_date"] = shares, basis, cd.shares_date
        m["market_cap"] = self.price * shares if self.price and shares else None
        if m["market_cap"] is None:
            self.notes.append("market_cap: missing " + ("price" if not self.price else "shares"))

        values = {**cd.latest_bs, "interest_expense": self.ttm("interest_expense"),
                  "revenue": self.ttm("revenue")}
        debt, debt_date = resolve_debt(cd.cik, values), cd.latest_bs_date
        if not debt.ok and len(cd.annual):
            fy_debt = resolve_debt(cd.cik, self.annual_values(0))
            if fy_debt.ok:
                debt, debt_date = fy_debt, cd.fy_end()
                self.notes.append("debt: latest quarter unresolved, used fiscal year end")
        m.update(total_debt=debt.total, debt_basis=debt.basis, debt_confidence=debt.confidence,
                 debt_date=debt_date, debt_problem=debt.problem, bs_date=cd.latest_bs_date)
        cash_like = (self.bs("cash") or 0) + (self.bs("short_term_investments") or 0)
        if self.bs("cash") is None:
            self.notes.append("cash: missing at latest balance sheet")
            cash_like = None
        m["cash_like"] = cash_like
        m["net_debt"] = debt.total - cash_like if debt.ok and cash_like is not None else None
        if m["market_cap"] is not None and m["net_debt"] is not None:
            m["enterprise_value"] = (m["market_cap"] + m["net_debt"] + (self.bs("preferred_stock") or 0)
                                     + (self.bs("minority_interest") or 0))
        else:
            m["enterprise_value"] = None

    # ---- standard / REIT metrics ----
    def ev_ebit(self) -> None:
        m = self.m
        ebit, basis, period_basis = self.ebit_ttm()
        m.update(ebit_ttm=ebit, ebit_basis=basis, ebit_period_basis=period_basis)
        m["ev_ebit"] = _div(m.get("enterprise_value"), ebit) if ebit and ebit > 0 else None
        m["earnings_yield"] = _div(ebit, m.get("enterprise_value")) if m.get("enterprise_value") else None
        e0, e1 = self.ebit_fy(0)[0], self.ebit_fy(1)[0]
        m["ebit_fy"], m["ebit_fy_prior"] = e0, e1
        m["ebit_fy_change"] = (e0 - e1) / abs(e1) if e0 is not None and e1 else None
        years = [self.ebit_fy(b)[0] for b in range(3)]
        years = [y for y in years if y is not None]
        m["ebit_3y_n"] = len(years)
        m["ebit_3y_avg"] = sum(years) / 3 if len(years) == 3 else None
        if ebit is not None and ebit <= 0:
            self.notes.append("ev_ebit: EBIT <= 0")

    def gp_assets(self) -> None:
        self.m["gp_assets"] = _div(self.gross_profit(), self.cd.fy("total_assets"))
        if self.m["gp_assets"] is None:
            self.notes.append("gp_assets: missing gross profit or assets")
        # Operating profitability (Ball, Gerakos, Linnainmaa & Nikolaev 2015):
        # (operating income + R&D) / total assets, with the system's EBIT definition.
        ebit, _ = self.ebit_fy(0)
        rd = self.fy("rd_expense") or 0.0
        self.m["total_assets_fy"] = self.cd.fy("total_assets")
        self.m["operating_profitability"] = _div(ebit + rd, self.cd.fy("total_assets")) if ebit is not None else None
        if self.m["operating_profitability"] is None:
            self.notes.append("operating_profitability: missing EBIT or assets")

    def sloan(self) -> None:
        ni, cfo, cfi = self.cd.fy("net_income"), self.cd.fy("operating_cash_flow"), self.cd.fy("investing_cash_flow")
        ta0, ta1 = self.cd.fy("total_assets"), self.cd.fy("total_assets", 1)
        avg_ta = (ta0 + ta1) / 2 if ta0 and ta1 else ta0
        if None in (ni, cfo, cfi, avg_ta):
            self.m["sloan_accruals"] = None
            self.notes.append("sloan_accruals: missing input")
            return
        self.m["sloan_accruals"] = (ni - cfo - cfi) / avg_ta

    def piotroski(self) -> None:
        f = self.cd.fy
        ni0, ni1 = f("net_income"), f("net_income", 1)
        ta0, ta1, ta2 = f("total_assets"), f("total_assets", 1), f("total_assets", 2)
        cfo0 = f("operating_cash_flow")
        rev0, rev1 = f("revenue"), f("revenue", 1)
        roa0, roa1 = _div(ni0, ta1), _div(ni1, ta2)

        def ltd(back):  # same basis for both years
            for c in ("long_term_debt_noncurrent", "long_term_debt_total"):
                if f(c, 0) is not None and f(c, 1) is not None:
                    return f(c, back)
            return self.debt_fy(back)

        lev0, lev1 = _div(ltd(0), ta0), _div(ltd(1), ta1)
        cr0, cr1 = _div(f("current_assets"), f("current_liabilities")), _div(f("current_assets", 1), f("current_liabilities", 1))
        sh0 = f("diluted_shares") or f("shares_outstanding")
        sh1 = f("diluted_shares", 1) or f("shares_outstanding", 1)
        # Margin test: gross margin; companies without COGS use operating (EBIT) margin instead.
        gm0, gm1 = _div(self.gross_profit(), rev0), _div(self.gross_profit(1), rev1)
        margin_basis = "gross"
        if gm0 is None or gm1 is None:
            gm0, gm1 = _div(self.ebit_fy(0)[0], rev0), _div(self.ebit_fy(1)[0], rev1)
            margin_basis = "operating"
        at0, at1 = _div(rev0, ta1), _div(rev1, ta2)

        def cmp(a, b, test):
            return None if a is None or b is None else int(test(a, b))

        tests = {
            "roa_pos": None if roa0 is None else int(roa0 > 0),
            "cfo_pos": None if cfo0 is None else int(cfo0 > 0),
            "roa_up": cmp(roa0, roa1, lambda a, b: a > b),
            "cfo_gt_ni": cmp(cfo0, ni0, lambda a, b: a > b),
            "leverage_down": cmp(lev0, lev1, lambda a, b: a < b or (a == 0 and b == 0)),
            "current_ratio_up": cmp(cr0, cr1, lambda a, b: a > b),
            "no_dilution": cmp(sh0, sh1, lambda a, b: a <= b),
            "margin_up": cmp(gm0, gm1, lambda a, b: a > b),
            "asset_turnover_up": cmp(at0, at1, lambda a, b: a > b),
        }
        have = {k: v for k, v in tests.items() if v is not None}
        self.m["f_points"] = sum(have.values())
        self.m["f_tests"] = len(have)
        self.m["f_score"] = self.m["f_points"] if len(have) == 9 else None
        # Scaled to a 0-9 scale when >= PIOTROSKI_MIN_TESTS criteria ran; otherwise low confidence.
        self.m["f_ratio"] = self.m["f_points"] / len(have) if have else None
        self.m["f_score_scaled"] = (9 * self.m["f_ratio"] if len(have) >= PIOTROSKI_MIN_TESTS else None)
        self.m["f_confidence"] = "normal" if len(have) >= PIOTROSKI_MIN_TESTS else "low"
        self.m["f_margin_basis"] = margin_basis
        self.m["f_detail"] = " ".join(f"{k}={'-' if v is None else v}" for k, v in tests.items())
        if len(have) < 9:
            self.notes.append("f_score: missing " + ",".join(k for k, v in tests.items() if v is None))

    def beneish(self) -> None:
        f = self.cd.fy
        rev0, rev1 = f("revenue"), f("revenue", 1)
        rec0, rec1 = f("receivables"), f("receivables", 1)
        gp0, gp1 = self.gross_profit(), self.gross_profit(1)
        ca0, ca1 = f("current_assets"), f("current_assets", 1)
        ppe0, ppe1 = f("ppe_net"), f("ppe_net", 1)
        sec0, sec1 = self.fy("long_term_investments"), self.fy("long_term_investments", 1)
        ta0, ta1 = f("total_assets"), f("total_assets", 1)
        dep0, dep1 = f("depreciation_amortization"), f("depreciation_amortization", 1)
        sga0, sga1 = self.sga(), self.sga(1)
        cl0, cl1 = f("current_liabilities"), f("current_liabilities", 1)
        ltd0 = f("long_term_debt_noncurrent") if f("long_term_debt_noncurrent") is not None else self.debt_fy(0)
        ltd1 = f("long_term_debt_noncurrent", 1) if f("long_term_debt_noncurrent", 1) is not None else self.debt_fy(1)
        ni0, cfo0 = f("net_income"), f("operating_cash_flow")

        def aq(ca, ppe, sec, ta):
            return None if None in (ca, ppe, ta) or not ta else 1 - (ca + ppe + (sec or 0)) / ta

        def dep_rate(dep, ppe):
            return None if dep is None or ppe is None or dep + ppe == 0 else dep / (dep + ppe)

        def lev(cl, ltd, ta):
            return None if None in (cl, ltd, ta) or not ta else (cl + ltd) / ta

        idx = {
            "dsri": _div(_div(rec0, rev0), _div(rec1, rev1)),
            "gmi": _div(_div(gp1, rev1), _div(gp0, rev0)),
            "aqi": _div(aq(ca0, ppe0, sec0, ta0), aq(ca1, ppe1, sec1, ta1)),
            "sgi": _div(rev0, rev1),
            "depi": _div(dep_rate(dep1, ppe1), dep_rate(dep0, ppe0)),
            "sgai": _div(_div(sga0, rev0), _div(sga1, rev1)),
            "lvgi": _div(lev(cl0, ltd0, ta0), lev(cl1, ltd1, ta1)),
            "tata": _div(ni0 - cfo0, ta0) if ni0 is not None and cfo0 is not None else None,
        }
        missing = [k for k, v in idx.items() if v is None]
        score = BENEISH_INTERCEPT + sum(BENEISH_WEIGHTS[k] * (1.0 if v is None else v) for k, v in idx.items())
        self.m["m_score"] = score if not missing else None
        # tata has no neutral value, so it can't be imputed
        imputable = len(missing) <= BENEISH_MAX_IMPUTED and "tata" not in missing
        self.m["m_score_imputed"] = score if imputable else None
        self.m["m_missing"] = " ".join(missing)
        # M&A exception input: cash acquisitions this year / beginning-of-year total assets
        # (end-of-year assets are inflated by the acquisition itself).
        self.m["acquisitions_to_assets"] = _div(self.fy("acquisitions"), ta1)
        self.m["m_detail"] = " ".join(f"{k}={'-' if v is None else round(v, 3)}" for k, v in idx.items())
        if missing:
            self.notes.append("m_score: missing " + ",".join(missing))

    def shareholder_yield(self) -> None:
        m = self.m
        mcap = m.get("market_cap")
        div, buy, iss = self.ttm("dividends_paid"), self.ttm("buybacks"), self.ttm("stock_issuance")
        if self.model == "bank":
            paydown = 0.0  # bank debt is operating funding, not financing (same logic as Rule 1)
        else:
            d0, d1 = self.debt_fy(0), self.debt_fy(1)
            paydown = d1 - d0 if d0 is not None and d1 is not None else None
        m["dividend_yield"] = _div(div, mcap)
        m["net_buyback_yield"] = _div((buy or 0) - (iss or 0), mcap) if buy is not None else None
        m["debt_paydown_yield"] = _div(paydown, mcap)
        parts = [m["dividend_yield"], m["net_buyback_yield"], m["debt_paydown_yield"]]
        m["shareholder_yield"] = sum(parts) if None not in parts else None
        if m["shareholder_yield"] is None:
            self.notes.append("shareholder_yield: missing " + ",".join(
                n for n, v in zip(["dividends", "buybacks", "debt_paydown"], parts) if v is None))

    def rd_capitalization(self) -> None:
        rd = [self.fy("rd_expense", k) for k in range(RD_LIFE_YEARS + 1)]
        n = sum(v is not None for v in rd)
        rd = [v or 0.0 for v in rd]
        asset = sum(rd[k] * (RD_LIFE_YEARS - k) / RD_LIFE_YEARS for k in range(RD_LIFE_YEARS))
        amort = sum(rd[k] / RD_LIFE_YEARS for k in range(1, RD_LIFE_YEARS + 1))
        ebit, _ = self.ebit_fy(0)
        self.m.update(rd_asset=asset, rd_amortization=amort, rd_years=n,
                      ebit_rd_adj=ebit + rd[0] - amort if ebit is not None else None)
        if n < RD_LIFE_YEARS + 1 and any(rd):
            self.notes.append(f"rd_capitalized: only {n} of {RD_LIFE_YEARS + 1} years of R&D")

    def roic_reinvestment(self) -> None:
        m, f = self.m, self.cd.fy
        ebit, _ = self.ebit_fy(0)
        pretax, tax = f("pretax_income"), f("income_tax")
        rate = (min(max(tax / pretax, 0.0), MAX_TAX_RATE) if pretax and pretax > 0 and tax is not None
                else DEFAULT_TAX_RATE)

        def ic(back):
            eq, debt = f("stockholders_equity", back), self.debt_fy(back)
            cash = f("cash", back)
            if None in (eq, debt, cash):
                return None
            return eq + debt + (self.fy("minority_interest", back) or 0) - cash - (self.fy("short_term_investments", back) or 0)

        def nwc(back):
            ca, cl, cash = f("current_assets", back), f("current_liabilities", back), f("cash", back)
            if None in (ca, cl, cash):
                return None
            sti = self.fy("short_term_investments", back) or 0
            st_debt = f("debt_current_incl_short_term", back)
            if st_debt is None:
                st_debt = (f("long_term_debt_current", back) or 0) + (self.fy("short_term_borrowings", back) or 0)
            return (ca - cash - sti) - (cl - st_debt)

        ic0, ic1 = ic(0), ic(1)
        avg_ic = (ic0 + ic1) / 2 if ic0 is not None and ic1 is not None else ic0
        nopat = ebit * (1 - rate) if ebit is not None else None
        n0, n1 = nwc(0), nwc(1)
        d_nwc = n0 - n1 if n0 is not None and n1 is not None else 0.0
        if n0 is None or n1 is None:
            self.notes.append("reinvestment: working capital unavailable, change assumed 0")
        capex, da = f("capex"), f("depreciation_amortization")
        reinvest = capex - da + d_nwc if capex is not None and da is not None else None

        m["tax_rate"] = rate
        m["roic"] = _div(nopat, avg_ic) if avg_ic and avg_ic > 0 else None
        m["reinvestment_rate"] = _div(reinvest, nopat) if nopat and nopat > 0 else None
        m["growth_roic_x_rr"] = (m["roic"] * m["reinvestment_rate"]
                                 if m["roic"] is not None and m["reinvestment_rate"] is not None else None)
        # R&D-adjusted versions
        rd_net = (self.fy("rd_expense") or 0) - m.get("rd_amortization", 0)
        ebit_adj = m.get("ebit_rd_adj")
        nopat_adj = ebit_adj * (1 - rate) if ebit_adj is not None else None
        avg_ic_adj = avg_ic + m.get("rd_asset", 0) if avg_ic is not None else None
        m["roic_rd_adj"] = _div(nopat_adj, avg_ic_adj) if avg_ic_adj and avg_ic_adj > 0 else None
        m["reinvestment_rate_rd_adj"] = (_div(reinvest + rd_net, nopat_adj)
                                         if reinvest is not None and nopat_adj and nopat_adj > 0 else None)
        m["growth_rd_adj"] = (m["roic_rd_adj"] * m["reinvestment_rate_rd_adj"]
                              if m["roic_rd_adj"] is not None and m["reinvestment_rate_rd_adj"] is not None else None)
        if m["roic"] is None:
            self.notes.append("roic: missing EBIT, invested capital, or IC <= 0")
        if reinvest is None:
            self.notes.append("reinvestment_rate: missing capex or D&A")

    # ---- financials and REITs ----
    def _avg_with_year_ago(self, now: float | None, concepts_sign: list[tuple[str, int]]) -> float | None:
        if now is None or not self.cd.latest_bs_date:
            return now
        total = 0.0
        for concept, sign in concepts_sign:
            v = self.cd.instant_one_year_before(concept, self.cd.latest_bs_date)
            if v is None:
                if concept in ABSENT_MEANS_ZERO:
                    v = 0.0
                else:
                    return now
            total += sign * v
        return (now + total) / 2

    def bank(self) -> None:
        m = self.m
        eq = self.bs("stockholders_equity")
        parts = [("stockholders_equity", 1), ("preferred_stock", -1), ("goodwill", -1), ("intangibles_ex_goodwill", -1)]
        tce = None if eq is None else sum(sign * (self.bs(c) or 0) for c, sign in parts)
        ni = self.ttm("net_income")
        m["tbv"] = tce
        m["p_tbv"] = _div(m.get("market_cap"), tce) if tce and tce > 0 else None
        avg_tce = self._avg_with_year_ago(tce, parts)
        m["rotce"] = _div(ni, avg_tce) if avg_tce and avg_tce > 0 else None
        if m["p_tbv"] is None or m["rotce"] is None:
            self.notes.append("bank: missing equity, TBV <= 0, or market cap")

    def insurer(self) -> None:
        m = self.m
        eq = self.bs("stockholders_equity")
        parts = [("stockholders_equity", 1), ("preferred_stock", -1)]
        bv = None if eq is None else eq - (self.bs("preferred_stock") or 0)
        ni = self.ttm("net_income")
        m["book_value"] = bv
        m["p_b"] = _div(m.get("market_cap"), bv) if bv and bv > 0 else None
        avg_bv = self._avg_with_year_ago(bv, parts)
        m["roe"] = _div(ni, avg_bv) if avg_bv and avg_bv > 0 else None
        if m["p_b"] is None or m["roe"] is None:
            self.notes.append("insurer: missing equity, book value <= 0, or market cap")

    def reit(self) -> None:
        m = self.m
        ni, da = self.ttm("net_income"), self.ttm("depreciation_amortization")
        gains = self.cd.ttm("gain_on_property_sales").value  # absence is NOT zero (rule)
        m["ffo_ttm"] = ni + da - gains if None not in (ni, da, gains) else None
        years = []
        for back in range(3):
            parts = [self.cd.fy("net_income", back), self.cd.fy("depreciation_amortization", back),
                     self.cd.fy("gain_on_property_sales", back)]
            if None not in parts:
                years.append(parts[0] + parts[1] - parts[2])
        m["ffo_3y_avg"] = sum(years) / 3 if len(years) == 3 else None
        m["p_ffo"] = _div(m.get("market_cap"), m["ffo_ttm"]) if m["ffo_ttm"] and m["ffo_ttm"] > 0 else None
        if m["ffo_ttm"] is None:
            self.notes.append("p_ffo: missing " + ",".join(
                n for n, v in [("net_income", ni), ("d&a", da), ("gain_on_property_sales", gains)] if v is None))


def compute(cd: CompanyData, model: str, price: float | None, price_date: str | None) -> tuple[dict, list[str]]:
    c = Calc(cd, model, price, price_date)
    c.valuation_base()
    metrics = MODEL_RULES[model]["metrics"]
    if model == "standard":
        c.ev_ebit()
        c.rd_capitalization()
    if "gp_assets" in metrics:
        c.gp_assets()
    if "sloan_accruals" in metrics:
        c.sloan()
    if "piotroski_f" in metrics:
        c.piotroski()
    if "beneish_m" in metrics:
        c.beneish()
    if "shareholder_yield" in metrics:
        c.shareholder_yield()
    if "roic_reinvestment" in metrics:
        c.roic_reinvestment()
    if model == "bank":
        c.bank()
    elif model == "insurer":
        c.insurer()
    elif model == "reit":
        c.reit()
    # Corporate-action materiality inputs: acquisitions vs pre-event assets; revenue restatement
    # (a disposed business moved to discontinued operations shrinks restated revenue).
    c.m["acquisitions_ttm_to_assets"] = _div(c.ttm("acquisitions"), cd.fy("total_assets"))
    ratio = cd.ttm("revenue").restatement_ratio
    c.m["revenue_restated_share_removed"] = 1 - ratio if ratio is not None else None
    c.m["fy_end"] = cd.fy_end()
    return c.m, c.notes
