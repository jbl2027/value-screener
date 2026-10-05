"""Milestone 8: Gemini qualitative scoring of each company's latest 10-K.

Usage:
  python -u scripts/run_llm.py --tickers EIX,FSLR,...   test run -> data/llm_scores_test_<version>.csv
  python -u scripts/run_llm.py --all                     full run -> data/llm_scores_<version>.csv

Outputs (resumable; responses cached per company/accession/model/prompt version):
  data/llm_scores[_test]_<version>.csv     one row per company
  data/llm_results[_test]_<version>.jsonl  full model output (rationales, evidence) per company
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
    PROMPT_VERSION, DailyQuotaExhausted, Gemini, ServiceUnavailable, build_prompt, component_scores, llm_score,
    gate_review, qualitative_gate, risk_change, verify_evidence,
)
from screener.sec_client import SECClient  # noqa: E402

CACHE = DATA_DIR / "cache" / "llm"
# Fixed column order: error rows have fewer fields, and appending them unaligned shifts the
# error text into other columns (it once made failed rows look successful).
COLUMNS = ["as_of", "cik", "ticker", "model", "prompt_version", "accn", "filed", "period", "rf_chars",
           "mdna_chars", "cam_chars", "prior_cam_chars", "risk_added_share", "risk_similarity", "moat",
           "moat_lost_revenue_share", "management_candor", "risk_change_score", "cam_score", "new_risks",
           "new_risks_serious_company", "cam_count", "cam_new", "cam_unusual", "cam_prior_available",
           "cam_topics", "qualitative_gate", "gate_reasons", "gate_review", "sections_adequate", "dq_notes",
           "evidence_verified", "evidence_total", "llm_score", "prompt_tokens", "output_tokens", "error"]


def score_company(r, sec: SECClient, gemini: Gemini, as_of: str, dry_run: bool = False) -> tuple[dict, dict | None]:
    cik = int(r.cik)
    row = {"as_of": as_of, "cik": cik, "ticker": r.ticker, "model": gemini.model, "prompt_version": PROMPT_VERSION}
    filings = ten_k_filings(cik, sec, as_of, 2)
    if not filings:
        return {**row, "error": "no 10-K"}, None
    cur = filings[0]
    row.update(accn=cur["accn"], filed=cur["filed"], period=cur["period"])
    cache_file = CACHE / f"{r.ticker}_{cur['accn']}_{gemini.model}_{PROMPT_VERSION}.json"

    sections = extract_sections(fetch_text(cur, sec))
    prior = extract_sections(fetch_text(filings[1], sec)) if len(filings) > 1 else None
    change = risk_change(sections["risk_factors"], prior["risk_factors"]) \
        if prior and sections["risk_factors"] and prior["risk_factors"] else None
    row.update(rf_chars=len(sections["risk_factors"]), mdna_chars=len(sections["mdna"]),
               cam_chars=len(sections["cam"]), prior_cam_chars=len(prior["cam"]) if prior else 0,
               risk_added_share=change["added_share"] if change else None,
               risk_similarity=change["similarity"] if change else None)
    if not sections["risk_factors"] and not sections["mdna"]:
        return {**row, "error": "risk factors and MD&A not found"}, None

    prompt = build_prompt(r.name, r.ticker, r.gics_sub_industry, cur["filed"], sections, change,
                          prior["cam"] if prior else None)
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        result, usage = cached["result"], cached["usage"]
    elif dry_run:
        return {**row, "error": f"dry run: prompt {len(prompt):,} chars (~{len(prompt) // 4:,} tokens)"}, None
    else:
        result, usage = gemini.generate(prompt)
        CACHE.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"result": result, "usage": usage}), encoding="utf-8")

    verified, total = verify_evidence(result, prompt)
    comp = component_scores(result)
    gate = qualitative_gate(result)
    rc = result["risk_factor_change"]
    row.update(
        moat=comp["moat"], moat_lost_revenue_share=result["moat"]["revenue_share_affected_by_lost_advantage"],
        management_candor=comp["candor"], risk_change_score=comp["risk_change"], cam_score=comp["cam"],
        new_risks=len(rc["new_risks"]),
        new_risks_serious_company=sum(1 for x in rc["new_risks"] if x["scope"] == "company_specific"
                                      and x["severity"] in ("serious", "existential")),
        cam_count=comp["cam_count"], cam_new=comp["cam_new"], cam_unusual=comp["cam_unusual"],
        cam_prior_available=comp["cam_prior_available"],
        cam_topics="; ".join(c["topic"] for c in result["critical_audit_matters"]["cams"]),
        qualitative_gate=bool(gate), gate_reasons=" | ".join(gate), gate_review=" | ".join(gate_review(result)),
        sections_adequate=result["data_quality"]["sections_adequate"], dq_notes=result["data_quality"]["notes"],
        evidence_verified=verified, evidence_total=total, llm_score=llm_score(comp),
        prompt_tokens=usage.get("promptTokenCount"), output_tokens=usage.get("candidatesTokenCount"),
        error=None)
    return row, {"ticker": r.ticker, "accn": cur["accn"], "model": gemini.model, "prompt_version": PROMPT_VERSION,
                 "risk_added_share": row["risk_added_share"], "components": comp, "gate": gate, **result}


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tickers")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true", help="build prompts without calling Gemini")
    args = parser.parse_args()

    universe = pd.read_csv(DATA_DIR / "universe.csv").drop_duplicates("cik")
    if args.tickers:
        wanted = [t.strip().upper() for t in args.tickers.split(",")]
        universe = universe[universe["ticker"].isin(wanted)]
        suffix = "_test"
    else:
        suffix = ""
    # One file per prompt version: schemas differ between versions.
    scores_path = DATA_DIR / f"llm_scores{suffix}_{PROMPT_VERSION}.csv"
    results_path = DATA_DIR / f"llm_results{suffix}_{PROMPT_VERSION}.jsonl"
    done = set()
    if scores_path.exists():
        # Resume: drop failed rows (they are retried); skip companies done for this prompt version.
        prev = pd.read_csv(scores_path)
        # A successful row has the model name and a score; anything else is retried.
        ok = prev[prev["error"].isna() & prev["llm_score"].notna() & (prev["model"] == Gemini().model)]
        if len(ok) < len(prev):
            ok.to_csv(scores_path, index=False)
        done = set(ok.loc[ok["prompt_version"] == PROMPT_VERSION, "cik"])

    sec, gemini = SECClient(), Gemini()
    print(f"Model {gemini.model}, prompt {PROMPT_VERSION}; {len(universe)} companies, {len(done)} already done",
          flush=True)
    new, consecutive_errors = 0, 0
    for i, r in enumerate(universe.itertuples(), 1):
        if r.cik in done:
            continue
        if args.dry_run:
            row, _ = score_company(r, sec, gemini, args.as_of, dry_run=True)
            print(f"[{i}/{len(universe)}] {r.ticker}: RF {row.get('rf_chars')} MDA {row.get('mdna_chars')} "
                  f"CAM {row.get('cam_chars')} priorCAM {row.get('prior_cam_chars')} "
                  f"added {row.get('risk_added_share')}: {row.get('error')}", flush=True)
            continue
        try:
            row, full = score_company(r, sec, gemini, args.as_of)
        except (DailyQuotaExhausted, ServiceUnavailable) as exc:
            print(f"{exc}. Stopping; rerun later (quota resets at midnight Pacific) to resume "
                  f"({len(done) + new} of {len(universe)} done).", flush=True)
            return
        except Exception as exc:
            row, full = {"as_of": args.as_of, "cik": r.cik, "ticker": r.ticker, "prompt_version": PROMPT_VERSION,
                         "error": f"{type(exc).__name__}: {exc}"[:300]}, None
        pd.DataFrame([{c: row.get(c) for c in COLUMNS}]).to_csv(
            scores_path, mode="a", header=not scores_path.exists(), index=False)
        new += 0 if row.get("error") else 1
        consecutive_errors = consecutive_errors + 1 if row.get("error") else 0
        if consecutive_errors >= 2:
            print(f"Stopping after 2 consecutive errors (last: {row['error']}); fix and rerun to resume.", flush=True)
            return
        if full:
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(full) + "\n")
        status = row.get("error") or (
            f"moat {row['moat']} candor {row['management_candor']} risk {row['risk_change_score']} "
            f"cam {row['cam_score']} (n={row['cam_count']}, new={row['cam_new']}, unusual={row['cam_unusual']}) "
            f"score {row['llm_score']} gate {row['qualitative_gate']} evidence {row['evidence_verified']}/{row['evidence_total']}")
        print(f"[{i}/{len(universe)}] {r.ticker}: {status}", flush=True)


if __name__ == "__main__":
    main()
