"""Milestone 8: Gemini qualitative scoring of each company's latest 10-K.

Usage:
  python -u scripts/run_llm.py --tickers EIX,FSLR,...   test run -> data/llm_scores_test.csv
  python -u scripts/run_llm.py --all                     full run -> data/llm_scores.csv

Outputs (resumable; cached per company/accession/model/prompt version):
  data/llm_scores[_test].csv       one row per company: rubric scores, CAM flags, llm_score
  data/llm_results[_test].jsonl    full model output (rationales, evidence) per company
  data/cache/llm/*.json            raw cached responses (gitignored)
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import DATA_DIR  # noqa: E402
from screener.filings import extract_sections, fetch_text, ten_k_filings  # noqa: E402
from screener.llm import (  # noqa: E402
    PROMPT_VERSION, DailyQuotaExhausted, Gemini, build_prompt, llm_score, risk_change, verify_evidence,
)
from screener.sec_client import SECClient  # noqa: E402

CACHE = DATA_DIR / "cache" / "llm"


def score_company(ticker: str, cik: int, name: str, sec: SECClient, gemini: Gemini, as_of: str) -> tuple[dict, dict | None]:
    row = {"as_of": as_of, "cik": cik, "ticker": ticker, "model": gemini.model, "prompt_version": PROMPT_VERSION}
    filings = ten_k_filings(cik, sec, as_of, 2)
    if not filings:
        return {**row, "error": "no 10-K"}, None
    cur = filings[0]
    row.update(accn=cur["accn"], filed=cur["filed"], period=cur["period"])
    cache_file = CACHE / f"{ticker}_{cur['accn']}_{gemini.model}_{PROMPT_VERSION}.json"

    sections = extract_sections(fetch_text(cur, sec))
    prior = extract_sections(fetch_text(filings[1], sec)) if len(filings) > 1 else None
    change = risk_change(sections["risk_factors"], prior["risk_factors"]) \
        if prior and sections["risk_factors"] and prior["risk_factors"] else None
    row.update(rf_chars=len(sections["risk_factors"]), mdna_chars=len(sections["mdna"]),
               cam_chars=len(sections["cam"]),
               risk_similarity=change["similarity"] if change else None,
               risk_added_share=change["added_share"] if change else None)
    if not sections["risk_factors"] and not sections["mdna"]:
        return {**row, "error": "risk factors and MD&A not found"}, None

    prompt = build_prompt(name, ticker, cur["filed"], sections, change)
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        result, usage = cached["result"], cached["usage"]
    else:
        result, usage = gemini.generate(prompt)
        CACHE.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"result": result, "usage": usage}), encoding="utf-8")

    verified, total = verify_evidence(result, prompt)
    score, cam_flag = llm_score(result)
    cam = result["critical_audit_matters"]
    row.update(
        moat=result["moat"]["score"], management_candor=result["management_candor"]["score"],
        risk_factor_change=result["risk_factor_change"]["score"],
        cam_topics="; ".join(cam["topics"]), cam_revenue_recognition=cam["revenue_recognition"],
        cam_goodwill_impairment=cam["goodwill_or_intangible_impairment"], cam_risk_flag=cam_flag,
        sections_adequate=result["data_quality"]["sections_adequate"], dq_notes=result["data_quality"]["notes"],
        evidence_verified=verified, evidence_total=total, llm_score=round(score, 1),
        prompt_tokens=usage.get("promptTokenCount"), output_tokens=usage.get("candidatesTokenCount"),
        error=None)
    return row, {"ticker": ticker, "accn": cur["accn"], "model": gemini.model,
                 "prompt_version": PROMPT_VERSION, "risk_similarity": row["risk_similarity"], **result}


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tickers")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--as-of", default=date.today().isoformat())
    args = parser.parse_args()

    universe = pd.read_csv(DATA_DIR / "universe.csv").drop_duplicates("cik")
    if args.tickers:
        wanted = [t.strip().upper() for t in args.tickers.split(",")]
        universe = universe[universe["ticker"].isin(wanted)]
        suffix = "_test"
    else:
        suffix = ""
    scores_path = DATA_DIR / f"llm_scores{suffix}.csv"
    results_path = DATA_DIR / f"llm_results{suffix}.jsonl"
    done = set()
    if scores_path.exists():
        # Resume: keep successful rows only, so failed companies are retried.
        prev = pd.read_csv(scores_path)
        ok = prev[prev["error"].isna()] if "error" in prev else prev
        if len(ok) < len(prev):
            ok.to_csv(scores_path, index=False)
        done = set(ok["cik"])

    sec, gemini = SECClient(), Gemini()
    print(f"Model {gemini.model}, prompt {PROMPT_VERSION}; {len(universe)} companies, {len(done)} already done",
          flush=True)
    new = 0
    for i, r in enumerate(universe.itertuples(), 1):
        if r.cik in done:
            continue
        try:
            row, full = score_company(r.ticker, int(r.cik), r.name, sec, gemini, args.as_of)
        except DailyQuotaExhausted as exc:
            print(f"{exc}. Stopping; rerun after midnight Pacific to resume "
                  f"({len(done) + new} of {len(universe)} done).", flush=True)
            return
        except Exception as exc:
            row, full = {"as_of": args.as_of, "cik": r.cik, "ticker": r.ticker,
                         "error": f"{type(exc).__name__}: {exc}"[:300]}, None
        pd.DataFrame([row]).to_csv(scores_path, mode="a", header=not scores_path.exists(), index=False)
        new += 0 if row.get("error") else 1
        if full:
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(full) + "\n")
        status = row.get("error") or (f"moat {row['moat']} candor {row['management_candor']} "
                                      f"risk {row['risk_factor_change']} cam_flag {row['cam_risk_flag']} "
                                      f"score {row['llm_score']} evidence {row['evidence_verified']}/{row['evidence_total']}")
        print(f"[{i}/{len(universe)}] {r.ticker}: {status}", flush=True)


if __name__ == "__main__":
    main()
