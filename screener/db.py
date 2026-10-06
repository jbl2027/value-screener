"""Postgres connection to the Supabase project.

Uses the Supavisor session pooler (IPv4; supports COPY and transactions). The direct database
host is IPv6-only, which GitHub-hosted runners can't reach. The project ref comes from
SUPABASE_URL; override the pooler host with SUPABASE_DB_HOST if the project moves region.
"""
import os
from urllib.parse import urlparse

import psycopg

from screener.config import require

DEFAULT_POOLER_HOST = "aws-0-us-east-1.pooler.supabase.com"


def connect() -> psycopg.Connection:
    ref = urlparse(require("SUPABASE_URL")).hostname.split(".")[0]
    return psycopg.connect(
        host=os.getenv("SUPABASE_DB_HOST") or DEFAULT_POOLER_HOST,
        port=int(os.getenv("SUPABASE_DB_PORT") or 5432),
        user=f"postgres.{ref}",
        password=require("SUPABASE_DB_PASSWORD"),
        dbname="postgres",
        sslmode="require",
        connect_timeout=15,
    )
