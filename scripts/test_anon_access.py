"""Verify the dashboard's tables and views are unreadable/unwritable when signed out.

Part A (always): connect as postgres and switch to the `anon` role, which is exactly what the
Supabase REST API (PostgREST) does for a request carrying only the anon key; SELECT and INSERT
must fail with permission denied or return zero rows. As a sanity check, the `authenticated` role
must be able to read but not write.

Part B (if SUPABASE_ANON_KEY is set): real HTTP requests to the REST API with only the anon key
(no user session): each relation must return 401/403/permission error or zero rows, and writes
must be rejected.

Usage: python scripts/test_anon_access.py      (exit code 1 if anything is readable or writable)
"""
import os
import sys
from pathlib import Path

import psycopg
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import require  # noqa: E402
from screener.db import connect  # noqa: E402

RELATIONS = ["latest_screen", "llm_latest", "valuations_latest", "scores_latest", "screen_history", "filing_flags",
             "runs", "metrics_latest", "llm_scores", "universe", "facts", "fundamentals_summary", "prices",
             "pipeline_status", "schema_migrations"]


def as_role(conn, role: str, sql: str):
    """Run sql as role inside a rolled-back transaction; return ('rows', n) or ('denied', message)."""
    try:
        with conn.transaction():
            conn.execute(f"set local role {role}")
            n = conn.execute(sql).fetchone()[0] if sql.lstrip().lower().startswith("select") else conn.execute(sql).rowcount
            raise _Rollback(n)
    except _Rollback as r:
        return "rows", r.n
    except psycopg.errors.InsufficientPrivilege as exc:
        return "denied", str(exc).splitlines()[0]
    except psycopg.Error as exc:
        return "error", str(exc).splitlines()[0]


class _Rollback(Exception):
    def __init__(self, n):
        self.n = n


def part_a() -> bool:
    ok = True
    print("Part A: database roles (anon = signed-out REST request)")
    print(f"  {'relation':22s} {'anon SELECT':34s} {'anon INSERT':22s} {'authenticated SELECT':22s} authenticated write")
    with connect() as conn:
        for rel in RELATIONS:
            a_sel = as_role(conn, "anon", f"select count(*) from public.{rel}")
            a_ins = as_role(conn, "anon", f"insert into public.{rel} default values")
            u_sel = as_role(conn, "authenticated", f"select count(*) from public.{rel}")
            u_del = as_role(conn, "authenticated", f"delete from public.{rel}")
            anon_safe = (a_sel[0] != "rows" or a_sel[1] == 0) and a_ins[0] != "rows"
            write_safe = u_del[0] != "rows" or u_del[1] == 0
            ok &= anon_safe and write_safe
            fmt = lambda r: f"{r[0]} ({r[1]} rows)" if r[0] == "rows" else r[0]
            print(f"  {rel:22s} {fmt(a_sel):34s} {fmt(a_ins):22s} {fmt(u_sel):22s} {fmt(u_del)}"
                  f"{'' if anon_safe and write_safe else '   <-- EXPOSED'}")
    return ok


def part_b() -> bool | None:
    key = os.getenv("SUPABASE_ANON_KEY")
    if not key:
        print("\nPart B: skipped (set SUPABASE_ANON_KEY in .env to the project's anon/publishable key)")
        return None
    base = require("SUPABASE_URL").rstrip("/") + "/rest/v1"
    # New-style publishable keys (sb_publishable_...) are not JWTs and go only in the apikey header;
    # legacy anon keys are JWTs and are also sent as the bearer token.
    headers = {"apikey": key} if key.startswith("sb_") else {"apikey": key, "Authorization": f"Bearer {key}"}
    print(f"\nPart B: HTTP with the {'publishable' if key.startswith('sb_') else 'legacy anon'} key only (signed out)")
    ok = True
    for rel in RELATIONS:
        r = requests.get(f"{base}/{rel}", headers=headers, params={"select": "*", "limit": 5}, timeout=30)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        rows = len(body) if isinstance(body, list) else None
        w = requests.post(f"{base}/{rel}", headers={**headers, "Content-Type": "application/json"}, json={}, timeout=30)
        read_safe = r.status_code in (401, 403, 404) or rows == 0 or (isinstance(body, dict) and "code" in body)
        write_safe = w.status_code >= 400
        ok &= read_safe and write_safe
        detail = f"{rows} rows" if rows is not None else (body.get("message", "")[:55] if isinstance(body, dict) else "")
        try:
            w_msg = w.json().get("message", "")[:55]
        except ValueError:
            w_msg = w.text[:55]
        print(f"  {rel:22s} GET {r.status_code} {detail:56s} POST {w.status_code} {w_msg}"
              f"{'' if read_safe and write_safe else '   <-- EXPOSED'}")
    return ok


def main() -> None:
    a = part_a()
    b = part_b()
    passed = a and b is not False
    print(f"\nResult: {'PASS' if passed else 'FAIL'} (Part A {'pass' if a else 'FAIL'}; "
          f"Part B {'skipped' if b is None else 'pass' if b else 'FAIL'})")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
