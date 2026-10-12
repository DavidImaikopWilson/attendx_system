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


# The value shipped in .env.example. Refused in production by _resolve_secret_key.
_PLACEHOLDER_SECRET_KEY = "change-this-in-production-use-a-long-random-string"


def _resolve_secret_key() -> str:
    """Return the signing key, refusing the shipped placeholder in production.

    Same fail-fast shape as _resolve_database_url, and for a sharper reason
    than usual. This key signs two separate things:

      * the Flask session cookie, so a known key forges `user_id` -- any
        lecturer, any admin;
      * the rotating QR token, HMAC-SHA256(SECRET_KEY, "session_id:window"),
        so a known key computes a valid token offline for any session without
        ever being in the room.

    The second one is new. Before Stage 4 the token was optional decoration and
    a default key cost little; Stage 4 made the token the thing that admits a
    student at an uncalibrated venue, which is every venue until Stage 5 lands.
    The placeholder being acceptable before that change and unacceptable after
    it is exactly the kind of drift this project keeps a log for.

    Local development is left alone -- a default key is the reason `flask run`
    works out of the box, and nothing on a developer's laptop is at risk.
    """
    key = os.environ.get("SECRET_KEY", _PLACEHOLDER_SECRET_KEY)
    if not _on_vercel():
        return key

    if key == _PLACEHOLDER_SECRET_KEY or not key.strip():
        raise RuntimeError(
            "Set SECRET_KEY in the Vercel project's environment variables. "
            "It is still the placeholder from .env.example, which is public: "
            "anyone can forge an admin session cookie and compute valid QR "
            "attendance tokens offline. Generate one with "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"`, "
            "then redeploy - Vercel reads environment variables at build time."
        )
    return key


# Hosts that only ever mean "this machine". On Vercel that is the function's
# own container, which has no database in it.
_LOCAL_HOSTS = ("@localhost", "@127.0.0.1", "@[::1]", "@0.0.0.0")


def _resolve_database_url() -> str:
    """Return the URL to use, refusing settings that cannot work on Vercel.

    Validation lives here rather than in the class body: a comprehension
    inside a class body gets its own scope and cannot read class attributes.
    """
    url = _database_url()
    if not _on_vercel():
        return url

    if url.startswith("sqlite"):
        raise RuntimeError(
            "Set DATABASE_URL to hosted Postgres on Vercel. "
            "SQLite does not persist on the serverless filesystem."
        )

    if any(host in url for host in _LOCAL_HOSTS):
        raise RuntimeError(
            "DATABASE_URL points at localhost, which on Vercel is this "
            "function's own container - nothing is listening there. "
            "This is the placeholder from .env.example; replace it with the "
            "connection string from your hosted Postgres provider "
            "(Vercel Storage, Neon, Supabase, ...)."
        )

    if "://username:password@" in url:
        raise RuntimeError(
            "DATABASE_URL still has the placeholder credentials "
            "'username:password' from .env.example. Use the real connection "
            "string from your hosted Postgres provider."
        )

    if url.startswith("postgresql") and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"

    return url


class Config:
    # -------------------------------------------------------
    # Core Flask settings
    # -------------------------------------------------------
    SECRET_KEY = _resolve_secret_key()

    # -------------------------------------------------------
    # Database — swap to your PostgreSQL or MySQL URL in .env
    # PostgreSQL:  postgresql://user:pass@localhost:5432/attendance_db
    # MySQL:       mysql+pymysql://user:pass@localhost:3306/attendance_db
    # SQLite:      sqlite:///attendance.db  (development only — not for Vercel)
    # -------------------------------------------------------
    _vercel = _on_vercel()
    _db_url = _resolve_database_url()

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