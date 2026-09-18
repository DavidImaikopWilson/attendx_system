"""
config.py — Application Configuration
======================================
Modify DATABASE_URL and SECRET_KEY before deployment.
"""

import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # -------------------------------------------------------
    # Core Flask settings
    # -------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production-use-a-long-random-string")

    # -------------------------------------------------------
    # Database — swap to your PostgreSQL or MySQL URL in .env
    # PostgreSQL:  postgresql://user:pass@localhost:5432/attendance_db
    # MySQL:       mysql+pymysql://user:pass@localhost:3306/attendance_db
    # SQLite:      sqlite:///attendance.db  (development only)
    # -------------------------------------------------------
    _db_url = os.environ.get("DATABASE_URL", "sqlite:///attendance.db")
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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
