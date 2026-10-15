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
    """Return the database URL, with the driver named explicitly.

    A bare `postgresql://` URL does not say which DBAPI to use, so SQLAlchemy
    picks one -- and which one it picks is a property of the installed
    SQLAlchemy version, not of this project. That took production down on
    2026-10-14: SQLAlchemy is not pinned (Flask-SQLAlchemy 3.1.1 asks only for
    `sqlalchemy>=2.0.16`), so one build resolved a version routing
    `postgresql://` to psycopg2, which is installed, and a later build resolved
    one routing it to psycopg (v3), which is not:

        File ".../sqlalchemy/dialects/postgresql/psycopg.py", line 497
          import psycopg
        ModuleNotFoundError: No module named 'psycopg'

    `db = SQLAlchemy(app)` is at module scope in app.py, so that killed the
    import before any route existed and every path returned 500, including
    /login. Nothing in the application code was involved -- reverting it did
    not help, because it was never the cause.

    Naming the driver here removes the choice. `postgresql+psycopg2://` always
    means psycopg2, which requirements.txt pins. The pin and this suffix defend
    independently: a version bump alone cannot reroute the dialect, and a
    DATABASE_URL written with some other driver suffix cannot either -- which
    matters because the URL comes from the Neon integration, not from this repo.
    """
    url = os.environ.get("DATABASE_URL", "sqlite:///attendance.db")

    # Heroku-style scheme that SQLAlchemy dropped support for.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    # Normalise any Postgres URL -- however it names (or fails to name) its
    # driver -- onto the one driver this project actually installs.
    if url.startswith("postgresql"):
        scheme, sep, rest = url.partition("://")
        if sep:
            url = "postgresql+psycopg2" + sep + rest

    return url


def _on_vercel() -> bool:
    return os.environ.get("VERCEL") == "1"


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
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production-use-a-long-random-string")

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
