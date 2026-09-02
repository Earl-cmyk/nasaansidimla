import os
import warnings
from pathlib import Path

from dotenv import load_dotenv
import psycopg

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if "?pgbouncer=true" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("?pgbouncer=true", 1)[0]
if "connect_timeout=" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL + "?connect_timeout=5" if "?" not in DATABASE_URL else DATABASE_URL + "&connect_timeout=5"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured. Add the Supabase/Postgres connection string to the .env file.")
    conn = psycopg.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def initialize_db():
    if not DATABASE_URL:
        warnings.warn("DATABASE_URL is not configured; skipping Postgres schema initialization.")
        return
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
            with conn.cursor() as cursor:
                cursor.execute(schema_sql)
    except Exception as exc:  # pragma: no cover - environment-dependent startup guard
        warnings.warn(f"Postgres initialization skipped because the database is unavailable: {exc}")


initialize_db()