"""Resolve a company's total debt from tagged XBRL concepts.

Order of preference (first that applies wins):
  1. total_all          DebtLongtermAndShorttermCombinedAmount (already includes short-term)
  2. long_term_total    long-term debt incl. current maturities + short-term borrowings
  3. noncurrent+current balance-sheet noncurrent line + current line + short-term borrowings
                        (if the current line is DebtCurrent it already includes short-term)
  4. noncurrent_only    noncurrent line + short-term borrowings; current maturities assumed
                        zero (LOW confidence)
  5. notes_total        NotesPayable / SeniorNotes + short-term borrowings (LOW)
  6. parts_sum          sum of distinct instrument-level parts, one tag per overlap group (LOW)
  7. no_long_term_debt  no debt tags at all and negligible interest: short-term borrowings only
  otherwise             insufficient (debt likely reported under custom tags)

Sanity check for LOW-confidence results: implied interest rate = interest expense / debt.
Above MAX_IMPLIED_RATE, debt is probably missing, so the result is insufficient instead.
"""
from dataclasses import dataclass

CONFIDENCE_NORMAL = "normal"
CONFIDENCE_LOW = "low"

MAX_IMPLIED_RATE = 0.08
NO_DEBT_MAX_INTEREST_TO_ASSETS = 0.002
NO_DEBT_MAX_INTEREST_TO_REVENUE = 0.005  # catches balance-sheet-heavy filers (e.g. AMP)

# Known to carry debt under custom tags with no tagged interest expense: never infer zero debt.
HAS_UNTAGGED_DEBT = {
    217346,  # TXT (Textron)
}

NONCURRENT_PARTS = ("debt_part_convertible_noncurrent", "debt_part_notes_noncurrent",
                    "debt_part_secured_noncurrent", "debt_part_credit_facility",
                    "debt_part_other_noncurrent")
CURRENT_PARTS = ("debt_part_convertible_current", "debt_part_notes_current")
DEBT_CONCEPTS = ("debt_total_all", "long_term_debt_total", "long_term_debt_noncurrent",
                 "long_term_debt_current", "debt_current_incl_short_term", "notes_payable_total",
                 *NONCURRENT_PARTS, *CURRENT_PARTS)


@dataclass
class DebtResult:
    total: float | None
    basis: str
    confidence: str | None
    problem: str | None = None
    implied_rate: float | None = None

    @property
    def ok(self) -> bool:
        return self.total is not None and self.problem is None


def resolve_debt(cik: int, values: dict[str, float]) -> DebtResult:
    """values: {concept: value} as of one balance-sheet date (e.g. the latest 10-K)."""
    v = values.get
    st = v("short_term_borrowings") or 0
    interest = abs(v("interest_expense") or 0)

    def checked(total: float, basis: str) -> DebtResult:
        rate = interest / total if total > 0 and interest else None
        if rate is not None and rate > MAX_IMPLIED_RATE:
            return DebtResult(None, basis, None, f"implied_rate_{rate:.0%}_exceeds_{MAX_IMPLIED_RATE:.0%}", rate)
        return DebtResult(total, basis, CONFIDENCE_LOW, None, rate)

    if v("debt_total_all") is not None:
        return DebtResult(v("debt_total_all"), "total_all", CONFIDENCE_NORMAL)
    if v("long_term_debt_total") is not None:
        return DebtResult(v("long_term_debt_total") + st, "long_term_total", CONFIDENCE_NORMAL)

    noncurrent = v("long_term_debt_noncurrent")
    if noncurrent is not None:
        if v("long_term_debt_current") is not None:
            return DebtResult(noncurrent + v("long_term_debt_current") + st, "noncurrent+current",
                              CONFIDENCE_NORMAL)
        if v("debt_current_incl_short_term") is not None:
            return DebtResult(noncurrent + v("debt_current_incl_short_term"), "noncurrent+debt_current",
                              CONFIDENCE_NORMAL)
        current_parts = sum(v(c) or 0 for c in CURRENT_PARTS)
        if current_parts:
            return checked(noncurrent + current_parts + st, "noncurrent+current_parts")
        return checked(noncurrent + st, "noncurrent_only")

    if v("notes_payable_total") is not None:
        return checked(v("notes_payable_total") + st, "notes_total")

    parts = [v(c) for c in NONCURRENT_PARTS + CURRENT_PARTS if v(c) is not None]
    if parts:
        return checked(sum(parts) + st, "parts_sum")

    if cik not in HAS_UNTAGGED_DEBT:
        assets = v("total_assets") or 0
        revenue = max(v("revenue") or 0, 0)
        if assets and (interest == 0 or (interest <= NO_DEBT_MAX_INTEREST_TO_ASSETS * assets
                                         and interest <= NO_DEBT_MAX_INTEREST_TO_REVENUE * revenue)):
            return DebtResult(st, "no_long_term_debt", CONFIDENCE_NORMAL)

    return DebtResult(None, "none", None, "no_debt_tags_found")
