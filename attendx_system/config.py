"""
config.py — Application Configuration
======================================
Modify DATABASE_URL and SECRET_KEY before deployment.
"""

import os
from dotenv import load_dotenv
from sqlalchemy.pool import NullPool

load_dotenv()


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///attendance.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _on_vercel() -> bool:
    return os.environ.get("VERCEL") == "1"


class Config:
    # -------------------------------------------------------
    # Core Flask settings
    # -------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production-use-a-long-random-string")

    # -------------------------------------------------------
    # Database — swap to your PostgreSQL or MySQL URL in .env
    # PostgreSQL:  postgresql://user:pass@localhost:5432/attendance_db
    # MySQL:       mysql+pymysql://user:pass@localhost:3306/attendance_db
    # SQLite:      sqlite:///attendance.db  (development only — not for Vercel)
    # -------------------------------------------------------
    _db_url = _database_url()
    _vercel = _on_vercel()

    if _vercel and _db_url.startswith("sqlite"):
        raise RuntimeError(
            "Set DATABASE_URL to hosted Postgres on Vercel. "
            "SQLite does not persist on the serverless filesystem."
        )

    if _vercel and _db_url.startswith("postgresql") and "sslmode=" not in _db_url:
        _db_url += ("&" if "?" in _db_url else "?") + "sslmode=require"

    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = (
        {"poolclass": NullPool, "pool_pre_ping": True}
        if _vercel
        else {"pool_pre_ping": True}
    )

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _vercel or os.environ.get(
        "SESSION_COOKIE_SECURE", ""
    ).lower() in ("1", "true", "yes")

    # -------------------------------------------------------
    # Geofencing — fixed system-wide radius in metres
    # -------------------------------------------------------
    GEOFENCE_RADIUS_METRES = 50

    # -------------------------------------------------------
    # GPS accuracy threshold — reject readings worse than this
    # -------------------------------------------------------
    MAX_GPS_ACCURACY_METRES = 500

    # -------------------------------------------------------
    # Session duration limits (minutes)
    # -------------------------------------------------------
    MIN_SESSION_DURATION = 5
    MAX_SESSION_DURATION = 60

    # -------------------------------------------------------
    # Maximum number of times a single schedule can be postponed
    # -------------------------------------------------------
    MAX_SCHEDULE_POSTPONES = 2
