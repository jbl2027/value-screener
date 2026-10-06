"""Apply supabase/migrations/*.sql in order, once each (tracked in schema_migrations)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from screener.db import connect  # noqa: E402

MIGRATIONS = ROOT / "supabase" / "migrations"


def main() -> None:
    with connect() as conn:
        conn.execute("create table if not exists schema_migrations "
                     "(name text primary key, applied_at timestamptz not null default now())")
        conn.execute("alter table schema_migrations enable row level security")
        applied = {r[0] for r in conn.execute("select name from schema_migrations").fetchall()}
        conn.commit()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in applied:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            print(f"applied {path.name}")
        print("migrations up to date")


if __name__ == "__main__":
    main()
