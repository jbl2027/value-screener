"""Supabase storage for Gemini 10-K scores (table llm_scores; migration 0002)."""
import json
import math

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

STORE_COLS = ["cik", "ticker", "accn", "filed", "period", "model", "prompt_version", "as_of", "moat",
              "moat_lost_revenue_share", "management_candor", "risk_change_score", "cam_score", "new_risks",
              "new_risks_serious_company", "cam_count", "cam_new", "cam_unusual", "cam_topics",
              "qualitative_gate", "gate_reasons", "gate_review", "risk_added_share", "evidence_verified",
              "evidence_total", "llm_score", "prompt_tokens", "output_tokens", "runs"]
JSON_COLS = ["run_scores"]  # stored with save(); not part of the latest_scores() CSV


def _clean(v):
    return None if isinstance(v, float) and math.isnan(v) else v


def scored_accessions(conn: psycopg.Connection, model: str, version: str) -> dict[int, set[str]]:
    """{cik: accessions already scored with this model and prompt version}."""
    out: dict[int, set[str]] = {}
    for cik, accn in conn.execute("select cik, accn from llm_scores where model = %s and prompt_version = %s",
                                  (model, version)).fetchall():
        out.setdefault(cik, set()).add(accn)
    return out


def save(conn: psycopg.Connection, row: dict, result: dict) -> None:
    dumps = lambda o: json.dumps(o, default=str)  # noqa: E731
    cols = STORE_COLS + JSON_COLS
    values = ([_clean(row.get("runs", 1) if c == "runs" else row.get(c)) for c in STORE_COLS]
              + [Jsonb(row.get(c), dumps=dumps) if row.get(c) is not None else None for c in JSON_COLS])
    conn.execute(
        f"insert into llm_scores ({', '.join(cols)}, result) values ({', '.join(['%s'] * (len(cols) + 1))}) "
        f"on conflict (cik, accn, model, prompt_version) do update set "
        f"{', '.join(f'{c} = excluded.{c}' for c in cols[3:])}, result = excluded.result, scored_at = now()",
        values + [Jsonb(result, dumps=dumps)])
    conn.commit()


def latest_scores(conn: psycopg.Connection, model: str, version: str) -> pd.DataFrame:
    """Latest score per company for this model and prompt version."""
    rows = conn.execute(
        f"select distinct on (cik) {', '.join(STORE_COLS)} from llm_scores "
        f"where model = %s and prompt_version = %s order by cik, filed desc nulls last, scored_at desc",
        (model, version)).fetchall()
    return pd.DataFrame(rows, columns=STORE_COLS)
