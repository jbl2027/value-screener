"""Loads secrets from .env. Never print or log the values."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# VALUE_SCREENER_DATA redirects pipeline inputs/outputs (e.g. to test a Supabase round trip).
DATA_DIR = Path(os.getenv("VALUE_SCREENER_DATA") or ROOT / "data")


def require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
