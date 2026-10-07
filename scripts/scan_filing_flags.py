"""Rule-based gate detectors on each company's latest 10-K text: going concern, material weakness.

Each 10-K is scanned once (results in Supabase table filing_flags), so after the first pass only
new 10-Ks are fetched. Runs in the weekly workflow.

Usage: python -u scripts/scan_filing_flags.py [--recheck-flagged]
  --recheck-flagged  re-scan 10-Ks currently flagged (after a detector change)
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.db import connect  # noqa: E402
from screener.filings import fetch_text, ten_k_filings  # noqa: E402
from screener.gate_rules import text_detectors  # noqa: E402
from screener.sec_client import SECClient  # noqa: E402


def main() -> None:
    sec = SECClient()
    with connect() as conn:
        companies = conn.execute("select distinct on (cik) cik, ticker from universe order by cik, ticker").fetchall()
        where = "where not (going_concern or material_weakness)" if "--recheck-flagged" in sys.argv else ""
        done = {(c, a) for c, a in conn.execute(f"select cik, accn from filing_flags {where}").fetchall()}
        counts = {"scanned": 0, "up_to_date": 0, "no_10k": 0, "error": 0, "going_concern": 0, "material_weakness": 0}
        for i, (cik, ticker) in enumerate(companies, 1):
            try:
                filings = ten_k_filings(cik, sec, date.today().isoformat(), 1)
                if not filings:
                    counts["no_10k"] += 1
                    continue
                f = filings[0]
                if (cik, f["accn"]) in done:
                    counts["up_to_date"] += 1
                    continue
                d = text_detectors(fetch_text(f, sec))
            except Exception as exc:
                counts["error"] += 1
                print(f"[{i}] {ticker}: {type(exc).__name__}: {exc}"[:200], flush=True)
                continue
            conn.execute("insert into filing_flags (cik, ticker, accn, filed, going_concern, material_weakness, evidence) "
                         "values (%s, %s, %s, %s, %s, %s, %s) on conflict (cik, accn) do update set "
                         "going_concern = excluded.going_concern, material_weakness = excluded.material_weakness, "
                         "evidence = excluded.evidence, checked_at = now()",
                         (cik, ticker, f["accn"], f["filed"], d["going_concern"], d["material_weakness"],
                          " | ".join(d["evidence"]) or None))
            conn.commit()
            counts["scanned"] += 1
            counts["going_concern"] += d["going_concern"]
            counts["material_weakness"] += d["material_weakness"]
            if d["going_concern"] or d["material_weakness"]:
                print(f"[{i}] {ticker}: {' | '.join(d['evidence'])[:300]}", flush=True)
            if i % 50 == 0:
                print(f"  {i}/{len(companies)} {counts}", flush=True)
        print(f"Done: {counts}", flush=True)


if __name__ == "__main__":
    main()
