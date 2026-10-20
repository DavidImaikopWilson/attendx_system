"""
app.py — Location-Based Attendance System (Flask Backend)
==========================================================
Provides:
  - Authentication (login / logout)
  - Lecturer API  (start session, view records)
  - Student API   (list courses, mark attendance)
  - HTML page routes served via Jinja2 templates
"""

import math
import os
import sys
import json
import hmac
import hashlib
import calendar as month_calendar
from datetime import datetime, timedelta, date, time

import bcrypt
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, abort)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

# Vercel loads this file by path with the project root as the working
# directory, so this folder is not guaranteed to be on sys.path.
# Local runs (python app.py, python run_student.py) already have it.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from config import Config  # noqa: E402  (needs _BASE_DIR on sys.path first)

# ─────────────────────────────────────────────────────────────
# App & DB initialisation
# ─────────────────────────────────────────────────────────────

app = Flask(
    __name__,
    template_folder=os.path.join(_BASE_DIR, "templates"),
)
app.config.from_object(Config)
db = SQLAlchemy(app)


# DB still stores legacy ACTIVE/CLOSED in many environments.
# We expose OPEN/CLOSED in APIs while accepting both ACTIVE and OPEN as open states.
OPEN_DB_STATUSES = {"ACTIVE", "OPEN"}
CLOSED_DB_STATUS = "CLOSED"

# Geofence defaults for the set-gps endpoint
DEFAULT_RADIUS_M = 50      # metres — minimum / single-point radius
LOCATION_BUFFER_M = 15      # metres — extra buffer added around multi-point hull


def is_session_open(status: str | None) -> bool:
    return (status or "").upper() in OPEN_DB_STATUSES


def api_session_status(status: str | None) -> str:
    return "OPEN" if is_session_open(status) else "CLOSED"


def api_record_status(status: str | None) -> str:
    if not status:
        return "present"
    return "present" if status.upper() == "PRESENT" else status.lower()


_open_write_status_cache: str | None = None


def get_open_write_status() -> str:
    """
    Choose OPEN or ACTIVE for writes based on the current DB constraint.
    This keeps old ACTIVE/CLOSED databases functional while newer schemas
    use OPEN/CLOSED naming.
    """
    global _open_write_status_cache
    if _open_write_status_cache:
        return _open_write_status_cache

    try:
        checks = inspect(db.engine).get_check_constraints("attendance_sessions") or []
        for chk in checks:
            sqltext = (chk.get("sqltext") or "").upper()
            if "'ACTIVE'" in sqltext and "'OPEN'" not in sqltext:
                _open_write_status_cache = "ACTIVE"
                return _open_write_status_cache
            if "'OPEN'" in sqltext:
                _open_write_status_cache = "OPEN"
                return _open_write_status_cache
    except Exception:
        pass

    try:
        sample = db.session.execute(text("SELECT status FROM attendance_sessions LIMIT 1")).scalar()
        if isinstance(sample, str) and sample.upper() in OPEN_DB_STATUSES:
            _open_write_status_cache = sample.upper()
            return _open_write_status_cache
    except Exception:
        # Rollback, not a bare pass. Postgres aborts the whole transaction on a
        # failed statement, so swallowing this without rolling back leaves the
        # session poisoned for whatever the caller does next -- and the caller
        # here is the session-start path, which is about to INSERT. Same reason
        # _apply_ddl rolls back; same invisible-on-SQLite failure mode.
        db.session.rollback()

    _open_write_status_cache = "OPEN"
    return _open_write_status_cache


# ─────────────────────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(120), nullable=False)
    email         = db.Column(db.String(180), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(20),  nullable=False)  # 'student' | 'lecturer' | 'admin'
    is_superuser  = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    def set_password(self, password: str):
        self.password_hash = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt()
        ).decode()


class Course(db.Model):
    __tablename__ = "courses"
    id           = db.Column(db.Integer, primary_key=True)
    course_code  = db.Column(db.String(20),  unique=True, nullable=False)
    course_name  = db.Column(db.String(200), nullable=False)
    lecturer_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    location_lat = db.Column(db.Float, nullable=True, default=None)
    location_lng = db.Column(db.Float, nullable=True, default=None)
    radius       = db.Column(db.Float, nullable=True, default=None)   # geofence radius in metres
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    lecturer  = db.relationship("User", backref="courses")
    enrollments = db.relationship("Enrollment", backref="course", lazy="dynamic")
    sessions    = db.relationship("AttendanceSession", backref="course", lazy="dynamic")
    schedules   = db.relationship("CourseSchedule", backref="course", lazy="dynamic")


class Venue(db.Model):
    __tablename__ = "venues"
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(120), unique=True, nullable=False)
    latitude   = db.Column(db.Float, nullable=True, default=None)
    longitude  = db.Column(db.Float, nullable=True, default=None)
    radius     = db.Column(db.Float, nullable=True, default=None)   # geofence radius in metres
    # ── Building and floor (Stage 3) — nullable ──
    # These are NEVER used in distance maths. GNSS vertical error runs 15-50m
    # against a 3-4m floor spacing and is usually null indoors, browsers expose
    # no barometer, and H3 is strictly 2D — so a floor cannot be measured, only
    # declared. These two columns exist for exactly three jobs: detect venues
    # stacked above one another, label reports, and warn the admin at setup.
    building   = db.Column(db.String(80), nullable=True, default=None)
    floor      = db.Column(db.Integer, nullable=True, default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Learned fence (Stage 5) — all nullable ──
    # The §1.6 inversion: instead of trusting the admin's one walked circle, a
    # venue's fence is *learned* from QR-verified check-ins, the only data here
    # that corrects the systematic WiFi-fingerprint offset rather than merely
    # averaging out noise. These stay NULL until a venue has earned promotion
    # (CAL_MIN_* thresholds); is_calibrated() keys off them, and once set the
    # geofence reads these in place of latitude/longitude/radius. Never written
    # from anything but qr_trusted rows, always via median/percentile, and never
    # auto-applied past the displacement cap — see recompute_venue_calibration().
    cal_lat        = db.Column(db.Float, nullable=True, default=None)
    cal_lng        = db.Column(db.Float, nullable=True, default=None)
    cal_radius     = db.Column(db.Float, nullable=True, default=None)
    cal_n          = db.Column(db.Integer, nullable=True, default=None)
    cal_updated_at = db.Column(db.DateTime, nullable=True, default=None)

    # A calibration candidate that met the count thresholds but tripped a
    # poisoning guard (moved the centre > CAL_MAX_DISPLACEMENT_M, or wanted to
    # expand the radius past CAL_MAX_RADIUS_EXPANSION_FACTOR x the survey). Held
    # as JSON for an admin to apply or discard, never auto-applied — a large
    # disagreement means either the survey is wrong or something is being gamed,
    # and which of those it is is not a judgement this loop may make for itself.
    cal_review     = db.Column(db.Text, nullable=True, default=None)

    schedules = db.relationship("CourseSchedule", backref="venue", lazy="dynamic")


class CourseSchedule(db.Model):
    __tablename__ = "course_schedules"
    id         = db.Column(db.Integer, primary_key=True)
    course_id  = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    class_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=True)
    venue_id   = db.Column(db.Integer, db.ForeignKey("venues.id"), nullable=True)
    postpone_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("course_id", "class_date", "start_time"),)


class Enrollment(db.Model):
    __tablename__ = "enrollments"
    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    course_id  = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    enrolled_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("student_id", "course_id"),)
    student = db.relationship("User", backref="enrollments")


class AttendanceSession(db.Model):
    __tablename__ = "attendance_sessions"
    id         = db.Column(db.Integer, primary_key=True)
    course_id  = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    venue_id   = db.Column(db.Integer, db.ForeignKey("venues.id"), nullable=True)
    start_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    end_time   = db.Column(db.DateTime, nullable=False)
    status     = db.Column(db.String(10), nullable=False, default="OPEN")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    venue   = db.relationship("Venue")
    records = db.relationship("AttendanceRecord", backref="session", lazy="dynamic")

    def is_expired(self) -> bool:
        return datetime.utcnow() > self.end_time

    def seconds_remaining(self) -> int:
        delta = self.end_time - datetime.utcnow()
        return max(0, int(delta.total_seconds()))

    def close_if_expired(self):
        """Lazily close session if its window has passed."""
        if is_session_open(self.status) and self.is_expired():
            self.status = CLOSED_DB_STATUS
            db.session.commit()
            self._on_closed()

    def _on_closed(self, actor_id=None):
        """
        Hook: this session just transitioned OPEN -> CLOSED. Recompute its
        venue's learned fence from the check-ins now that the session is
        complete. Attributed to the course's lecturer when no explicit actor is
        given, because admin_logs.admin_id is NOT NULL and a lazy expiry has no
        human behind it. recompute never raises, so attendance is never put at
        risk by a calibration failure.
        """
        if actor_id is None and self.course is not None:
            actor_id = self.course.lecturer_id
        recompute_venue_calibration(self.venue, actor_id=actor_id)


class AttendanceRecord(db.Model):
    __tablename__ = "attendance_records"
    id         = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("attendance_sessions.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    timestamp  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    lat        = db.Column(db.Float, nullable=False)
    lng        = db.Column(db.Float, nullable=False)
    accuracy   = db.Column(db.Float, nullable=False)
    distance_m = db.Column(db.Float, nullable=False)
    status     = db.Column(db.String(10), nullable=False, default="PRESENT")

    # ── Multi-sample capture (Stage 1) — all nullable for old rows ──
    # lat/lng above hold the robust centroid; accuracy holds the raw
    # device-reported figure. acc_eff is the honest uncertainty actually
    # used by the geofence decision.
    sample_count = db.Column(db.Integer, nullable=True)
    spread_m     = db.Column(db.Float, nullable=True)
    acc_eff      = db.Column(db.Float, nullable=True)

    # ── Three-state verification (Stage 2) — also nullable ──
    # `status` above stays "PRESENT" forever: it is String(10), so
    # "PRESENT_UNVERIFIED" would not fit, and this database may carry CHECK
    # constraints on status columns. The verification outcome therefore lives
    # in its own columns and every existing report keeps working untouched.
    #   verify_state : INSIDE | AMBIGUOUS   (OUTSIDE is rejected, never stored)
    #   confidence   : 0.0–1.0, capped at an uncalibrated venue
    verify_state = db.Column(db.String(16), nullable=True)
    confidence   = db.Column(db.Float, nullable=True)

    # The geofence's own verdict, before anything downgraded or overrode it.
    # Stored because verify_state alone is lossy in both directions and the
    # two disagreements it hides are the ones worth auditing:
    #   geo_state=OUTSIDE, verify_state=AMBIGUOUS -> step 7b override fired
    #   geo_state=INSIDE,  verify_state=AMBIGUOUS -> a nearer venue contested it
    # Without this column an overridden row is byte-identical to a genuinely
    # fuzzy one, so neither a lecturer nor a later audit can tell a fix taken
    # 400m away from a fix taken at the edge of the room.
    geo_state    = db.Column(db.String(16), nullable=True)

    # ── QR ground truth (Stage 4) — nullable ──
    # qr_verified is the factual one: this submission carried a token and it
    # validated. Nothing more is claimed, because a projector QR is a shared
    # secret and a valid token does not attest to the fix sent alongside it.
    qr_verified  = db.Column(db.Boolean, nullable=True)

    # Stage 5's input population, and narrower on purpose. A true value means
    # *this device reported these coordinates while provably in that venue* and
    # the fix was close enough to the survey to be worth learning from. See
    # qr_is_calibration_grade() for the two exclusions and why neither costs
    # the §1.6 bootstrap anything. Stage 5 reads this column, never qr_verified.
    qr_trusted   = db.Column(db.Boolean, nullable=True)

    __table_args__ = (db.UniqueConstraint("session_id", "student_id"),)
    student = db.relationship("User", backref="attendance_records")


class AdminLog(db.Model):
    __tablename__ = "admin_logs"
    id          = db.Column(db.Integer, primary_key=True)
    admin_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action      = db.Column(db.String(60), nullable=False)
    target_type = db.Column(db.String(30), nullable=True)
    target_id   = db.Column(db.Integer, nullable=True)
    details     = db.Column(db.Text, nullable=True)  # JSON string
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    admin = db.relationship("User", backref="admin_logs")


# ─────────────────────────────────────────────────────────────
# DEMO SEED (runs once when the users table is empty)
# ─────────────────────────────────────────────────────────────

def ensure_default_venues() -> None:
    if Venue.query.count() > 0:
        return

    venues = (
        "Main Auditorium",
        "Engineering Block A",
        "Science Hall",
        "ICT Centre",
        "Library Annex",
    )
    for name in venues:
        db.session.add(Venue(name=name))
    db.session.commit()


def ensure_demo_schedules() -> None:
    if CourseSchedule.query.count() > 0:
        return

    courses = Course.query.order_by(Course.course_code.asc()).all()
    venues = Venue.query.order_by(Venue.id.asc()).all()
    if not courses or not venues:
        return

    venue_by_name = {v.name: v for v in venues}
    patterns = {
        # weekday: Monday=0 ... Sunday=6
        "CSC401": {
            "weekdays": {0, 2},
            "start_time": time(hour=9, minute=0),
            "venue": "Main Auditorium",
        },
        "CSC403": {
            "weekdays": {1, 4},
            "start_time": time(hour=11, minute=0),
            "venue": "Engineering Block A",
        },
        "CSC405": {
            "weekdays": {0, 3},
            "start_time": time(hour=14, minute=0),
            "venue": "Science Hall",
        },
    }
    fallback_venue = venues[0]
    today = date.today()

    for offset in range(0, 28):
        class_day = today + timedelta(days=offset)
        weekday = class_day.weekday()

        for course in courses:
            cfg = patterns.get(course.course_code)
            if cfg and weekday in cfg["weekdays"]:
                venue = venue_by_name.get(cfg["venue"], fallback_venue)
                db.session.add(CourseSchedule(
                    course_id=course.id,
                    class_date=class_day,
                    start_time=cfg["start_time"],
                    venue_id=venue.id,
                ))

    db.session.commit()

def seed_demo_if_empty() -> None:
    """Create demo users/courses once, then ensure venues and schedules exist."""
    seeded_demo = False

    if User.query.first() is None:
        demo_pw = "password123"
        amara = User(
            name="Dr. Amara Osei",
            email="amara.osei@university.edu",
            role="lecturer",
        )
        funmi = User(
            name="Dr. Funmi Adeyemi",
            email="funmi.adeyemi@university.edu",
            role="lecturer",
        )
        chidi = User(
            name="Chidi Nwosu", email="chidi.nwosu@student.edu", role="student"
        )
        amina = User(
            name="Amina Bello", email="amina.bello@student.edu", role="student"
        )
        tunde = User(
            name="Tunde Fashola",
            email="tunde.fashola@student.edu",
            role="student",
        )
        for u in (amara, funmi, chidi, amina, tunde):
            u.set_password(demo_pw)
            db.session.add(u)
        db.session.flush()

        c1 = Course(
            course_code="CSC401",
            course_name="Advanced Algorithms",
            lecturer_id=amara.id,
        )
        c2 = Course(
            course_code="CSC403",
            course_name="Computer Networks",
            lecturer_id=amara.id,
        )
        c3 = Course(
            course_code="CSC405",
            course_name="Database Management Systems",
            lecturer_id=funmi.id,
        )
        db.session.add_all((c1, c2, c3))
        db.session.flush()

        for sid, cid in (
            (chidi.id, c1.id),
            (chidi.id, c2.id),
            (chidi.id, c3.id),
            (amina.id, c1.id),
            (amina.id, c3.id),
            (tunde.id, c2.id),
            (tunde.id, c3.id),
        ):
            db.session.add(Enrollment(student_id=sid, course_id=cid))

        db.session.commit()
        seeded_demo = True

    # ── Seed default superuser if no admin user exists ──
    if not User.query.filter_by(role="admin").first():
        superuser = User(
            name="System Admin",
            email="admin@university.edu",
            role="admin",
            is_superuser=True,
        )
        superuser.set_password("password123")
        db.session.add(superuser)
        db.session.commit()
        print("[OK] Default superuser created - admin@university.edu / password123")

    ensure_default_venues()
    ensure_demo_schedules()

    if seeded_demo:
        print("[OK] Demo data loaded - e.g. amara.osei@university.edu / password123")


def _apply_ddl(label: str, ddl: str) -> bool:
    """
    Run one DDL statement in its own transaction, rolling back on failure.

    This exists because of a Postgres/SQLite difference that cost nothing
    locally and would have cost everything in production. Postgres aborts the
    entire transaction on a failed statement, and every later statement then
    raises InFailedSqlTransaction until somebody rolls back. The original code
    caught exceptions and `pass`ed *without* rolling back, so one DDL hiccup
    silently poisoned the session: the remaining ALTERs all failed into the
    same `pass`, and then seed_demo_if_empty() -- which runs at import, right
    after this function -- threw, which fails module import and returns 500
    for every route including /login.

    SQLite never showed this, because it does not abort transactions the same
    way. That is precisely the gap D12 warns about, and the reason the fix is
    structural rather than a bigger try block: one statement per transaction,
    rollback on failure, so a column that cannot be added costs exactly that
    column and nothing else.
    """
    try:
        db.session.execute(text(ddl))
        db.session.commit()
        return True
    except Exception as exc:                      # noqa: BLE001 - see docstring
        db.session.rollback()
        app.logger.warning("schema: could not apply %s (%s)", label, exc)
        return False


def ensure_schema_compatibility() -> None:
    """
    Keep older databases compatible with newer models without requiring
    a dedicated migration tool for this project.

    Every statement is independent (see _apply_ddl). Tables are also
    independent of each other: a table that cannot be inspected is skipped
    rather than ending the whole function, because the previous early `return`
    meant an unreadable `course_schedules` silently prevented all sixteen
    later columns from being added.
    """
    inspector = inspect(db.engine)

    def columns(table: str):
        """Column names for a table, or None when it cannot be inspected."""
        try:
            return {c["name"] for c in inspector.get_columns(table)}
        except Exception:
            return None

    # ── Create admin_logs table if missing ──
    try:
        AdminLog.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as exc:                      # noqa: BLE001
        db.session.rollback()
        app.logger.warning("schema: could not create admin_logs (%s)", exc)

    # Every column this application has added since the first release, grouped
    # by table. Nullable unless noted: an existing row genuinely has no value
    # for these and must not be given an invented one. `status` is deliberately
    # absent -- it is String(10) and may carry a CHECK constraint, so new
    # states go in their own columns rather than widening it.
    additions: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
        ("course_schedules", (
            ("postpone_count",
             "ALTER TABLE course_schedules "
             "ADD COLUMN postpone_count INTEGER NOT NULL DEFAULT 0"),
        )),
        ("courses", (
            ("radius",
             "ALTER TABLE courses ADD COLUMN radius FLOAT NOT NULL DEFAULT 50.0"),
        )),
        ("users", (
            ("is_superuser",
             "ALTER TABLE users ADD COLUMN is_superuser BOOLEAN NOT NULL DEFAULT FALSE"),
        )),
        ("venues", (
            ("radius",   "ALTER TABLE venues ADD COLUMN radius FLOAT"),
            # Stage 3 — building and floor. "floor" is a reserved-ish word in
            # some dialects, so it is quoted. Never used in distance maths.
            ("building", 'ALTER TABLE venues ADD COLUMN building VARCHAR(80)'),
            ("floor",    'ALTER TABLE venues ADD COLUMN "floor" INTEGER'),
            # Stage 5 — learned fence. cal_updated_at is TIMESTAMP so it is
            # portable to Postgres (D12); SQLite gives it NUMERIC affinity and
            # SQLAlchemy round-trips the datetime either way. cal_review is
            # JSON held as text, like admin_logs.details.
            ("cal_lat",        "ALTER TABLE venues ADD COLUMN cal_lat FLOAT"),
            ("cal_lng",        "ALTER TABLE venues ADD COLUMN cal_lng FLOAT"),
            ("cal_radius",     "ALTER TABLE venues ADD COLUMN cal_radius FLOAT"),
            ("cal_n",          "ALTER TABLE venues ADD COLUMN cal_n INTEGER"),
            ("cal_updated_at", "ALTER TABLE venues ADD COLUMN cal_updated_at TIMESTAMP"),
            ("cal_review",     "ALTER TABLE venues ADD COLUMN cal_review TEXT"),
        )),
        ("attendance_sessions", (
            ("venue_id",
             "ALTER TABLE attendance_sessions "
             "ADD COLUMN venue_id INTEGER REFERENCES venues(id)"),
        )),
        ("attendance_records", (
            # Stage 1 — multi-sample capture.
            ("sample_count", "ALTER TABLE attendance_records ADD COLUMN sample_count INTEGER"),
            ("spread_m",     "ALTER TABLE attendance_records ADD COLUMN spread_m FLOAT"),
            ("acc_eff",      "ALTER TABLE attendance_records ADD COLUMN acc_eff FLOAT"),
            # Stage 2 — three-state verification outcome.
            ("verify_state", "ALTER TABLE attendance_records ADD COLUMN verify_state VARCHAR(16)"),
            ("confidence",   "ALTER TABLE attendance_records ADD COLUMN confidence FLOAT"),
            # Stage 4 — did this submission carry a valid token.
            ("qr_verified",  "ALTER TABLE attendance_records ADD COLUMN qr_verified BOOLEAN"),
            # Stage 4 remediation — the raw geofence verdict, and the narrower
            # calibration-eligibility flag Stage 5 actually reads. Added rather
            # than backfilled: a pre-existing row genuinely does not know
            # whether it was overridden, and NULL says that honestly. Stage 5
            # must treat NULL as "not eligible", never as False meaning
            # "checked and rejected" -- see qr_is_calibration_grade() and DR-31.
            ("geo_state",    "ALTER TABLE attendance_records ADD COLUMN geo_state VARCHAR(16)"),
            ("qr_trusted",   "ALTER TABLE attendance_records ADD COLUMN qr_trusted BOOLEAN"),
        )),
    )

    for table, cols in additions:
        existing = columns(table)
        if existing is None:
            # The table itself is unreadable or absent. db.create_all() builds
            # every table from the models before this runs, so this means
            # something is genuinely wrong with the database -- skip this table
            # and let the others proceed.
            app.logger.warning("schema: cannot inspect %s, skipping its columns", table)
            continue
        for col, ddl in cols:
            if col not in existing:
                _apply_ddl(f"{table}.{col}", ddl)

# ─────────────────────────────────────────────────────────────
# GEOFENCING UTILITY
# ─────────────────────────────────────────────────────────────

def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Returns the great-circle distance in metres between two GPS points.
    Uses the Haversine formula.
    """
    R = 6_371_000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lng2 - lng1)

    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)

    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalise_samples(raw, fallback_lat=None, fallback_lng=None, fallback_acc=None):
    """
    Coerce a client-supplied `samples` list into a clean list of
    (lat, lng, accuracy) tuples.

    Backward compatible: when `raw` is missing or unusable, synthesises a
    one-element list from the legacy flat lat/lng/accuracy fields, so older
    clients keep working unchanged.

    Junk entries are dropped rather than raising -- a single malformed fix
    must not cost a student their attendance.
    """
    def _clean(lat, lng, acc):
        """
        The single validity test, used by *both* input paths.

        It lives in one place because having it on only one path was a real
        defect: the legacy flat fields were appended unchecked, so
        `"accuracy": "NaN"` survived -- float("NaN") is a perfectly valid
        float. Every later comparison against NaN then returns False, which
        silently switched off the step-6 accuracy cap *and* both geofence
        inequalities, so `d - a > r` could not fire and a fix 11km away was
        judged AMBIGUOUS instead of OUTSIDE. A validation gap on one path
        disabled the geofence for anyone using that path.

        NaN is rejected explicitly rather than left to the range check. The
        range check does catch it -- every comparison with NaN is False -- but
        relying on that is exactly the subtlety that caused the bug.
        """
        try:
            lat, lng, acc = float(lat), float(lng), float(acc)
        except (TypeError, ValueError):
            return None
        if any(math.isnan(v) or math.isinf(v) for v in (lat, lng, acc)):
            return None
        # Reject impossible coordinates and non-positive accuracy.
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return None
        if not (acc > 0):
            return None
        return (lat, lng, acc)

    out = []
    if isinstance(raw, list):
        for s in raw:
            if not isinstance(s, dict):
                continue
            cleaned = _clean(s.get("lat"), s.get("lng"), s.get("acc", s.get("accuracy")))
            if cleaned:
                out.append(cleaned)

    if not out and fallback_lat is not None:
        cleaned = _clean(fallback_lat, fallback_lng, fallback_acc)
        if cleaned:
            out.append(cleaned)

    return out


def _median(values: list[float]) -> float:
    """Plain median. Returns the lower-upper mean for even counts."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _percentile(values: list[float], p: float) -> float:
    """
    The p-th percentile (0-100) by linear interpolation between closest ranks,
    the same method numpy uses by default.

    Stage 5 takes the 90th percentile of per-record distances as a learned
    radius, deliberately not the max: one student who walked to the window
    while scanning must not set the fence for the room. Interpolation keeps the
    result continuous as records accumulate, so the fence does not jump when the
    30th check-in lands.
    """
    if not values:
        raise ValueError("_percentile requires at least one value")
    s = sorted(values)
    n = len(s)
    if n == 1:
        return float(s[0])
    rank = (p / 100.0) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


def robust_fix(samples: list[tuple[float, float, float]]) -> dict:
    """
    Collapse N GPS samples into one defensible position estimate.

    Returns {lat, lng, acc_eff, spread_m, sample_count, used_count}.

    Why each step exists:

      * The first sample is dropped when there are enough to spare. On
        Android the fused provider answers in a few hundred milliseconds
        with a coarse network estimate; the GNSS fix lands seconds later.
        The first fix is therefore systematically the worst one.

      * Samples whose reported accuracy is wildly worse than the median are
        dropped. That signature -- a sudden jump of hundreds of metres --
        is the device switching between GNSS and WiFi mid-capture.

      * The centre is the component-wise median, not the mean. One bad fix
        cannot drag it. The geometric median (Weiszfeld) would be marginally
        better but differs by well under a metre at room scale, and is
        iterative and harder to explain.

      * `acc_eff` is deliberately pessimistic: you cannot claim to be more
        accurate than your best single reported fix, and you cannot claim
        to be tighter than your own observed scatter.

    NOTE: there is intentionally no `a / sqrt(n)` improvement here. GNSS
    errors are time-correlated -- the same satellites and the same multipath
    geometry persist across a capture window -- so the independent-noise
    assumption behind that formula does not hold and would overstate
    confidence. And indoors the dominant error is a systematic WiFi
    fingerprint offset, which no amount of sampling reduces at all:
    agreement between samples measures precision, never accuracy.
    """
    n_total = len(samples)
    if n_total == 0:
        raise ValueError("robust_fix requires at least one sample")

    working = samples[1:] if n_total >= 3 else list(samples)

    med_acc = _median([s[2] for s in working])
    filtered = [s for s in working if s[2] <= 3.0 * med_acc]
    if filtered:
        working = filtered

    centre_lat = _median([s[0] for s in working])
    centre_lng = _median([s[1] for s in working])

    if len(working) >= 2:
        spread = _median([
            haversine_distance(s[0], s[1], centre_lat, centre_lng)
            for s in working
        ])
    else:
        spread = 0.0

    best_acc = min(s[2] for s in working)

    return {
        "lat":          centre_lat,
        "lng":          centre_lng,
        "acc_eff":      max(best_acc, spread),
        "spread_m":     spread,
        "sample_count": n_total,
        "used_count":   len(working),
    }


# ─────────────────────────────────────────────────────────────
# VERIFICATION DECISION (Stage 2)
# ─────────────────────────────────────────────────────────────

STATE_INSIDE    = "INSIDE"
STATE_AMBIGUOUS = "AMBIGUOUS"
STATE_OUTSIDE   = "OUTSIDE"

# Not a geofence outcome at all — the marker for a record a lecturer asserted
# by hand (Stage 4). It lives in the same column because it answers the same
# question a reader asks of that column: how do we know this student was here?
STATE_MANUAL    = "MANUAL"

# A venue whose fence has not been learned from verified check-ins cannot
# support a confident verdict, however tidy the samples look. See the note in
# geofence_decision().
UNCALIBRATED_CONFIDENCE_CEILING = 0.7


def is_calibrated(venue) -> bool:
    """
    Has this venue's fence been *learned* from verified check-ins, as opposed
    to walked once by an admin?

    Stage 5 populates the cal_* columns and this function then starts
    returning True for venues that have earned it. Until those columns exist
    it returns False for everything, which is the safe direction: an
    uncalibrated venue never reaches INSIDE and its confidence is capped.

    Written with getattr so it is correct both before and after the Stage 5
    schema change, instead of needing to be rewritten.
    """
    if venue is None:
        return False
    return bool(
        getattr(venue, "cal_n", None)
        and getattr(venue, "cal_lat", None) is not None
        and getattr(venue, "cal_lng", None) is not None
        and getattr(venue, "cal_radius", None)
    )


def effective_fence_radius(venue) -> float:
    """
    The radius the geofence will actually use for this venue.

    A calibrated venue uses its *learned* radius (the 90th-percentile reach of
    verified check-ins plus a buffer), which is the whole point of Stage 5: the
    fence sits where students actually are, not where one admin walked. An
    uncalibrated venue falls back to the surveyed radius, then the system
    default.

    Extracted from /attendance/mark rather than duplicated into the lecturer
    payload, which needs the same number to make `distance_m` mean anything.
    Two copies of a fallback chain is how the two views end up disagreeing
    about where the fence was.
    """
    if is_calibrated(venue):
        return float(venue.cal_radius)
    r = getattr(venue, "radius", None)
    if r and r > 0:
        return float(r)
    return float(app.config["GEOFENCE_RADIUS_METRES"])


def effective_fence_centre(venue) -> tuple[float, float]:
    """
    The (lat, lng) the geofence measures distance from.

    This is the half of Stage 5 that actually moves the fence. `is_calibrated`
    already gated confidence and the QR override, but every distance was still
    taken from the surveyed centre — so a venue whose survey sat 60m east
    stayed 60m east no matter how many verified check-ins disagreed. A
    calibrated venue now measures from its learned centre; an uncalibrated one
    from the survey.

    Must be read together with effective_fence_radius: centre and radius have
    to come from the same source, or a learned radius gets measured against a
    surveyed centre and means nothing.
    """
    if is_calibrated(venue):
        return float(venue.cal_lat), float(venue.cal_lng)
    return float(venue.latitude), float(venue.longitude)


def reported_uncertainty(r) -> tuple[float, float | None]:
    """
    The two accuracy figures a reader of an attendance record needs, in the
    order of trust this system actually places in them.

    Returns (uncertainty_m, device_claim_m):

      * uncertainty_m is `acc_eff` -- the figure the geofence decided on, which
        is max(best reported accuracy, observed scatter). This is the number
        that belongs next to distance_m, because it is the one distance_m was
        judged against.
      * device_claim_m is the raw `accuracy` the handset reported, or None when
        it adds nothing (no multi-sample capture, so the two are the same).

    Why this exists: both lecturer payloads served `round(r.accuracy, 1)` under
    the key `accuracy_m`, while the student's own response serves `acc_eff`
    under that same key. So the one person asked to *review* an AMBIGUOUS row
    was shown the single quantity robust_fix and geofence_decision both exist
    to refuse -- the device's self-report, which on a stationary phone
    replaying a WiFi fingerprint reads a confident 12m while the observed
    scatter says 80m. A lecturer comparing distance_m against 12m sees a fix
    that should obviously have passed, and no reason for the flag.

    `acc_eff` is NULL on every row written before Stage 1, and NULL means this
    row does not know, not zero. Those rows fall back to the device figure --
    which for a single-sample capture is exactly what acc_eff would have been
    anyway, since spread is 0.0 with one sample and acc_eff = max(acc, 0.0).
    Falling back never claims more than the row supports.
    """
    device = float(r.accuracy) if r.accuracy is not None else None
    eff = float(r.acc_eff) if getattr(r, "acc_eff", None) is not None else None
    if eff is None:
        return (device if device is not None else 0.0), None
    # Only worth showing twice when they differ; with one sample they agree.
    return eff, (device if device is not None and abs(device - eff) >= 0.05
                 else None)


def geofence_decision(distance: float,
                      acc_eff: float,
                      base_radius: float,
                      calibrated: bool = False) -> dict:
    """
    Turn (distance, uncertainty, radius) into a three-state verdict plus a
    confidence score, instead of a boolean that has to lie.

    With `a` being a 95%-confidence radius per the W3C Geolocation spec:

        INSIDE      d + a <= r      certainly inside
        OUTSIDE     d - a >  r      certainly outside
        AMBIGUOUS   otherwise       the answer is genuinely unknown

        confidence = clamp( (r + a - d) / (2a), 0, 1 )

    The formula is linear, degrades sensibly and agrees with the states at
    both ends: 1.0 at d = r - a, 0.0 at d = r + a.

    The old rule was `d <= r + a`, which is exactly the negation of OUTSIDE.
    So the *reject* boundary is unchanged -- the tightening comes from the
    caller capping `a` relative to the radius rather than at a flat 500m, and
    from the middle band no longer being reported as a clean pass.

    WHY `calibrated` MATTERS MORE THAN THE ARITHMETIC
    -------------------------------------------------
    Agreement between GPS samples measures *precision*. Indoors the dominant
    error is a systematic WiFi-fingerprint offset: the position database's
    idea of where those access points sit. Ten fixes then yield ten copies of
    the same error -- near-zero spread, tiny `a`, and therefore a confident
    verdict about a position that is 100m wrong. Confidence computed from the
    sensor alone is confidence in the sensor's self-report.

    So until a venue's fence has been learned from verified check-ins
    (Stage 5), confidence is capped and INSIDE is withheld: the geometry may
    say inside, but nothing yet says the geometry is aimed at the right spot.
    `geo_state` keeps the raw geometric verdict for diagnostics.
    """
    d = float(distance)
    a = max(float(acc_eff), 0.0)
    r = float(base_radius)

    if d + a <= r:
        geo_state = STATE_INSIDE
    elif d - a > r:
        geo_state = STATE_OUTSIDE
    else:
        geo_state = STATE_AMBIGUOUS

    if a > 0:
        raw_conf = min(1.0, max(0.0, (r + a - d) / (2.0 * a)))
    else:
        # Cannot happen through normalise_samples, which rejects acc <= 0;
        # handled so the formula can never divide by zero.
        raw_conf = 1.0 if d <= r else 0.0

    if calibrated:
        state, conf = geo_state, raw_conf
    else:
        conf = min(raw_conf, UNCALIBRATED_CONFIDENCE_CEILING)
        state = STATE_AMBIGUOUS if geo_state == STATE_INSIDE else geo_state

    return {
        "geo_state":      geo_state,
        "verify_state":   state,
        "confidence":     conf,
        "raw_confidence": raw_conf,
        "calibrated":     bool(calibrated),
        "distance_m":     d,
        "acc_eff":        a,
        "radius_m":       r,
    }


# ─────────────────────────────────────────────────────────────
# VENUE COLLISION (Stage 3)
# ─────────────────────────────────────────────────────────────
#
# Two venues collide in two different ways, and they need different answers.
#
#   Overlapping  — their geofence circles intersect. A fix landing in the
#                  intersection genuinely could be either room. This is a
#                  design-time problem: the cheapest fix is for the admin to
#                  shrink a radius, so it is reported at set-gps time.
#   Stacked      — same building, essentially the same footprint, different
#                  floor. There is no fix. The circles are not merely close,
#                  they are the same circle, and §1.3 closes every geometric
#                  route to telling them apart. Stacked venues are therefore
#                  permanently ambiguous to GPS and must escalate to the QR
#                  token, which cannot pass through a concrete slab.

STACKED_MAX_SEPARATION_M = 15.0

# Below this, an overlap is an artifact rather than a finding. `radius` is
# stored as round(x, 2) after a 15m buffer is added to a walked hull, so two
# genuinely-adjacent rooms routinely intersect by centimetres. Reporting that
# trains admins to dismiss overlap warnings, which is worse than not having
# them. The geometric predicate below stays exact; only `venue_conflicts`,
# which exists to produce warnings, applies this floor.
OVERLAP_MIN_SIGNIFICANT_M = 1.0


def _venue_has_gps(v) -> bool:
    return bool(
        v is not None
        and getattr(v, "latitude", None) is not None
        and getattr(v, "longitude", None) is not None
    )


def venue_radius(v, default: float | None = None) -> float:
    """
    The fence radius actually in force for a venue, never zero.

    Calibration-aware: once a venue has a learned fence (Stage 5), that fence
    *is* the radius in force, and every verification-time reader must see it —
    the contest, the coarse-fix gate and the overlap test all measure against
    "the fence", and at a calibrated venue the surveyed radius is no longer
    that. Set-up diagnostics that must speak about the walked geometry use
    _surveyed_radius() instead, which deliberately ignores cal_*.
    """
    if is_calibrated(v):
        return float(v.cal_radius)
    r = getattr(v, "radius", None)
    if r and r > 0:
        return float(r)
    return float(default if default is not None else DEFAULT_RADIUS_M)


def venues_overlap(a, b, default_radius: float | None = None,
                   effective: bool = True) -> float | None:
    """
    Metres of overlap between two venue circles, or None if they are disjoint
    (or either lacks GPS). Positive means the circles intersect.

    `effective` picks which fence is being spoken about, and the two callers
    want different answers:

      * verification time (overlaps_another_open_session) asks whether two
        fences *in force* share ground, so it wants the effective fence — the
        learned centre and radius at a calibrated venue;
      * the set-up diagnostic (venue_conflicts, from set-gps) asks whether the
        admin's *surveyed* circles collide, so it wants the walked geometry.

    Mixing them is how a venue would appear to overlap in one view and not the
    other with no code path actually disagreeing about anything.
    """
    if not (_venue_has_gps(a) and _venue_has_gps(b)):
        return None
    if effective:
        al, ag = effective_fence_centre(a)
        bl, bg = effective_fence_centre(b)
        ra, rb = venue_radius(a, default_radius), venue_radius(b, default_radius)
    else:
        al, ag = a.latitude, a.longitude
        bl, bg = b.latitude, b.longitude
        ra, rb = _surveyed_radius(a), _surveyed_radius(b)
    d = haversine_distance(al, ag, bl, bg)
    reach = ra + rb
    return round(reach - d, 2) if d < reach else None


def venues_stacked(a, b) -> bool:
    """
    True when two venues sit on top of one another: same named building,
    centres within STACKED_MAX_SEPARATION_M, and different declared floors.

    All three conditions are required. Two rooms 4m apart on the *same* floor
    are an overlap problem, not a stacking problem, and a room in a different
    building that happens to be nearby is neither.
    """
    if not (_venue_has_gps(a) and _venue_has_gps(b)):
        return False
    ba, bb = getattr(a, "building", None), getattr(b, "building", None)
    if not ba or not bb or ba.strip().lower() != bb.strip().lower():
        return False
    fa, fb = getattr(a, "floor", None), getattr(b, "floor", None)
    if fa is None or fb is None or fa == fb:
        return False
    d = haversine_distance(a.latitude, a.longitude, b.latitude, b.longitude)
    return d < STACKED_MAX_SEPARATION_M


def venue_conflicts(venue, others, default_radius: float | None = None) -> dict:
    """
    Every other venue that collides with this one, split by kind.
    Callers pass `others` so this stays free of query concerns and testable
    against plain objects.

    This is the SET-UP diagnostic, so it speaks about the surveyed geometry
    (`effective=False`): it runs from set-gps, where the admin has just walked a
    perimeter and can still shrink the radius, and where the learned fence of
    some other venue is not the thing being corrected.

    Overlaps below OVERLAP_MIN_SIGNIFICANT_M are omitted — see that constant.
    """
    overlaps, stacked = [], []
    for o in others:
        if o is venue or getattr(o, "id", None) == getattr(venue, "id", object()):
            continue
        if venues_stacked(venue, o):
            stacked.append({
                "venue_id": getattr(o, "id", None),
                "name":     getattr(o, "name", None),
                "building": getattr(o, "building", None),
                "floor":    getattr(o, "floor", None),
            })
            continue          # a stacked pair is reported once, as stacked
        m = venues_overlap(venue, o, default_radius, effective=False)
        if m is not None and m >= OVERLAP_MIN_SIGNIFICANT_M:
            overlaps.append({
                "venue_id":  getattr(o, "id", None),
                "name":      getattr(o, "name", None),
                "overlap_m": m,
                "distance_m": round(haversine_distance(
                    venue.latitude, venue.longitude, o.latitude, o.longitude), 1),
                "radius_m":  _surveyed_radius(o),
            })
    return {"overlaps": overlaps, "stacked": stacked}


def contested_by(lat: float, lng: float, own, others,
                 default_radius: float | None = None) -> dict | None:
    """
    Nearest-venue-wins. Returns the venue that explains this fix at least as
    well as the session's own venue, or None if the session's venue is the
    clear winner.

    Ranking is by **normalised** margin `d / r`, not raw distance: a fix 40m
    into a 200m yard is far more comfortably inside it than a fix 40m into a
    45m room, and raw distance would say the opposite. Smaller is better —
    0.0 is dead centre, 1.0 is on the fence.

    A consequence worth knowing: a venue whose circle *contains* another's will
    out-rank it for any fix near the inner venue's edge. If a campus has both
    "Main Quad" (r=200) and "LT-1" (r=50) at the same centre, an LT-1 fix 40m
    off-centre scores 0.80 for the room and 0.20 for the quad, so the quad
    contests and the submission goes AMBIGUOUS. That is the honest answer —
    GPS genuinely cannot tell that student from one standing in the quad
    outside the room — and Stage 4 resolves it with a QR scan.

    Two deliberate asymmetries:

    * A stacked peer always contests, whatever the margins say. Two rooms on
      different floors of one building differ by centimetres of horizontal
      separation, so whichever wins, wins on noise. Letting a coin flip decide
      which room a student is in is worse than admitting we cannot tell.
    * Ties go to the challenger (`<=`, not `<`). Erring toward ambiguity costs
      a lecturer one review; erring the other way records a confident answer
      the data does not support.

    The result can only ever *downgrade* a verdict to AMBIGUOUS. It never
    upgrades, and it never rejects — being near two rooms is not evidence of
    being outside both.
    """
    if not _venue_has_gps(own):
        return None

    # Measured from the SAME fence the geofence decision used: the learned
    # centre/radius at a calibrated venue, the survey otherwise. Reading the
    # surveyed centre here while the verdict came from the learned one makes
    # own_margin — and therefore whether a peer can contest at all — describe a
    # fence that is not the one in force.
    own_lat, own_lng = effective_fence_centre(own)
    own_r = venue_radius(own, default_radius)
    own_d = haversine_distance(lat, lng, own_lat, own_lng)
    own_margin = own_d / own_r if own_r > 0 else float("inf")

    best = None
    for o in others:
        if o is own or getattr(o, "id", None) == getattr(own, "id", object()):
            continue
        if not _venue_has_gps(o):
            continue

        # Peers are read from their own effective fence too, for the same
        # reason: a peer that has calibrated is a differently-shaped room than
        # its survey says.
        o_lat, o_lng = effective_fence_centre(o)
        r = venue_radius(o, default_radius)
        d = haversine_distance(lat, lng, o_lat, o_lng)
        margin = d / r if r > 0 else float("inf")
        is_stacked = venues_stacked(own, o)

        # A stacked peer contests regardless of margin; anything else has to
        # actually contain the fix and match or beat the session's venue.
        if not is_stacked and not (d <= r and margin <= own_margin):
            continue
        if best is None or margin < best["margin"]:
            best = {
                "venue_id":   getattr(o, "id", None),
                "name":       getattr(o, "name", None),
                "building":   getattr(o, "building", None),
                "floor":      getattr(o, "floor", None),
                "distance_m": round(d, 1),
                "radius_m":   r,
                "margin":     margin,
                "reason":     "stacked" if is_stacked else "nearer",
            }

    if best is None:
        return None
    best["own_margin"] = round(own_margin, 4)
    best["margin"] = round(best["margin"], 4)
    return best


# ─────────────────────────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────────────────────────

def get_current_user() -> User | None:
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


def login_required(role=None):
    """Decorator — enforces authentication and optional role check."""
    from functools import wraps
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if not user:
                if request.is_json:
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for("login_page"))
            if role and user.role != role:
                if request.is_json:
                    return jsonify({"error": "Forbidden"}), 403
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def superuser_required(f):
    """Decorator — requires admin role AND is_superuser=True."""
    from functools import wraps
    @wraps(f)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login_page"))
        if user.role != "admin" or not user.is_superuser:
            if request.is_json:
                return jsonify({"error": "Superuser access required"}), 403
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def log_admin_action(
    admin_id: int,
    action: str,
    target_type: str = None,
    target_id: int = None,
    details: dict = None,
) -> None:
    """Write a row to admin_logs. Caller must commit."""
    entry = AdminLog(
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=json.dumps(details, default=str) if details else None,
    )
    db.session.add(entry)


# ─────────────────────────────────────────────────────────────
# QR TOKEN GENERATION  (stateless HMAC-based, 30-second windows)
# ─────────────────────────────────────────────────────────────

QR_TOKEN_INTERVAL = 30  # seconds per token window


def generate_qr_token(session_id: int, unix_ts: float = None) -> str:
    """Generate a short-lived QR token for an attendance session."""
    if unix_ts is None:
        unix_ts = datetime.utcnow().timestamp()
    window = int(unix_ts) // QR_TOKEN_INTERVAL
    message = f"{session_id}:{window}"
    return hmac.new(
        app.config["SECRET_KEY"].encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


def validate_qr_token(session_id: int, token: str) -> bool:
    """
    Validate a QR token -- accepts the current window and the previous one.

    Two windows, ~60 seconds. The offsets used to run to -3 (~120s) while this
    docstring already claimed two, and the code was the wrong half of that
    disagreement: a QR code's whole security property is that it is confined to
    one room by the physics of light, and the only way around that is to
    photograph it and send the picture elsewhere. The acceptance window is
    therefore exactly the relay window, and two minutes is generous to a
    relay. One window would be too tight -- a student who starts scanning at
    the 29th second of a window would fail through no fault of their own.
    """
    if not token:
        return False
    # The token comes straight from client JSON, so it can be any type the
    # client likes. hmac.compare_digest raises TypeError on a non-str and on a
    # str with non-ASCII characters, which turned `"qr_token": "cafécafé"` --
    # or any JSON object -- into a 500 on the busiest endpoint in the system.
    # A malformed token is not a valid token; say so rather than crashing.
    if not isinstance(token, str) or not token.isascii():
        return False
    now = datetime.utcnow().timestamp()
    for offset in (0, -1):
        expected = generate_qr_token(session_id, now + offset * QR_TOKEN_INTERVAL)
        if hmac.compare_digest(token, expected):
            return True
    return False


# ─────────────────────────────────────────────────────────────
# ADAPTIVE QR GATING  (Stage 4)
# ─────────────────────────────────────────────────────────────
#
# GPS and QR fail in exactly opposite ways, which is the whole reason this
# stage is cheap to build:
#
#                  resolves a room?            relayable off-site?
#   GPS            no  - 15-50m, no floors     no  - you cannot fake campus
#   QR             yes - confined by light     yes - a photo travels
#
# Neither is sufficient alone and each closes the other's hole. Both halves
# already existed in this codebase; what was missing was any rule about *when*
# to demand the second one. `if qr_token:` meant a client could simply omit the
# field, so the QR system was decorative -- it protected exactly the students
# honest enough to use it.
#
# The rule is adaptive rather than always-on because always-on charges every
# student for a problem that belongs to specific venues, and this system is
# meant to be used by people in a hurry between classes.

# How far a QR-verified fix may sit from the surveyed centre before the token
# stops being treated as ground truth. See qr_ground_truth_limit().
#
# Absolute campus-scale ceiling, and a multiple of the fence radius. The
# multiple is the operative one at every venue this system actually has: a flat
# 500m against a 50m lecture hall is a 10x widening, which is the exact shape of
# the 550m effective fence this project set out to remove (D3). Scaling with the
# radius keeps the override proportionate to the thing it is overriding -- 150m
# at a default 50m venue, and the flat ceiling only binds above r = 167m.
QR_GROUND_TRUTH_MAX_M = 500.0
QR_GROUND_TRUTH_RADIUS_FACTOR = 3.0

# Above this fraction of the fence radius, a fix is too coarse to stand alone.
QR_COARSE_FIX_FRACTION = 0.5


def has_stacked_peers(venue) -> bool:
    """Is any other venue stacked above or below this one?"""
    if venue is None or not getattr(venue, "building", None):
        return False
    peers = Venue.query.filter(Venue.id != venue.id).all()
    return any(venues_stacked(venue, p) for p in peers)


def overlaps_another_open_session(sess, default_radius=None) -> bool:
    """
    Is another session running *right now* at a venue whose fence overlaps
    this one's?

    Overlap alone is a design-time problem and Stage 3 already warns the admin
    about it. It becomes a verification problem only when two classes are
    simultaneously live in overlapping fences, because then a fix in the shared
    area genuinely matches two different sessions and GPS cannot say which.
    """
    if sess is None:
        return False
    venue = sess.venue
    if venue is None or not _venue_has_gps(venue):
        return False

    others = (
        AttendanceSession.query
        .filter(AttendanceSession.id != sess.id,
                AttendanceSession.status.in_(tuple(OPEN_DB_STATUSES)))
        .all()
    )
    for other in others:
        if other.venue_id == venue.id:
            continue
        ov = other.venue
        if ov is None or not _venue_has_gps(ov):
            continue
        if other.is_expired():
            continue
        if venues_overlap(venue, ov, default_radius) is not None:
            return True
    return False


def qr_required(sess, venue, verify_state, acc_eff, contest=None,
                default_radius=None) -> dict:
    """
    Decide whether this submission must carry a valid QR token.

    Returns {"required": bool, "reasons": [str, ...]} rather than a bare
    boolean, because a student told only "scan the code" with no reason will
    assume the app is broken, and a lecturer needs to know whether the demand
    came from a stacked venue (permanent, structural) or a weak fix (transient).

    Every reason is a case where GPS is known to be unable to answer the
    question on its own:
    """
    reasons = []

    # An unproven venue. This line is what makes the whole scheme bootstrap:
    # every venue starts out requiring a scan and earns its way out by
    # accumulating the verified check-ins Stage 5 learns its real fence from.
    # Until Stage 5 lands, is_calibrated() is False everywhere and this means
    # QR everywhere -- stricter than today, and safe.
    if not is_calibrated(venue):
        reasons.append("venue_uncalibrated")

    # Floors. Geometry cannot separate a stack at any radius; light can.
    if has_stacked_peers(venue):
        reasons.append("stacked_venue")

    # Two live classes sharing ground.
    if overlaps_another_open_session(sess, default_radius):
        reasons.append("concurrent_overlapping_session")

    # Step 8 found a venue that explains the fix at least as well.
    if contest is not None:
        reasons.append("contested_by_nearby_venue")

    # The geofence itself declined to be certain.
    if verify_state == STATE_AMBIGUOUS:
        reasons.append("ambiguous_fix")

    # A fix whose uncertainty is half the fence is not evidence about the fence.
    radius = venue_radius(venue, default_radius) if venue is not None else None
    if radius and acc_eff is not None and acc_eff > QR_COARSE_FIX_FRACTION * radius:
        reasons.append("coarse_fix")

    return {"required": bool(reasons), "reasons": reasons}


def qr_ground_truth_limit(radius_m=None) -> float:
    """
    How far from the surveyed centre a valid token still counts as evidence.

    Two paths in this file accept a fix further out than the fence allows, and
    before this existed only one of them consulted a bound:

      * step 7b overrides an OUTSIDE verdict outright;
      * the AMBIGUOUS band `d <= r + a` reaches `r + min(2r, 500)`, because
        step 6 caps a_eff at min(2r, 500) rather than rejecting.

    The second reach reads further than the first at any r >= 250 (r=250 reaches
    750m, r=400 reaches 900m) while never looking at the 500m bound at all. A
    bound that one of two acceptance paths ignores is not a bound, so both now
    read this one function.
    """
    if radius_m is None or radius_m <= 0:
        return QR_GROUND_TRUTH_MAX_M
    return min(QR_GROUND_TRUTH_MAX_M, QR_GROUND_TRUTH_RADIUS_FACTOR * radius_m)


def qr_ground_truth_override(distance_m: float, calibrated: bool,
                             radius_m=None) -> bool:
    """
    May a valid QR token overrule an OUTSIDE geofence verdict? (Defect D9)

    The deadlock this breaks: a venue whose surveyed centre is systematically
    wrong -- the §1.1 WiFi-fingerprint offset, which can be 100m or more --
    rejects every honest fix taken from inside its own room. Rejected
    submissions store nothing, so no verified check-ins accumulate, so Stage 5
    can never learn the true fence, so the survey error never gets corrected.
    The venue is permanently broken and the system has no way to notice.

    A valid token cuts that knot, because it is better evidence than the
    survey it contradicts: the code was on the projector *in that room*, and
    the survey is one admin's walk with the same offset sensor. When the two
    disagree about an uncalibrated venue, the token wins.

    Bounded by qr_ground_truth_limit(), because a photographed code travels.
    The bound is a multiple of the fence radius rather than a flat campus-scale
    number: a 50m venue that accepts a token 500m out has a 10x wider effective
    fence than the one the admin surveyed, which is the defect this project
    started from.

    Never applies to a calibrated venue -- there the fence was learned from
    verified check-ins, so a fix outside it disagrees with the students
    themselves rather than with a possibly-bad survey, and the token no longer
    outranks it.
    """
    if calibrated:
        return False
    return distance_m <= qr_ground_truth_limit(radius_m)


def qr_is_calibration_grade(qr_ok: bool, overrode: bool, distance_m: float,
                            radius_m=None) -> bool:
    """
    May this row be used as ground truth when Stage 5 learns a venue's fence?

    Stricter than "a valid token was presented", and deliberately so, because
    Stage 5's input population is a security boundary that writes itself. Two
    exclusions:

    **Beyond the ground-truth limit.** A token proves the student saw the
    projector; it says nothing about the accuracy of the fix submitted
    alongside it. Past the limit the fix is no more credible than the survey
    it disagrees with, so it is credited to the student and withheld from the
    fence.

    **Override rows, at any distance.** A row that needed step 7b to be
    accepted is, by definition, a fix the current fence calls provably
    outside. Feeding it back into the fence is the fence rewriting itself from
    the evidence of its own failures -- and since a >10% tail moves a 90th
    percentile, enough of them move the fence to wherever they were submitted
    from. Worse, the promotion that poisoning earns then sets `cal_*`, which
    flips is_calibrated() to True, which switches the override off: the escape
    hatch for a bad survey is closed by the abuse it enabled.

    This costs the §1.6 bootstrap nothing, which is the non-obvious part. The
    survey errors that bootstrap describes are tens of metres -- comparable to
    a_eff -- so `d - a > r` never fires and those rows arrive as AMBIGUOUS with
    a valid token, calibration-grade, override untouched. Step 7b only fires on
    errors of hundreds of metres, where "the survey is wrong" and "the token
    was relayed" are genuinely indistinguishable from here. Those belong in
    front of an admin, which is what Stage 5's displacement flag is for, not in
    an automatic feedback loop.

    Mirrors the discipline manual_mark already applies, where a record written
    from no measurement at all is stored with qr_verified=False specifically so
    it can never reach this population.
    """
    if not qr_ok or overrode:
        return False
    return distance_m <= qr_ground_truth_limit(radius_m)



# ─────────────────────────────────────────────────────────────
# VENUE CALIBRATION (Stage 5) — learn the fence from check-ins
# ─────────────────────────────────────────────────────────────
#
# This is the §1.6 inversion and the only mechanism in the system that corrects
# a systematic WiFi-fingerprint offset rather than merely averaging out noise:
# every QR-verified check-in is a labelled observation -- *this device reported
# these coordinates while provably inside this room* -- and enough of them say
# where the room's devices actually report from, which is the fence that belongs
# there. A feedback loop that writes its own security boundary needs brakes, so
# every number below is a median or a percentile, the input is only ever
# qr_trusted rows (frozen at write time, DR-28), and a candidate that moves the
# centre too far or widens the fence too much is handed to an admin rather than
# applied.

# Promotion thresholds. One unusual session's atmospheric conditions must not
# become a permanent fence, so promotion needs breadth on four axes at once.
CAL_MIN_RECORDS  = 30
CAL_MIN_STUDENTS = 15
CAL_MIN_SESSIONS = 3
CAL_MIN_DAYS     = 2

# The learned radius is the 90th percentile of per-record distances, never the
# max, plus a small buffer. Percentile so one student at the window does not set
# the fence for the room.
CAL_RADIUS_PERCENTILE = 90.0
CAL_RADIUS_BUFFER_M   = 10.0

# Poisoning brakes. The displacement cap is load-bearing, not belt-and-braces:
# at the default 50m venue the furthest a calibration-grade row can sit is
# 3 x radius = 150m (qr_ground_truth_limit), which equals this cap exactly, so
# one row is enough to reach it (DR-30). A later author tuning either number in
# isolation breaks the other. The radius cap stops a noisy spell quietly
# doubling the fence. Past either, we do not auto-apply -- a big disagreement
# means the survey is wrong or something is being gamed, and deciding which is
# an admin's call, not this loop's.
CAL_MAX_DISPLACEMENT_M          = 150.0
CAL_MAX_RADIUS_EXPANSION_FACTOR = 2.0


def _surveyed_radius(venue) -> float:
    """
    The admin-walked radius, NEVER the learned one.

    Distinct from effective_fence_radius(), which returns cal_radius once a
    venue is calibrated. The guards below must always measure a candidate
    against the original survey, or recalibration ratchets: each learned radius
    becomes the baseline the next one is allowed to double.
    """
    r = getattr(venue, "radius", None)
    if r and r > 0:
        return float(r)
    return float(app.config["GEOFENCE_RADIUS_METRES"])


def compute_venue_calibration(records, survey_lat, survey_lng,
                              survey_radius) -> dict | None:
    """
    Collapse a venue's calibration-grade check-ins into one candidate fence.

    `records` is a list of dicts with lat, lng, student_id, session_id and day
    (a date). Pure and DB-free so the thresholds and the arithmetic can be
    tested directly; recompute_venue_calibration() builds the list and acts on
    the result.

    Returns None for an empty list, else a candidate carrying both the proposed
    geometry and the breadth stats calibration_decision() judges:

        lat, lng   : component-wise median of the fixes -- robust to a few bad
                     ones, the same centre estimator robust_fix uses.
        radius     : 90th-percentile distance from that centre + buffer.
        n          : record count.
        students   : distinct student_id count.
        sessions   : distinct session_id count.
        days       : distinct day count.
        displacement_m : how far the learned centre sits from the survey.
    """
    if not records:
        return None

    lat = _median([r["lat"] for r in records])
    lng = _median([r["lng"] for r in records])

    dists = [haversine_distance(r["lat"], r["lng"], lat, lng) for r in records]
    raw_radius = _percentile(dists, CAL_RADIUS_PERCENTILE)

    return {
        "lat": lat,
        "lng": lng,
        "radius": raw_radius + CAL_RADIUS_BUFFER_M,
        "raw_radius_m": raw_radius,
        "n": len(records),
        "students": len({r["student_id"] for r in records}),
        "sessions": len({r["session_id"] for r in records}),
        "days": len({r["day"] for r in records if r["day"] is not None}),
        "displacement_m": haversine_distance(lat, lng, survey_lat, survey_lng),
    }


def calibration_decision(candidate: dict, survey_radius: float) -> tuple[str, str]:
    """
    Judge a candidate: ('promote' | 'flag' | 'insufficient', reason).

      insufficient  one of the four breadth thresholds is not yet met; keep
                    collecting, change nothing.
      flag          thresholds met but a poisoning brake tripped -- the centre
                    moved past CAL_MAX_DISPLACEMENT_M, or the radius would
                    exceed CAL_MAX_RADIUS_EXPANSION_FACTOR x the survey. Hold
                    the candidate for an admin; do NOT move the fence.
      promote       thresholds met, brakes clear; apply as the learned fence.

    Order matters: breadth is checked first, because a displacement computed
    from nine records is not yet evidence of anything, and flagging on it would
    put noise in front of an admin.
    """
    if candidate is None:
        return "insufficient", "no records"
    if candidate["n"] < CAL_MIN_RECORDS:
        return "insufficient", f"n={candidate['n']} < {CAL_MIN_RECORDS}"
    if candidate["students"] < CAL_MIN_STUDENTS:
        return "insufficient", f"students={candidate['students']} < {CAL_MIN_STUDENTS}"
    if candidate["sessions"] < CAL_MIN_SESSIONS:
        return "insufficient", f"sessions={candidate['sessions']} < {CAL_MIN_SESSIONS}"
    if candidate["days"] < CAL_MIN_DAYS:
        return "insufficient", f"days={candidate['days']} < {CAL_MIN_DAYS}"

    if candidate["displacement_m"] > CAL_MAX_DISPLACEMENT_M:
        return ("flag",
                f"learned centre {round(candidate['displacement_m'])}m from "
                f"survey (> {round(CAL_MAX_DISPLACEMENT_M)}m cap)")
    if candidate["radius"] > CAL_MAX_RADIUS_EXPANSION_FACTOR * survey_radius:
        return ("flag",
                f"learned radius {round(candidate['radius'])}m > "
                f"{round(CAL_MAX_RADIUS_EXPANSION_FACTOR)}x survey "
                f"{round(survey_radius)}m")

    return "promote", "thresholds met, brakes clear"


def recompute_venue_calibration(venue, actor_id=None) -> None:
    """
    Recompute a venue's learned fence from its calibration-grade check-ins and
    apply, flag, or do nothing per calibration_decision().

    Triggered whenever a session closes (see AttendanceSession._on_closed).
    Idempotent: it reads the whole eligible population every time, so firing it
    twice for the same venue simply writes the same answer.

    Calibration is strictly secondary to attendance, so this never raises into
    its caller: any failure rolls back only its own uncommitted changes and
    leaves the already-committed attendance untouched.
    """
    try:
        if venue is None or venue.latitude is None or venue.longitude is None:
            return  # no survey centre => no displacement => nothing to judge

        rows = (db.session.query(AttendanceRecord)
                .join(AttendanceSession,
                      AttendanceRecord.session_id == AttendanceSession.id)
                .filter(AttendanceSession.venue_id == venue.id,
                        # .is_(True), never == True and never a bare truthiness
                        # test: NULL means "not eligible" (DR-31), and only
                        # .is_(True) excludes NULL the way the design requires.
                        AttendanceRecord.qr_trusted.is_(True))
                .all())
        if not rows:
            return

        recs = [{
            "lat": r.lat, "lng": r.lng,
            "student_id": r.student_id, "session_id": r.session_id,
            "day": r.timestamp.date() if r.timestamp else None,
        } for r in rows]

        survey_radius = _surveyed_radius(venue)
        candidate = compute_venue_calibration(
            recs, venue.latitude, venue.longitude, survey_radius)
        outcome, reason = calibration_decision(candidate, survey_radius)

        if outcome == "insufficient":
            return

        if outcome == "promote":
            was_calibrated = is_calibrated(venue)
            venue.cal_lat        = candidate["lat"]
            venue.cal_lng        = candidate["lng"]
            venue.cal_radius     = candidate["radius"]
            venue.cal_n          = candidate["n"]
            venue.cal_updated_at = datetime.utcnow()
            venue.cal_review     = None   # a clean promotion clears any flag
            if actor_id is not None:
                log_admin_action(
                    actor_id,
                    "RECALIBRATE_VENUE" if was_calibrated else "CALIBRATE_VENUE",
                    "venue", venue.id, {
                        "cal_lat": candidate["lat"], "cal_lng": candidate["lng"],
                        "cal_radius": round(candidate["radius"], 2),
                        "n": candidate["n"], "students": candidate["students"],
                        "sessions": candidate["sessions"], "days": candidate["days"],
                        "displacement_m": round(candidate["displacement_m"], 1),
                    })
            db.session.commit()
            return

        # outcome == "flag": hold the candidate, never move the fence.
        venue.cal_review = json.dumps({
            "lat": candidate["lat"], "lng": candidate["lng"],
            "radius": round(candidate["radius"], 2),
            "n": candidate["n"], "students": candidate["students"],
            "sessions": candidate["sessions"], "days": candidate["days"],
            "displacement_m": round(candidate["displacement_m"], 1),
            "survey_radius_m": round(survey_radius, 2),
            "reason": reason,
            "at": datetime.utcnow().isoformat(),
        }, default=str)
        if actor_id is not None:
            log_admin_action(actor_id, "CALIBRATION_FLAGGED", "venue", venue.id,
                             {"reason": reason, "n": candidate["n"],
                              "displacement_m": round(candidate["displacement_m"], 1)})
        db.session.commit()

    except Exception:
        db.session.rollback()



# ─────────────────────────────────────────────────────────────
# PAGE ROUTES  (serve HTML templates)
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    user = get_current_user()
    if not user:
        return redirect(url_for("login_page"))
    if user.role == "admin":
        return redirect(url_for("admin_dashboard"))
    if user.role == "lecturer":
        return redirect(url_for("lecturer_dashboard"))
    return redirect(url_for("student_dashboard"))


@app.route("/login")
def login_page():
    if get_current_user():
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/dashboard/student")
@login_required(role="student")
def student_dashboard():
    user = get_current_user()
    return render_template("student_dashboard.html", user=user)


@app.route("/dashboard/lecturer")
@login_required(role="lecturer")
def lecturer_dashboard():
    user = get_current_user()
    return render_template("lecturer_dashboard.html", user=user)


@app.route("/dashboard/admin")
@login_required(role="admin")
def admin_dashboard():
    user = get_current_user()
    return render_template("admin_dashboard.html", user=user)



# ─────────────────────────────────────────────────────────────
# API — AUTH
# ─────────────────────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid credentials"}), 401

    session.clear()
    session["user_id"]   = user.id
    session["user_role"] = user.role
    session.permanent    = True

    return jsonify({
        "message":  "Login successful",
        "user":     {"id": user.id, "name": user.name, "role": user.role},
        "redirect": (
            "/dashboard/admin" if user.role == "admin"
            else "/dashboard/lecturer" if user.role == "lecturer"
            else "/dashboard/student"
        )
    })


# ─────────────────────────────────────────────────────────────
# API — LECTURER
# ─────────────────────────────────────────────────────────────

def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_iso_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def get_day_bounds(target_day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target_day, time.min)
    end = start + timedelta(days=1)
    return start, end


def get_max_schedule_postpones() -> int:
    raw = app.config.get("MAX_SCHEDULE_POSTPONES", 2)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 2


def get_latest_open_session(course_id: int, target_day: date | None = None) -> AttendanceSession | None:
    query = (AttendanceSession.query
             .filter(
                 AttendanceSession.course_id == course_id,
                 AttendanceSession.status.in_(OPEN_DB_STATUSES),
             ))

    if target_day:
        day_start, day_end = get_day_bounds(target_day)
        query = query.filter(
            AttendanceSession.start_time >= day_start,
            AttendanceSession.start_time < day_end,
        )

    sess = query.order_by(AttendanceSession.start_time.desc()).first()
    if sess:
        sess.close_if_expired()
    return sess if sess and is_session_open(sess.status) else None


@app.route("/api/lecturer/venues", methods=["GET"])
@login_required(role="lecturer")
def api_lecturer_venues():
    venues = Venue.query.order_by(Venue.name.asc()).all()
    return jsonify({
        "venues": [{
            "id": v.id,
            "name": v.name,
            "latitude": v.latitude,
            "longitude": v.longitude,
        } for v in venues]
    })


@app.route("/api/lecturer/calendar", methods=["GET"])
@login_required(role="lecturer")
def api_lecturer_calendar():
    user = get_current_user()
    today = date.today()
    max_postpones = get_max_schedule_postpones()

    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month
    if not (1 <= month <= 12):
        return jsonify({"error": "month must be between 1 and 12"}), 400

    first_day = date(year, month, 1)
    _, last_dom = month_calendar.monthrange(year, month)
    last_day = date(year, month, last_dom)

    schedules = (CourseSchedule.query
                 .join(Course)
                 .filter(
                     Course.lecturer_id == user.id,
                     CourseSchedule.class_date >= first_day,
                     CourseSchedule.class_date <= last_day,
                 )
                 .all())
    schedules.sort(key=lambda s: (s.class_date, s.start_time or time.min, s.course.course_code))

    day_map = {}
    for sch in schedules:
        day_key = sch.class_date.isoformat()
        open_session = get_latest_open_session(sch.course_id, target_day=sch.class_date)

        item = {
            "schedule_id": sch.id,
            "course_id": sch.course_id,
            "course_code": sch.course.course_code,
            "course_name": sch.course.course_name,
            "class_date": day_key,
            "start_time": sch.start_time.strftime("%H:%M") if sch.start_time else None,
            "postpone_count": sch.postpone_count or 0,
            "postpone_limit": max_postpones,
            "venue": {
                "id": sch.venue.id,
                "name": sch.venue.name,
            } if sch.venue else None,
            "can_start": sch.class_date == today,
        }
        if open_session:
            item["open_session"] = {
                "id": open_session.id,
                "status": api_session_status(open_session.status),
                "seconds_remaining": open_session.seconds_remaining(),
            }

        day_map.setdefault(day_key, []).append(item)

    return jsonify({
        "year": year,
        "month": month,
        "month_name": month_calendar.month_name[month],
        "today": today.isoformat(),
        "weeks": month_calendar.monthcalendar(year, month),
        "days": {
            day_key: {
                "count": len(items),
                "items": items,
            }
            for day_key, items in day_map.items()
        },
    })


@app.route("/api/lecturer/calendar/day", methods=["GET"])
@login_required(role="lecturer")
def api_lecturer_calendar_day():
    user = get_current_user()
    today = date.today()
    max_postpones = get_max_schedule_postpones()
    target_date = parse_iso_date(request.args.get("date"))
    if not target_date:
        return jsonify({"error": "Valid date query parameter is required (YYYY-MM-DD)"}), 400

    day_start, day_end = get_day_bounds(target_date)
    schedules = (CourseSchedule.query
                 .join(Course)
                 .filter(
                     Course.lecturer_id == user.id,
                     CourseSchedule.class_date == target_date,
                 )
                 .all())
    schedules.sort(key=lambda s: (s.start_time or time.min, s.course.course_code))

    items = []
    for sch in schedules:
        open_session = get_latest_open_session(sch.course_id, target_day=target_date)

        sessions_for_day = (AttendanceSession.query
                            .filter(
                                AttendanceSession.course_id == sch.course_id,
                                AttendanceSession.start_time >= day_start,
                                AttendanceSession.start_time < day_end,
                            )
                            .order_by(AttendanceSession.start_time.desc())
                            .all())
        latest_session = sessions_for_day[0] if sessions_for_day else None
        enrolled_count = Enrollment.query.filter_by(course_id=sch.course_id).count()
        present_count = 0
        if latest_session:
            present_count = latest_session.records.filter(
                AttendanceRecord.status.in_(["PRESENT", "present"])
            ).count()

        item = {
            "schedule_id": sch.id,
            "course_id": sch.course_id,
            "course_code": sch.course.course_code,
            "course_name": sch.course.course_name,
            "class_date": sch.class_date.isoformat(),
            "start_time": sch.start_time.strftime("%H:%M") if sch.start_time else None,
            "postpone_count": sch.postpone_count or 0,
            "postpone_limit": max_postpones,
            "venue": {
                "id": sch.venue.id,
                "name": sch.venue.name,
                "latitude": sch.venue.latitude,
                "longitude": sch.venue.longitude,
            } if sch.venue else None,
            "can_start": sch.class_date == today,
            "enrolled": enrolled_count,
            "present": present_count,
            "attendance_rate": round((present_count / enrolled_count * 100), 1) if enrolled_count else 0,
            "latest_session": {
                "id": latest_session.id,
                "status": api_session_status(latest_session.status),
                "start_time": latest_session.start_time.isoformat(),
                "end_time": latest_session.end_time.isoformat(),
                "seconds_remaining": latest_session.seconds_remaining(),
            } if latest_session else None,
        }
        if open_session:
            item["open_session"] = {
                "id": open_session.id,
                "status": api_session_status(open_session.status),
                "seconds_remaining": open_session.seconds_remaining(),
            }

        items.append(item)

    return jsonify({
        "date": target_date.isoformat(),
        "items": items,
    })


@app.route("/api/lecturer/schedule/<int:schedule_id>/postpone", methods=["POST"])
@login_required(role="lecturer")
def api_postpone_schedule(schedule_id):
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    max_postpones = get_max_schedule_postpones()

    schedule = (CourseSchedule.query
                .join(Course)
                .filter(
                    CourseSchedule.id == schedule_id,
                    Course.lecturer_id == user.id,
                )
                .first())
    if not schedule:
        return jsonify({"error": "Schedule not found or access denied"}), 404

    # Block postpone if attendance was already taken on this date
    day_start = datetime.combine(schedule.class_date, time.min)
    day_end   = datetime.combine(schedule.class_date, time.max)
    has_session = AttendanceSession.query.filter(
        AttendanceSession.course_id == schedule.course_id,
        AttendanceSession.start_time >= day_start,
        AttendanceSession.start_time <= day_end,
    ).first()
    if has_session:
        return jsonify({"error": "Cannot postpone — attendance has already been taken for this class."}), 400

    if (schedule.postpone_count or 0) >= max_postpones:
        return jsonify({"error": f"Can only postpone {max_postpones} times"}), 400

    new_date = parse_iso_date(data.get("new_date"))
    if not new_date:
        return jsonify({"error": "new_date is required in YYYY-MM-DD format"}), 400

    new_start_time = parse_iso_time(data.get("new_start_time"))
    if data.get("new_start_time") and not new_start_time:
        return jsonify({"error": "new_start_time must be HH:MM"}), 400

    venue_id = data.get("venue_id")
    if venue_id is not None:
        try:
            venue_id = int(venue_id)
        except (TypeError, ValueError):
            return jsonify({"error": "venue_id must be an integer"}), 400

        venue = db.session.get(Venue, venue_id)
        if not venue:
            return jsonify({"error": "Selected venue not found"}), 404
        schedule.venue_id = venue.id

    schedule.class_date = new_date
    if new_start_time:
        schedule.start_time = new_start_time
    schedule.postpone_count = (schedule.postpone_count or 0) + 1

    db.session.commit()
    return jsonify({
        "message": "Schedule postponed successfully",
        "schedule": {
            "id": schedule.id,
            "class_date": schedule.class_date.isoformat(),
            "start_time": schedule.start_time.strftime("%H:%M") if schedule.start_time else None,
            "venue_id": schedule.venue_id,
            "postpone_count": schedule.postpone_count,
        },
    })


@app.route("/api/lecturer/schedule/<int:schedule_id>/attendance", methods=["GET"])
@login_required(role="lecturer")
def api_schedule_attendance(schedule_id):
    user = get_current_user()
    schedule = (CourseSchedule.query
                .join(Course)
                .filter(
                    CourseSchedule.id == schedule_id,
                    Course.lecturer_id == user.id,
                )
                .first())
    if not schedule:
        return jsonify({"error": "Schedule not found or access denied"}), 404

    target_date = parse_iso_date(request.args.get("date")) or schedule.class_date
    day_start, day_end = get_day_bounds(target_date)

    sessions = (AttendanceSession.query
                .filter(
                    AttendanceSession.course_id == schedule.course_id,
                    AttendanceSession.start_time >= day_start,
                    AttendanceSession.start_time < day_end,
                )
                .order_by(AttendanceSession.start_time.desc())
                .all())

    session_payload = []
    for sess in sessions:
        sess.close_if_expired()
        records = []
        for r in sess.records.order_by(AttendanceRecord.timestamp.asc()).all():
            unc, device_claim = reported_uncertainty(r)
            records.append({
                "student_name": r.student.name,
                "student_email": r.student.email,
                "timestamp": r.timestamp.strftime("%H:%M:%S"),
                "distance_m": round(r.distance_m, 1),
                # The uncertainty the geofence judged distance_m against, not
                # the handset's self-report. See reported_uncertainty().
                "accuracy_m": round(unc, 1),
                "device_accuracy_m": (round(device_claim, 1)
                                      if device_claim is not None else None),
                "status": api_record_status(r.status),
                "verify_state": r.verify_state,
                "confidence": round(r.confidence, 3) if r.confidence is not None else None,
            })
        session_payload.append({
            "session_id": sess.id,
            "status": api_session_status(sess.status),
            "start_time": sess.start_time.isoformat(),
            "end_time": sess.end_time.isoformat(),
            "records": records,
        })

    return jsonify({
        "date": target_date.isoformat(),
        "schedule": {
            "id": schedule.id,
            "course_id": schedule.course_id,
            "course_code": schedule.course.course_code,
            "course_name": schedule.course.course_name,
            "start_time": schedule.start_time.strftime("%H:%M") if schedule.start_time else None,
            "venue": schedule.venue.name if schedule.venue else None,
        },
        "sessions": session_payload,
    })

@app.route("/api/lecturer/courses", methods=["GET"])
@login_required(role="lecturer")
def api_lecturer_courses():
    """Return all courses taught by the current lecturer."""
    user = get_current_user()
    courses = Course.query.filter_by(lecturer_id=user.id).all()

    result = []
    for c in courses:
        # Find the most recent open session (if any)
        active = get_latest_open_session(c.id)
        location_set = bool(c.location_lat or c.location_lng)

        result.append({
            "id":           c.id,
            "course_code":  c.course_code,
            "course_name":  c.course_name,
            "location_lat": c.location_lat,
            "location_lng": c.location_lng,
            "radius":       c.radius if c.radius else app.config["GEOFENCE_RADIUS_METRES"],
            "location_set": location_set,
            "enrolled":     c.enrollments.count(),
            "active_session": {
                "id":                active.id,
                "seconds_remaining": active.seconds_remaining(),
                "status":            api_session_status(active.status),
            } if active and is_session_open(active.status) else None
        })

    return jsonify({"courses": result})


# ─────────────────────────────────────────────────────────────
# API — SET LOCATION  (lecturer GPS geofence)
# ─────────────────────────────────────────────────────────────


@app.route("/api/lecturer/session/start", methods=["POST"])
@login_required(role="lecturer")
def api_start_session():
    """
    Lecturer starts an attendance session for a course.

    Required JSON body:
        course_id   : int
        duration_min: int   (5 – 60)
        venue_id    : int (recommended)

    Optional:
        schedule_id : int
        location_lat/location_lng fallback when venue_id is not supplied
    """
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    course_id_raw    = data.get("course_id")
    schedule_id_raw  = data.get("schedule_id")
    venue_id_raw     = data.get("venue_id")
    duration_min_raw = data.get("duration_min")
    lat              = data.get("location_lat")
    lng              = data.get("location_lng")

    try:
        course_id = int(course_id_raw) if course_id_raw is not None else None
        duration_min = int(duration_min_raw) if duration_min_raw is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "course_id and duration_min must be integers"}), 400

    schedule_id = None
    if schedule_id_raw is not None:
        try:
            schedule_id = int(schedule_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "schedule_id must be an integer"}), 400

    venue_id = None
    if venue_id_raw is not None:
        try:
            venue_id = int(venue_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "venue_id must be an integer"}), 400

    if lat is not None or lng is not None:
        try:
            lat = float(lat) if lat is not None else None
            lng = float(lng) if lng is not None else None
        except (TypeError, ValueError):
            return jsonify({"error": "location_lat and location_lng must be numeric"}), 400

    # ---- Basic validation ----
    if not all([course_id, duration_min]):
        return jsonify({"error": "course_id and duration_min are required"}), 400

    if not (app.config["MIN_SESSION_DURATION"] <= duration_min <= app.config["MAX_SESSION_DURATION"]):
        return jsonify({
            "error": f"Duration must be between {app.config['MIN_SESSION_DURATION']} and "
                     f"{app.config['MAX_SESSION_DURATION']} minutes"
        }), 400

    course = Course.query.filter_by(id=course_id, lecturer_id=user.id).first()
    if not course:
        return jsonify({"error": "Course not found or access denied"}), 404

    schedule = None
    if schedule_id is not None:
        schedule = (CourseSchedule.query
                    .join(Course)
                    .filter(
                        CourseSchedule.id == schedule_id,
                        Course.lecturer_id == user.id,
                    )
                    .first())
        if not schedule or schedule.course_id != course.id:
            return jsonify({"error": "Schedule not found for this course"}), 404
        if schedule.class_date != date.today():
            return jsonify({
                "error": "Attendance can only be started for today's scheduled class. Use postpone to reschedule."
            }), 400

    venue = None
    if venue_id is not None:
        venue = db.session.get(Venue, venue_id)
        if not venue:
            return jsonify({"error": "Selected venue was not found"}), 404
    elif schedule and schedule.venue_id:
        venue = schedule.venue

    # Geofence comes from the venue — admin must set GPS on the venue first.
    if not venue:
        return jsonify({"error": "A venue must be selected to start attendance."}), 400
    if not venue.latitude or not venue.longitude:
        return jsonify({
            "error": f"GPS not set for venue \"{venue.name}\". "
                     "Ask admin to set GPS coordinates in the Locations tab."
        }), 400

    # ---- One session per course per day ----
    today_start = datetime.combine(date.today(), time.min)
    today_end   = datetime.combine(date.today(), time.max)
    existing_today = AttendanceSession.query.filter(
        AttendanceSession.course_id == course_id,
        AttendanceSession.start_time >= today_start,
        AttendanceSession.start_time <= today_end,
    ).first()
    if existing_today:
        return jsonify({
            "error": "An attendance session has already been run for this course today."
        }), 400

    # ---- Close any lingering open sessions for this course ----
    old_sessions = (AttendanceSession.query
                    .filter(
                        AttendanceSession.course_id == course_id,
                        AttendanceSession.status.in_(OPEN_DB_STATUSES),
                    )
                    .all())
    for s in old_sessions:
        s.status = CLOSED_DB_STATUS

    # ---- Create new session ----
    now      = datetime.utcnow()
    new_sess = AttendanceSession(
        course_id  = course_id,
        venue_id   = venue.id,
        start_time = now,
        end_time   = now + timedelta(minutes=duration_min),
        status     = get_open_write_status()
    )
    db.session.add(new_sess)
    db.session.commit()

    # Each lingering session we just closed is a session-close event: recompute
    # its venue's fence now that it is final. After the commit above, so a
    # recompute failure cannot roll back the new session.
    for s in old_sessions:
        s._on_closed(actor_id=user.id)

    return jsonify({
        "message":           "Session started",
        "session_id":        new_sess.id,
        "start_time":        new_sess.start_time.isoformat(),
        "end_time":          new_sess.end_time.isoformat(),
        "status":            "OPEN",
        "schedule_id":       schedule.id if schedule else None,
        "venue": {
            "id": venue.id,
            "name": venue.name,
            "latitude": venue.latitude,
            "longitude": venue.longitude,
        } if venue else None,
        "seconds_remaining": new_sess.seconds_remaining(),
        "geofence_radius_m": app.config["GEOFENCE_RADIUS_METRES"]
    }), 201


@app.route("/api/lecturer/session/<int:session_id>", methods=["GET"])
@login_required(role="lecturer")
def api_session_detail(session_id):
    """Return session status and attendance summary for lecturer."""
    user  = get_current_user()
    sess  = db.session.get(AttendanceSession, session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404

    # Verify ownership through course
    if sess.course.lecturer_id != user.id:
        return jsonify({"error": "Forbidden"}), 403

    sess.close_if_expired()

    enrolled_count = (Enrollment.query
                      .filter_by(course_id=sess.course_id)
                      .count())
    present_count = sess.records.filter(
        AttendanceRecord.status.in_(["PRESENT", "present"])
    ).count()

    records = []
    for r in sess.records.all():
        unc, device_claim = reported_uncertainty(r)
        records.append({
            "student_name": r.student.name,
            "student_email": r.student.email,
            "timestamp":    r.timestamp.strftime("%H:%M:%S"),
            "distance_m":   round(r.distance_m, 1),
            # acc_eff, falling back to the device figure only on pre-Stage-1
            # rows. This is the lecturer's review screen and `needs_review`
            # below is computed from a decision that used acc_eff, so showing
            # the handset's self-report here made the flag look unexplained.
            "accuracy_m":   round(unc, 1),
            # The raw handset claim, when it differs. Present so the gap
            # between the two is visible rather than quietly reconciled: a
            # row reading accuracy_m 80 / device_accuracy_m 12 is a phone
            # that was confidently wrong, and that is worth seeing.
            "device_accuracy_m": (round(device_claim, 1)
                                  if device_claim is not None else None),
            "spread_m":     (round(r.spread_m, 1)
                             if r.spread_m is not None else None),
            "sample_count": r.sample_count,
            "status":       api_record_status(r.status),
            # Stage 2 — None on rows written before three-state verification.
            "verify_state": r.verify_state,
            "confidence":   round(r.confidence, 3) if r.confidence is not None else None,
            "needs_review": r.verify_state == STATE_AMBIGUOUS,
            # Stage 4 — how this row was actually established.
            "qr_verified":  bool(r.qr_verified),
            "manual":       r.verify_state == STATE_MANUAL,
            # Stage 4 remediation — an AMBIGUOUS row can mean two very
            # different things and the lecturer could not tell them apart:
            # a fix at the edge of the room, or one the geofence rejected
            # outright that a scanned token overruled. `distance_m` alone did
            # not disambiguate, because this payload carried no radius to
            # compare it against. Both are supplied now.
            "radius_m":     round(effective_fence_radius(sess.venue), 1),
            # None, not False, when geo_state was never recorded. A row written
            # before this column existed may well have been an override under
            # the old flat-500m rule, and there is no way to tell from the row.
            # Reporting False there would be this payload asserting a fact it
            # does not have -- the same confident-but-unfounded claim the whole
            # stage exists to remove. MANUAL rows are exempt: geo_state is NULL
            # there because no geofence ran, and `manual` already explains them.
            "qr_override":  None if (r.geo_state is None
                                     and r.verify_state != STATE_MANUAL)
                            else (r.geo_state == STATE_OUTSIDE
                                  and r.verify_state == STATE_AMBIGUOUS),
            # bool() on purpose here, and the asymmetry is deliberate: an
            # unknown override is reported as unknown, but an unknown
            # calibration eligibility is reported as not-eligible, because that
            # is the safe reading and Stage 5 must act on it the same way.
            "qr_trusted":   bool(r.qr_trusted),
        })

    needs_review = sum(1 for r in records if r["needs_review"])

    # Venue label, including floor — one of the three jobs the floor column
    # exists for. A report that says "LT-2" when there are three LT-2s stacked
    # above one another is not a report.
    v = sess.venue
    venue_label = None
    if v is not None:
        parts = [v.name]
        if v.building:
            parts.append(v.building)
        if v.floor is not None:
            parts.append(f"floor {v.floor}")
        venue_label = " · ".join(str(p) for p in parts if p)

    return jsonify({
        "session_id":        sess.id,
        "course_code":       sess.course.course_code,
        "course_name":       sess.course.course_name,
        "status":            api_session_status(sess.status),
        "seconds_remaining": sess.seconds_remaining(),
        "enrolled":          enrolled_count,
        "present":           present_count,
        "attendance_rate":   round((present_count / enrolled_count * 100), 1) if enrolled_count else 0,
        "needs_review":      needs_review,
        "venue": {
            "id":       v.id if v else None,
            "name":     v.name if v else None,
            "building": v.building if v else None,
            "floor":    v.floor if v else None,
            "label":    venue_label,
        },
        "records":           records
    })


@app.route("/api/lecturer/session/<int:session_id>/manual-mark", methods=["POST"])
@login_required(role="lecturer")
def api_session_manual_mark(session_id):
    """
    Mark a student present by lecturer assertion, with no GPS and no scan.

    Stage 4 makes QR mandatory wherever GPS cannot decide -- which, until a
    venue calibrates, is everywhere. That closes a real hole and opens a
    smaller one: a student with a dead battery, a broken camera, or a cracked
    screen, standing in the room, now has no path at all. Before this endpoint
    the only code that could create an attendance record was the student's own
    submission, so the enforcement had no escape hatch and would have turned a
    hardware failure into an absence.

    The escape hatch is deliberately a *different* mechanism rather than a
    looser geofence: it requires a human who can see the room, it names that
    human in the audit log, and it is visibly not a GPS observation.

    This is the one place in the system where a record is written from no
    measurement, so three safeguards sit on it:

      * verify_state = MANUAL, never INSIDE. No reader can mistake an
        assertion for a fix.
      * qr_verified = False, so Stage 5's calibration -- which feeds only on
        QR-verified rows -- can never learn a fence from coordinates nobody
        measured. That would be the purest form of the feedback poisoning
        Stage 5's guards exist to prevent.
      * sample_count = 0, which is the machine-readable version of the same
        statement and keeps Stage 6's zero-scatter detector (sample_count >= 5)
        from flagging these as spoofed.

    The stored lat/lng are the venue's own centre, because lat/lng are
    NOT NULL and SQLite cannot drop that. This is the same shape as the
    `demo_coords` fallback the project record flags as a latent backdoor, and
    it is only safe because of the three markers above -- so any future reader
    filtering these rows must filter on verify_state, never on coordinates.
    Stage 6's cluster detector must exclude MANUAL rows for exactly this
    reason: they all share one point by construction.
    """
    user = get_current_user()
    sess = db.session.get(AttendanceSession, session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    if sess.course.lecturer_id != user.id:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    student_id = data.get("student_id")
    reason     = (data.get("reason") or "").strip()

    if student_id is None:
        return jsonify({"error": "student_id is required"}), 400
    try:
        student_id = int(student_id)
    except (TypeError, ValueError):
        return jsonify({"error": "student_id must be an integer"}), 400

    if not reason:
        return jsonify({
            "error": "A reason is required. This record is created without any "
                     "location evidence, so the reason is the only account of "
                     "why it exists."
        }), 400

    student = db.session.get(User, student_id)
    if not student or student.role != "student":
        return jsonify({"error": "Student not found"}), 404

    enrolled = Enrollment.query.filter_by(
        student_id=student_id, course_id=sess.course_id
    ).first()
    if not enrolled:
        return jsonify({"error": "That student is not enrolled in this course"}), 403

    duplicate = AttendanceRecord.query.filter_by(
        session_id=session_id, student_id=student_id
    ).first()
    if duplicate:
        return jsonify({
            "error": "That student already has a record for this session"
        }), 409

    venue = sess.venue
    if not venue or venue.latitude is None or venue.longitude is None:
        return jsonify({"error": "Venue GPS not configured. Contact admin."}), 400

    record = AttendanceRecord(
        session_id   = session_id,
        student_id   = student_id,
        lat          = venue.latitude,
        lng          = venue.longitude,
        accuracy     = 0.0,
        distance_m   = 0.0,
        status       = "PRESENT",
        sample_count = 0,          # no samples were taken; see the docstring
        spread_m     = None,
        acc_eff      = None,
        verify_state = STATE_MANUAL,
        confidence   = None,
        qr_verified  = False,
        # Stage 5 reads qr_trusted, NOT qr_verified -- so the exclusion has to
        # be written on this column or it is not written at all. Leaving it NULL
        # was safe only for as long as Stage 5's query says `.is_(True)`; a
        # later `!= True` or `isnot(False)` would admit every NULL row, and this
        # particular row is the worst possible calibration input, because
        # lat/lng above are the *surveyed* centre. Feeding it back would drag
        # the learned centre towards the survey -- the exact offset §1.6 exists
        # to correct -- and the more a lecturer manually marks a venue with a
        # bad survey, the harder that survey becomes to fix. False, explicitly.
        qr_trusted   = False,
        # No geofence ran, so there is no geometric verdict to record. NULL here
        # means "not measured", which is why the lecturer view derives
        # qr_override from geo_state rather than storing it.
        geo_state    = None,
    )
    db.session.add(record)
    log_admin_action(
        user.id,
        "manual_attendance_mark",
        target_type="attendance_record",
        target_id=student_id,
        details={
            "session_id": session_id,
            "course_id":  sess.course_id,
            "student_id": student_id,
            "student":    student.email,
            "venue_id":   venue.id,
            "reason":     reason[:500],
        },
    )
    try:
        db.session.commit()
    except IntegrityError:
        # Same race as the student path: the duplicate check above is not atomic
        # with this insert. Narrower here -- it needs two lecturers, or one
        # double-clicking -- but the unique constraint is the same and so is the
        # right answer, which is the 409 this endpoint already returns.
        db.session.rollback()
        return jsonify({
            "error": "This student already has an attendance record for this session"
        }), 409

    return jsonify({
        "message":      "Attendance recorded manually",
        "record_id":    record.id,
        "student_id":   student_id,
        "student_name": student.name,
        "verify_state": STATE_MANUAL,
        "qr_verified":  False,
        "note": "Recorded on lecturer assertion, with no location evidence. "
                "This record is excluded from venue calibration.",
        "timestamp":    record.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }), 201


@app.route("/api/lecturer/session/<int:session_id>/qr-token", methods=["GET"])
@login_required(role="lecturer")
def api_session_qr_token(session_id):
    """Return the current rotating QR token and attendance URL."""
    user = get_current_user()
    sess = db.session.get(AttendanceSession, session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    if sess.course.lecturer_id != user.id:
        return jsonify({"error": "Forbidden"}), 403

    sess.close_if_expired()
    if not is_session_open(sess.status):
        return jsonify({"error": "Session is closed"}), 400

    token = generate_qr_token(sess.id)
    now_ts = datetime.utcnow().timestamp()
    window_end = ((int(now_ts) // QR_TOKEN_INTERVAL) + 1) * QR_TOKEN_INTERVAL
    expires_in = max(0, int(window_end - now_ts))

    attend_url = f"{request.scheme}://{request.host}/attend?session={sess.id}&t={token}"

    return jsonify({
        "token":             token,
        "expires_in":        expires_in,
        "attend_url":        attend_url,
        "session_id":        sess.id,
        "course_code":       sess.course.course_code,
        "seconds_remaining": sess.seconds_remaining(),
    })


# ─────────────────────────────────────────────────────────────
# API — STUDENT
# ─────────────────────────────────────────────────────────────

@app.route("/api/student/courses", methods=["GET"])
@login_required(role="student")
def api_student_courses():
    """Return enrolled courses with live session status."""
    user = get_current_user()

    enrolled = (Enrollment.query
                .filter_by(student_id=user.id)
                .join(Course)
                .all())

    result = []
    for enr in enrolled:
        c = enr.course

        # Lazily close expired sessions
        active = get_latest_open_session(c.id)

        # Has the student already marked for the active session?
        already_marked = False
        if active and is_session_open(active.status):
            already_marked = AttendanceRecord.query.filter_by(
                session_id=active.id, student_id=user.id
            ).first() is not None

        result.append({
            "course_id":       c.id,
            "course_code":     c.course_code,
            "course_name":     c.course_name,
            "lecturer_name":   c.lecturer.name,
            "session": {
                "id":                active.id,
                "status":            api_session_status(active.status),
                "seconds_remaining": active.seconds_remaining(),
                "already_marked":    already_marked,
            } if active and is_session_open(active.status) else None
        })

    return jsonify({"courses": result})




@app.route("/api/student/attendance/mark", methods=["POST"])
@login_required(role="student")
def api_mark_attendance():
    """
    Core attendance submission endpoint.

    Required JSON body:
        session_id : int
        lat        : float   (student GPS latitude)
        lng        : float   (student GPS longitude)
        accuracy   : float   (GPS accuracy in metres)

    Optional JSON body:
        samples    : [{lat, lng, acc}, ...]   multi-sample GPS capture

    When `samples` is supplied the position is the robust centroid of the
    samples and the uncertainty is derived from their observed scatter,
    which is far harder to fool and far more honest than a single fix.
    When it is absent the flat lat/lng/accuracy fields are used as a
    one-element sample, so older clients behave exactly as before.

    Validation pipeline:
        1. Session exists and is OPEN
        2. Current time within allowed window
        3. Student has not already submitted
        4. Student is enrolled in the course
        5. Venue has GPS configured
        6. Effective accuracy is within 2x the venue radius
        7. Three-state geofence decision — only OUTSIDE is rejected
        8. Venue collision — does another venue explain this fix better?

    The response carries `verify_state` (INSIDE / AMBIGUOUS) and a
    `confidence` in 0.0–1.0. AMBIGUOUS is an accepted submission flagged for
    the lecturer, not a rejection: the honest answer to "is this student in
    the room" is sometimes "cannot tell from GPS alone".

    Step 8 can only downgrade INSIDE to AMBIGUOUS, never reject — when it
    fires, `contested` names the venue that competed and why.

    Step 8b then decides whether a QR scan was required, which is knowable
    only once the verdict and the contest exist. A confidently-inside fix at a
    calibrated, isolated venue still needs no scan. At an uncalibrated venue a
    valid scan additionally outranks an OUTSIDE verdict (step 7b), because the
    survey it contradicts may carry the very offset that produced the
    rejection.
    """
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    session_id = data.get("session_id")
    lat        = data.get("lat")
    lng        = data.get("lng")
    accuracy   = data.get("accuracy")
    raw_samples = data.get("samples")

    has_samples = isinstance(raw_samples, list) and len(raw_samples) > 0

    # ── Step 0: presence check ──────────────────────────────────────
    if session_id is None:
        return jsonify({"error": "session_id is required"}), 400
    if not has_samples and any(v is None for v in [lat, lng, accuracy]):
        return jsonify({"error": "session_id, lat, lng, and accuracy are required"}), 400

    try:
        session_id = int(session_id)
        lat      = None if lat is None else float(lat)
        lng      = None if lng is None else float(lng)
        accuracy = None if accuracy is None else float(accuracy)
    except (TypeError, ValueError):
        return jsonify({"error": "session_id must be an integer and lat/lng/accuracy must be numeric"}), 400

    samples = normalise_samples(raw_samples, lat, lng, accuracy)
    if not samples:
        return jsonify({
            "error": "No usable GPS reading was received. "
                     "Please try again in an open area."
        }), 400

    fix      = robust_fix(samples)
    lat      = fix["lat"]
    lng      = fix["lng"]
    acc_eff  = fix["acc_eff"]
    # Keep the raw device figure for the record; the decision uses acc_eff.
    if accuracy is None:
        accuracy = acc_eff

    # ── Step 1: Session exists and is OPEN ─────────────────────────
    sess = db.session.get(AttendanceSession, session_id)
    if not sess:
        return jsonify({"error": "Attendance session not found"}), 404

    sess.close_if_expired()
    if not is_session_open(sess.status):
        return jsonify({"error": "This attendance session has closed"}), 400

    # ── Step 2: Within time window ────────────────────────────────────
    if sess.is_expired():
        return jsonify({"error": "Attendance window has expired"}), 400

    # ── Step 2b: QR token validation (when provided) ─────────────────
    # An invalid token is rejected here regardless of whether one was
    # required: presenting a bad code is a distinct, reportable error, and
    # silently ignoring it would tell a student who did scan that their scan
    # did not matter. Whether a token was *needed* cannot be known yet -- that
    # depends on the verdict -- so the outcome is carried to step 8b.
    qr_token = data.get("qr_token")
    qr_ok    = False
    if qr_token:
        if not validate_qr_token(session_id, qr_token):
            return jsonify({
                "error": "Invalid or expired QR code. "
                         "Ask the lecturer to show the latest code."
            }), 403
        qr_ok = True

    # ── Step 3: Duplicate submission check ───────────────────────────
    duplicate = AttendanceRecord.query.filter_by(
        session_id=session_id, student_id=user.id
    ).first()
    if duplicate:
        return jsonify({"error": "You have already submitted attendance for this session"}), 409

    # ── Step 4: Student enrolled in course ───────────────────────────
    enrolled = Enrollment.query.filter_by(
        student_id=user.id, course_id=sess.course_id
    ).first()
    if not enrolled:
        return jsonify({"error": "You are not enrolled in this course"}), 403

    # ── Step 5: Venue and radius ──────────────────────────────────────
    # Resolved before the accuracy check because the accuracy cap is now
    # relative to this venue's radius, not a flat campus-sized number.
    venue = sess.venue
    if not venue or not venue.latitude or not venue.longitude:
        return jsonify({"error": "Venue GPS not configured. Contact admin."}), 400

    base_radius = effective_fence_radius(venue)
    calibrated = is_calibrated(venue)

    # ── Step 6: GPS accuracy check — relative to the fence ────────────
    # A reading whose uncertainty exceeds twice the fence radius carries
    # essentially no information about that fence: the 95% ellipse swallows
    # the room and everything around it. The old flat 500m cap against a 50m
    # radius is what produced a 550m effective fence.
    # MAX_GPS_ACCURACY_METRES survives as an absolute ceiling, so an admin who
    # draws a very large venue cannot accidentally disable the check.
    max_acc = min(2.0 * base_radius, float(app.config["MAX_GPS_ACCURACY_METRES"]))
    if acc_eff > max_acc:
        return jsonify({
            "error": f"GPS signal too weak (accuracy: +/-{round(acc_eff)}m, "
                     f"limit {round(max_acc)}m for this venue). "
                     f"Please move towards a window or outdoors and try again.",
            "debug": {
                "acc_eff_m": round(acc_eff, 1),
                "max_acc_m": round(max_acc, 1),
                "radius_m":  round(base_radius, 1),
                "samples":      fix["sample_count"],
                "samples_used": fix["used_count"],
                "spread_m":     round(fix["spread_m"], 1),
            }
        }), 400

    # ── Step 7: Geofence decision — three states, not a boolean ───────
    # Measured from the EFFECTIVE centre: the learned one at a calibrated
    # venue, the survey otherwise. This is what finally moves the fence off a
    # bad survey -- is_calibrated already gated confidence and the override, but
    # every distance was still taken from the surveyed point until now.
    fence_lat, fence_lng = effective_fence_centre(venue)
    distance = haversine_distance(lat, lng, fence_lat, fence_lng)
    verdict  = geofence_decision(distance, acc_eff, base_radius, calibrated)

    # ── Step 7b: QR as ground truth at an uncalibrated venue (D9) ─────
    # A scanned token outranks a survey that may carry the very offset this
    # rejection is made of. The override lands on AMBIGUOUS, never INSIDE:
    # the student is credited and the record is kept -- which is what lets
    # Stage 5 eventually move the fence -- but the disagreement between
    # geometry and the token is preserved for the lecturer to see.
    qr_overrode = False
    if (verdict["verify_state"] == STATE_OUTSIDE
            and qr_ok
            and qr_ground_truth_override(distance, calibrated, base_radius)):
        verdict["verify_state"] = STATE_AMBIGUOUS
        verdict["qr_override"]  = True
        qr_overrode = True

    if verdict["verify_state"] == STATE_OUTSIDE:
        hint = ""
        if distance > 500:
            hint = (
                " Desktop/laptop browsers use IP-based location which can be "
                "off by kilometres. Use a phone with real GPS for accurate results."
            )
        elif not calibrated and not qr_ok:
            # The D9 path the student can actually act on. Without this line the
            # override exists but is invisible, and a venue with a bad survey
            # still looks like a dead end.
            hint = (
                " If you are in the room, scan the QR code the lecturer is "
                "showing -- this venue's location has not been confirmed yet, "
                "so a scan counts as proof and will be accepted."
            )
        return jsonify({
            "error": f"You are {round(distance)}m from the classroom "
                     f"(GPS accuracy: +/-{round(acc_eff)}m), which is outside "
                     f"the {round(base_radius)}m attendance area even allowing "
                     f"for that uncertainty.{hint}",
            "debug": {
                # The venue's centre is deliberately absent. This branch is
                # reachable with no QR token at all, so it answered "where is
                # the fence?" for anyone who wanted to know: submitting the
                # returned coordinates back verbatim drives d to 0 from
                # anywhere on earth, which makes every distance-based bound in
                # this file decorative. distance_m and radius_m stay, because
                # the student needs to know how far off they are and the error
                # message says both in prose anyway -- a distance alone is a
                # circle of solutions, not a location.
                "your_coords": {"lat": round(lat, 6), "lng": round(lng, 6)},
                "distance_m": round(distance, 1),
                "radius_m": round(base_radius, 1),
                "acc_eff_m": round(acc_eff, 1),
                "verify_state": verdict["verify_state"],
                "geo_state": verdict["geo_state"],
                "confidence": round(verdict["confidence"], 3),
                "samples": fix["sample_count"],
                "samples_used": fix["used_count"],
                "spread_m": round(fix["spread_m"], 1),
            }
        }), 400

    # ── Step 8: Venue collision — nearest-venue-wins ──────────────────
    # Steps 6 and 7 asked "could this fix be in the session's venue?". They
    # never asked whether some *other* venue explains it better. Two adjacent
    # halls with 50m fences 30m apart share most of their area, and a stacked
    # pair shares all of it — in both cases a confident INSIDE is a confident
    # guess. This can only downgrade to AMBIGUOUS; it never rejects, because
    # being near two rooms is not evidence of being outside both.
    contest = None
    if verdict["verify_state"] != STATE_OUTSIDE:
        peers = Venue.query.filter(
            Venue.id != venue.id,
            Venue.latitude.isnot(None),
            Venue.longitude.isnot(None),
        ).all()
        contest = contested_by(lat, lng, venue, peers,
                               app.config["GEOFENCE_RADIUS_METRES"])
        if contest is not None and verdict["verify_state"] == STATE_INSIDE:
            verdict["verify_state"] = STATE_AMBIGUOUS
            verdict["contested"] = True

    # ── Step 8b: Adaptive QR gate (Stage 4) ───────────────────────────
    # Enforcement happens here, not at step 2b, because whether a token is
    # required depends on the verdict and the contest -- neither of which
    # exists until now. A confidently-inside fix at a calibrated, isolated
    # venue still passes with no scan at all; that is the adaptive behaviour.
    gate = qr_required(sess, venue, verdict["verify_state"], acc_eff,
                       contest, app.config["GEOFENCE_RADIUS_METRES"])
    if gate["required"] and not qr_ok:
        return jsonify({
            "error": "Scan the QR code the lecturer is showing to confirm "
                     "you are in this room. GPS alone cannot confirm it here.",
            "qr_required": True,
            "reasons": gate["reasons"],
            "debug": {
                "distance_m":   round(distance, 1),
                "radius_m":     round(base_radius, 1),
                "acc_eff_m":    round(acc_eff, 1),
                "verify_state": verdict["verify_state"],
                "calibrated":   calibrated,
                "contested":    contest,
            }
        }), 403

    # ── All checks passed — record attendance ─────────────────────────
    # AMBIGUOUS submissions are accepted and stored with their confidence, then
    # surfaced to the lecturer for review. Rejecting the honest middle band
    # would punish students for the sensor's limits; hiding it would be the
    # dishonesty this stage exists to remove. Stage 4 escalates these to a QR
    # scan rather than a silent pass.
    #
    # qr_trusted is computed here and not derived later, because `distance` and
    # `qr_overrode` are both in scope only at this point. Deriving it from the
    # stored row would mean re-reading the venue's radius, which Stage 5 may
    # have changed since -- so a row's calibration eligibility would silently
    # depend on when you asked. Freezing it at write time keeps the answer the
    # one that was true about this submission.
    qr_trusted = qr_is_calibration_grade(qr_ok, qr_overrode, distance,
                                         base_radius)
    record = AttendanceRecord(
        session_id   = session_id,
        student_id   = user.id,
        lat          = lat,
        lng          = lng,
        accuracy     = accuracy,
        distance_m   = distance,
        status       = "PRESENT",
        sample_count = fix["sample_count"],
        spread_m     = fix["spread_m"],
        acc_eff      = acc_eff,
        verify_state = verdict["verify_state"],
        geo_state    = verdict["geo_state"],
        confidence   = verdict["confidence"],
        qr_verified  = qr_ok,
        qr_trusted   = qr_trusted,
    )
    db.session.add(record)
    try:
        db.session.commit()
    except IntegrityError:
        # The step-3 duplicate check is not atomic with this insert, and the gap
        # is an 8-second GPS capture wide. Two concurrent submissions from the
        # same student both pass step 3, and the loser violates
        # UniqueConstraint("session_id", "student_id") here.
        #
        # Unhandled, that is a 500 on the busiest endpoint in the system -- for
        # a student whose attendance the winning request just recorded
        # successfully. The intended answer already exists twelve steps up, so
        # give it: the record is there, the duplicate is refused, nothing is
        # lost.
        #
        # This matters more in production than it did locally. The local dev
        # server handles one request at a time, so the window barely exists;
        # Vercel runs concurrent invocations in separate processes, so it is
        # genuinely open. F3 removed the client-side cause (the 30s poll handing
        # the student an enabled duplicate button mid-capture), but a cached old
        # client, a double tap, or a retry still reaches it, and the server must
        # not answer a race with a 500.
        db.session.rollback()
        return jsonify({
            "error": "You have already submitted attendance for this session"
        }), 409

    # ── Step 9: Auto-close session if 100% attendance reached ────────
    total_enrolled = Enrollment.query.filter_by(course_id=sess.course_id).count()
    total_present = AttendanceRecord.query.filter_by(session_id=session_id).count()
    if total_enrolled > 0 and total_present >= total_enrolled:
        sess.status = CLOSED_DB_STATUS
        db.session.commit()
        # A session-close event like any other. The student submitting the last
        # mark is not an admin, so this is attributed to the course lecturer
        # (via _on_closed's default), and recompute cannot raise.
        sess._on_closed()

    return jsonify({
        "message":     "Attendance marked successfully",
        "status":      "present",
        "distance_m":  round(distance, 1),
        "accuracy_m":  round(acc_eff, 1),
        "samples":     fix["sample_count"],
        "verify_state": verdict["verify_state"],
        "geo_state":    verdict["geo_state"],
        "confidence":   round(verdict["confidence"], 3),
        "calibrated":   verdict["calibrated"],
        "contested":    contest,
        "qr_verified":  qr_ok,
        "qr_override":  qr_overrode,
        "qr_trusted":   qr_trusted,
        "timestamp":   record.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    }), 201




# ─────────────────────────────────────────────────────────────
# API — ADMIN: LOCATIONS (venues)
# ─────────────────────────────────────────────────────────────

@app.route("/api/admin/locations", methods=["GET"])
@login_required(role="admin")
def api_admin_list_locations():
    venues = Venue.query.order_by(Venue.name.asc()).all()
    result = []
    for v in venues:
        schedule_count = CourseSchedule.query.filter_by(venue_id=v.id).count()

        # Stage 5 — what, if anything, has been learned about this fence.
        # The delta between the learned centre and the survey is the single
        # most informative number here: it is how wrong Google's map of this
        # spot is, measured in metres, and it is what tells an admin whether a
        # pending review is a bad survey or a gaming attempt.
        cal = None
        if is_calibrated(v):
            cal = {
                "lat": v.cal_lat, "lng": v.cal_lng,
                "radius": round(v.cal_radius, 2),
                "n": v.cal_n,
                "updated_at": v.cal_updated_at.isoformat() if v.cal_updated_at else None,
                "displacement_m": (round(haversine_distance(
                    v.cal_lat, v.cal_lng, v.latitude, v.longitude), 1)
                    if v.latitude is not None and v.longitude is not None else None),
            }
        review = None
        if v.cal_review:
            try:
                review = json.loads(v.cal_review)
            except (ValueError, TypeError):
                review = None

        result.append({
            "id": v.id,
            "name": v.name,
            "latitude": v.latitude,
            "longitude": v.longitude,
            "radius": v.radius,
            "building": v.building,
            "floor": v.floor,
            "schedule_count": schedule_count,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "calibrated": is_calibrated(v),
            "calibration": cal,
            "calibration_review": review,
        })
    return jsonify({"locations": result})


@app.route("/api/admin/locations", methods=["POST"])
@login_required(role="admin")
def api_admin_add_location():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    lat = None
    lng = None
    if "latitude" in data and "longitude" in data:
        try:
            lat = float(data["latitude"])
            lng = float(data["longitude"])
        except (TypeError, ValueError):
            return jsonify({"error": "latitude and longitude must be numbers"}), 400
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return jsonify({"error": "Coordinates out of range"}), 400

    if Venue.query.filter_by(name=name).first():
        return jsonify({"error": "A location with that name already exists"}), 409

    building = (str(data.get("building")).strip()[:80] or None) \
        if data.get("building") else None
    floor = data.get("floor")
    if floor in (None, ""):
        floor = None
    else:
        try:
            floor = int(floor)
        except (TypeError, ValueError):
            return jsonify({"error": "floor must be an integer"}), 400

    venue = Venue(name=name, latitude=lat, longitude=lng,
                  building=building, floor=floor)
    db.session.add(venue)
    db.session.flush()
    log_admin_action(user.id, "ADD_VENUE", "venue", venue.id,
                     {"name": name, "latitude": lat, "longitude": lng,
                      "building": building, "floor": floor})
    db.session.commit()
    return jsonify({"message": "Location added", "id": venue.id}), 201


@app.route("/api/admin/locations/<int:location_id>", methods=["PUT"])
@login_required(role="admin")
def api_admin_edit_location(location_id):
    user = get_current_user()
    venue = db.session.get(Venue, location_id)
    if not venue:
        return jsonify({"error": "Location not found"}), 404
    data = request.get_json(silent=True) or {}
    before = {"name": venue.name, "latitude": venue.latitude,
              "longitude": venue.longitude,
              "building": venue.building, "floor": venue.floor}
    if "name" in data:
        new_name = (data["name"] or "").strip()
        if not new_name:
            return jsonify({"error": "name cannot be empty"}), 400
        existing = Venue.query.filter_by(name=new_name).first()
        if existing and existing.id != venue.id:
            return jsonify({"error": "A location with that name already exists"}), 409
        venue.name = new_name
    if "latitude" in data:
        try:
            venue.latitude = float(data["latitude"])
        except (TypeError, ValueError):
            return jsonify({"error": "latitude must be a number"}), 400
    if "longitude" in data:
        try:
            venue.longitude = float(data["longitude"])
        except (TypeError, ValueError):
            return jsonify({"error": "longitude must be a number"}), 400
    if "building" in data:
        b = data.get("building")
        venue.building = (str(b).strip()[:80] or None) if b is not None else None
    if "floor" in data:
        f = data.get("floor")
        if f in (None, ""):
            venue.floor = None
        else:
            try:
                venue.floor = int(f)
            except (TypeError, ValueError):
                return jsonify({"error": "floor must be an integer"}), 400
    after = {"name": venue.name, "latitude": venue.latitude,
             "longitude": venue.longitude,
             "building": venue.building, "floor": venue.floor}
    log_admin_action(user.id, "EDIT_VENUE", "venue", venue.id,
                     {"before": before, "after": after})
    db.session.commit()
    return jsonify({"message": "Location updated"})


@app.route("/api/admin/locations/<int:location_id>", methods=["DELETE"])
@login_required(role="admin")
def api_admin_delete_location(location_id):
    user = get_current_user()
    venue = db.session.get(Venue, location_id)
    if not venue:
        return jsonify({"error": "Location not found"}), 404
    schedule_count = CourseSchedule.query.filter_by(venue_id=venue.id).count()
    if schedule_count > 0:
        return jsonify({
            "error": f"Cannot delete — this location is used in {schedule_count} schedule(s). "
                     "Reassign or remove those schedules first."
        }), 409
    log_admin_action(user.id, "DELETE_VENUE", "venue", venue.id,
                     {"name": venue.name, "latitude": venue.latitude, "longitude": venue.longitude})
    db.session.delete(venue)
    db.session.commit()
    return jsonify({"message": "Location deleted"})


@app.route("/api/admin/locations/<int:location_id>/set-gps", methods=["POST"])
@login_required(role="admin")
def api_admin_set_venue_gps(location_id):
    """
    Receive GPS point(s) from the admin's device, compute the geofence
    center and radius, and persist them on the venue row.

    Body:  { points: [{lat, lng}, ...], building?: str, floor?: int }

    Also returns any venue collisions this creates — overlapping fences and
    stacked (same-building, different-floor) peers — so the dashboard can warn
    before the admin walks away.
    """
    user = get_current_user()
    venue = db.session.get(Venue, location_id)
    if not venue:
        return jsonify({"error": "Location not found"}), 404

    data = request.get_json(silent=True) or {}
    points_raw = data.get("points")

    if not isinstance(points_raw, list) or len(points_raw) < 1:
        return jsonify({"error": "points must be a non-empty array of {lat, lng}"}), 400

    points = []
    for i, p in enumerate(points_raw):
        try:
            lat = float(p["lat"])
            lng = float(p["lng"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": f"Point {i} is missing valid lat/lng values"}), 400
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return jsonify({"error": f"Point {i} has out-of-range coordinates"}), 400
        points.append((lat, lng))

    n = len(points)
    center_lat = sum(p[0] for p in points) / n
    center_lng = sum(p[1] for p in points) / n

    if n == 1:
        radius = float(DEFAULT_RADIUS_M)
    else:
        max_dist = max(
            haversine_distance(lat, lng, center_lat, center_lng)
            for lat, lng in points
        )
        radius = max_dist + LOCATION_BUFFER_M
        radius = max(radius, float(DEFAULT_RADIUS_M))

    venue.latitude  = center_lat
    venue.longitude = center_lng
    venue.radius    = round(radius, 2)

    # A fresh survey supersedes any learned fence. The learned centre and radius
    # were defined *relative to the survey* — the displacement cap measures
    # against it — so keeping a calibration past a re-walk would leave a fence
    # whose own provenance no longer holds, and would make the next recompute
    # measure displacement from the wrong baseline. Clearing here means the
    # venue re-earns calibration from check-ins, which is the honest state: the
    # admin has just said the old geometry was wrong.
    had_calibration = is_calibrated(venue) or bool(venue.cal_review)
    if had_calibration:
        venue.cal_lat = venue.cal_lng = venue.cal_radius = None
        venue.cal_n = None
        venue.cal_updated_at = None
        venue.cal_review = None

    # Optional building/floor, so a stacked pair can be declared at the same
    # time the perimeter is walked. Absent keys leave the existing value alone;
    # an explicit null clears it.
    if "building" in data:
        b = data.get("building")
        venue.building = (str(b).strip()[:80] or None) if b is not None else None
    if "floor" in data:
        f = data.get("floor")
        if f is None or f == "":
            venue.floor = None
        else:
            try:
                venue.floor = int(f)
            except (TypeError, ValueError):
                return jsonify({"error": "floor must be an integer"}), 400

    db.session.commit()

    # ── Collision check — a design-time diagnostic ────────────────────
    # Overlap is cheapest to fix here, while the admin is still standing in the
    # room and can shrink the radius. Detecting it at attendance time only lets
    # us say "we cannot tell"; detecting it now lets someone make it untrue.
    peers = Venue.query.filter(
        Venue.id != venue.id,
        Venue.latitude.isnot(None),
        Venue.longitude.isnot(None),
    ).all()
    conflicts = venue_conflicts(venue, peers, app.config["GEOFENCE_RADIUS_METRES"])

    warnings = []
    for o in conflicts["overlaps"]:
        warnings.append(
            f"{o['name']} overlaps this venue by {o['overlap_m']}m "
            f"(centres {o['distance_m']}m apart). Reduce one of the radii."
        )
    for s in conflicts["stacked"]:
        warnings.append(
            f"{s['name']} is stacked with this venue "
            f"({s['building']}, floor {s['floor']}). GPS cannot separate floors, "
            f"so attendance at either will require a QR scan."
        )

    log_admin_action(user.id, "SET_VENUE_GPS", "venue", venue.id, {
        "center_lat": center_lat, "center_lng": center_lng,
        "radius_m": venue.radius, "points_used": n,
        "building": venue.building, "floor": venue.floor,
        "overlaps": conflicts["overlaps"], "stacked": conflicts["stacked"],
        # Recording that a re-survey dropped a learned fence, so the loss is
        # visible in the audit trail rather than appearing as a venue that
        # mysteriously reverted to requiring QR.
        "cleared_calibration": had_calibration,
    })

    return jsonify({
        "message":    "GPS coordinates saved successfully",
        "venue_id":   venue.id,
        "center_lat": center_lat,
        "center_lng": center_lng,
        "radius_m":   venue.radius,
        "points_used": n,
        "building":   venue.building,
        "floor":      venue.floor,
        "overlaps":   conflicts["overlaps"],
        "stacked":    conflicts["stacked"],
        "warnings":   warnings,
    }), 200


@app.route("/api/admin/locations/<int:location_id>/calibration", methods=["POST"])
@login_required(role="admin")
def api_admin_venue_calibration(location_id):
    """
    The human half of Stage 5's feedback loop.

    Body: { action: "apply" | "clear" }

      apply   Promote a flagged candidate (one that tripped the displacement or
              radius brake) to the live learned fence. This is the deliberate
              act the brake exists to force: the loop refused to move the fence
              on its own, and an admin who can see the room decides the survey
              was wrong rather than the data gamed.
      clear   Wipe the learned fence and any pending candidate, returning the
              venue to its survey. The escape hatch the plan's risk note asks
              for: a venue calibrated from a bad run starts over.

    Both are audited. A recompute on the next session close may of course
    re-flag or re-promote from the same data -- clearing does not pin a venue
    uncalibrated, it resets the evidence to what is on record now.
    """
    user = get_current_user()
    venue = db.session.get(Venue, location_id)
    if not venue:
        return jsonify({"error": "Location not found"}), 404

    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip().lower()

    if action == "apply":
        if not venue.cal_review:
            return jsonify({"error": "No calibration candidate is awaiting review "
                                     "for this venue."}), 400
        try:
            cand = json.loads(venue.cal_review)
        except (ValueError, TypeError):
            return jsonify({"error": "The stored calibration candidate is unreadable; "
                                     "clear it and let it recompute."}), 400
        venue.cal_lat        = float(cand["lat"])
        venue.cal_lng        = float(cand["lng"])
        venue.cal_radius     = float(cand["radius"])
        venue.cal_n          = int(cand["n"])
        venue.cal_updated_at = datetime.utcnow()
        venue.cal_review     = None
        log_admin_action(user.id, "CALIBRATION_APPLIED", "venue", venue.id, {
            "cal_lat": venue.cal_lat, "cal_lng": venue.cal_lng,
            "cal_radius": round(venue.cal_radius, 2), "n": venue.cal_n,
            "displacement_m": cand.get("displacement_m"),
            "overrode_reason": cand.get("reason"),
        })
        db.session.commit()
        return jsonify({"message": "Calibration applied.",
                        "calibrated": True,
                        "cal_radius": round(venue.cal_radius, 2),
                        "cal_n": venue.cal_n}), 200

    if action == "clear":
        had = is_calibrated(venue) or bool(venue.cal_review)
        venue.cal_lat = venue.cal_lng = venue.cal_radius = None
        venue.cal_n = None
        venue.cal_updated_at = None
        venue.cal_review = None
        log_admin_action(user.id, "CALIBRATION_CLEARED", "venue", venue.id,
                         {"had_calibration": had})
        db.session.commit()
        return jsonify({"message": "Calibration cleared; venue is back on its survey.",
                        "calibrated": False}), 200

    return jsonify({"error": 'action must be "apply" or "clear"'}), 400


# ─────────────────────────────────────────────────────────────
# API — ADMIN: COURSES
# ─────────────────────────────────────────────────────────────

@app.route("/api/admin/courses", methods=["GET"])
@login_required(role="admin")
def api_admin_list_courses():
    courses = Course.query.order_by(Course.course_code.asc()).all()
    result = []
    for c in courses:
        result.append({
            "id": c.id,
            "course_code": c.course_code,
            "course_name": c.course_name,
            "lecturer_id": c.lecturer_id,
            "lecturer_name": c.lecturer.name if c.lecturer else None,
            "enrolled": c.enrollments.count(),
            "location_lat": c.location_lat,
            "location_lng": c.location_lng,
            "radius": c.radius,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return jsonify({"courses": result})


@app.route("/api/admin/courses", methods=["POST"])
@login_required(role="admin")
def api_admin_add_course():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    code = (data.get("course_code") or "").strip().upper()
    name = (data.get("course_name") or "").strip()
    lecturer_id_raw = data.get("lecturer_id")

    if not code:
        return jsonify({"error": "course_code is required"}), 400
    if not name:
        return jsonify({"error": "course_name is required"}), 400
    try:
        lecturer_id = int(lecturer_id_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "lecturer_id must be an integer"}), 400

    lecturer = User.query.filter_by(id=lecturer_id, role="lecturer").first()
    if not lecturer:
        return jsonify({"error": "Lecturer not found"}), 404
    if Course.query.filter_by(course_code=code).first():
        return jsonify({"error": "Course code already exists"}), 409

    course = Course(
        course_code=code,
        course_name=name,
        lecturer_id=lecturer_id,
        location_lat=0.0,
        location_lng=0.0,
        radius=50.0,
    )
    db.session.add(course)
    db.session.flush()
    log_admin_action(user.id, "ADD_COURSE", "course", course.id,
                     {"course_code": code, "course_name": name, "lecturer_id": lecturer_id})
    db.session.commit()
    return jsonify({"message": "Course created", "id": course.id}), 201


@app.route("/api/admin/courses/<int:course_id>", methods=["PUT"])
@login_required(role="admin")
def api_admin_edit_course(course_id):
    user = get_current_user()
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({"error": "Course not found"}), 404
    data = request.get_json(silent=True) or {}
    before = {
        "course_code": course.course_code,
        "course_name": course.course_name,
        "lecturer_id": course.lecturer_id,
    }
    if "course_name" in data:
        new_name = (data["course_name"] or "").strip()
        if not new_name:
            return jsonify({"error": "course_name cannot be empty"}), 400
        course.course_name = new_name
    if "course_code" in data:
        new_code = (data["course_code"] or "").strip().upper()
        if not new_code:
            return jsonify({"error": "course_code cannot be empty"}), 400
        existing = Course.query.filter_by(course_code=new_code).first()
        if existing and existing.id != course.id:
            return jsonify({"error": "Course code already in use"}), 409
        course.course_code = new_code
    if "lecturer_id" in data:
        try:
            new_lid = int(data["lecturer_id"])
        except (TypeError, ValueError):
            return jsonify({"error": "lecturer_id must be an integer"}), 400
        lecturer = User.query.filter_by(id=new_lid, role="lecturer").first()
        if not lecturer:
            return jsonify({"error": "Lecturer not found"}), 404
        course.lecturer_id = new_lid
    after = {
        "course_code": course.course_code,
        "course_name": course.course_name,
        "lecturer_id": course.lecturer_id,
    }
    log_admin_action(user.id, "EDIT_COURSE", "course", course.id,
                     {"before": before, "after": after})
    db.session.commit()
    return jsonify({"message": "Course updated"})


@app.route("/api/admin/courses/<int:course_id>", methods=["DELETE"])
@login_required(role="admin")
def api_admin_delete_course(course_id):
    user = get_current_user()
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({"error": "Course not found"}), 404
    log_admin_action(user.id, "DELETE_COURSE", "course", course.id,
                     {"course_code": course.course_code, "course_name": course.course_name})
    db.session.delete(course)
    db.session.commit()
    return jsonify({"message": "Course deleted"})


# ─────────────────────────────────────────────────────────────
# API — ADMIN: ENROLLMENTS
# ─────────────────────────────────────────────────────────────

@app.route("/api/admin/enrollments", methods=["GET"])
@login_required(role="admin")
def api_admin_list_enrollments():
    q = Enrollment.query
    course_id = request.args.get("course_id", type=int)
    student_id = request.args.get("student_id", type=int)
    if course_id:
        q = q.filter_by(course_id=course_id)
    if student_id:
        q = q.filter_by(student_id=student_id)
    enrollments = q.order_by(Enrollment.enrolled_at.desc()).all()
    result = []
    for e in enrollments:
        result.append({
            "id": e.id,
            "student_id": e.student_id,
            "student_name": e.student.name,
            "student_email": e.student.email,
            "course_id": e.course_id,
            "course_code": e.course.course_code,
            "course_name": e.course.course_name,
            "enrolled_at": e.enrolled_at.isoformat() if e.enrolled_at else None,
        })
    return jsonify({"enrollments": result})


@app.route("/api/admin/enrollments", methods=["POST"])
@login_required(role="admin")
def api_admin_enroll_student():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    try:
        student_id = int(data["student_id"])
        course_id  = int(data["course_id"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "student_id and course_id are required integers"}), 400

    student = User.query.filter_by(id=student_id, role="student").first()
    if not student:
        return jsonify({"error": "Student not found"}), 404
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({"error": "Course not found"}), 404
    if Enrollment.query.filter_by(student_id=student_id, course_id=course_id).first():
        return jsonify({"error": "Student is already enrolled in this course"}), 409

    enrollment = Enrollment(student_id=student_id, course_id=course_id)
    db.session.add(enrollment)
    db.session.flush()
    log_admin_action(user.id, "ENROLL_STUDENT", "enrollment", enrollment.id,
                     {"student_id": student_id, "student_name": student.name,
                      "course_id": course_id, "course_code": course.course_code})
    db.session.commit()
    return jsonify({"message": "Student enrolled", "id": enrollment.id}), 201


@app.route("/api/admin/enrollments/<int:enrollment_id>", methods=["DELETE"])
@login_required(role="admin")
def api_admin_unenroll_student(enrollment_id):
    user = get_current_user()
    enrollment = db.session.get(Enrollment, enrollment_id)
    if not enrollment:
        return jsonify({"error": "Enrollment not found"}), 404
    log_admin_action(user.id, "UNENROLL_STUDENT", "enrollment", enrollment_id,
                     {"student_id": enrollment.student_id,
                      "student_name": enrollment.student.name,
                      "course_id": enrollment.course_id,
                      "course_code": enrollment.course.course_code})
    db.session.delete(enrollment)
    db.session.commit()
    return jsonify({"message": "Student unenrolled"})


# ─────────────────────────────────────────────────────────────
# API — ADMIN: USERS & ROLE MANAGEMENT (superuser only for promote/demote)
# ─────────────────────────────────────────────────────────────

@app.route("/api/admin/users", methods=["GET"])
@login_required(role="admin")
def api_admin_list_users():
    role_filter = request.args.get("role")
    q = User.query
    if role_filter:
        q = q.filter_by(role=role_filter)
    users = q.order_by(User.role.asc(), User.name.asc()).all()
    result = []
    for u in users:
        result.append({
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role,
            "is_superuser": u.is_superuser,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })
    return jsonify({"users": result})


@app.route("/api/admin/users/<int:target_id>/promote", methods=["POST"])
@superuser_required
def api_admin_promote_user(target_id):
    actor = get_current_user()
    target = db.session.get(User, target_id)
    if not target:
        return jsonify({"error": "User not found"}), 404
    if target.id == actor.id:
        return jsonify({"error": "Cannot change your own role"}), 400
    if target.role == "admin":
        return jsonify({"error": "User is already an admin"}), 409
    before_role = target.role
    target.role = "admin"
    target.is_superuser = False
    log_admin_action(actor.id, "PROMOTE_USER", "user", target.id,
                     {"user_email": target.email, "from_role": before_role, "to_role": "admin"})
    db.session.commit()
    return jsonify({"message": f"{target.name} promoted to admin"})


@app.route("/api/admin/users/<int:target_id>/demote", methods=["POST"])
@superuser_required
def api_admin_demote_user(target_id):
    actor = get_current_user()
    data = request.get_json(silent=True) or {}
    target = db.session.get(User, target_id)
    if not target:
        return jsonify({"error": "User not found"}), 404
    if target.id == actor.id:
        return jsonify({"error": "Cannot change your own role"}), 400
    if target.is_superuser:
        return jsonify({"error": "Cannot demote another superuser"}), 403
    if target.role != "admin":
        return jsonify({"error": "User is not an admin"}), 400
    new_role = (data.get("role") or "").strip().lower()
    if new_role not in ("student", "lecturer"):
        return jsonify({"error": "role must be 'student' or 'lecturer'"}), 400
    log_admin_action(actor.id, "DEMOTE_USER", "user", target.id,
                     {"user_email": target.email, "from_role": "admin", "to_role": new_role})
    target.role = new_role
    target.is_superuser = False
    db.session.commit()
    return jsonify({"message": f"{target.name} demoted to {new_role}"})


# ─────────────────────────────────────────────────────────────
# API — ADMIN: LOGS
# ─────────────────────────────────────────────────────────────

@app.route("/api/admin/logs", methods=["GET"])
@login_required(role="admin")
def api_admin_logs():
    page       = request.args.get("page", 1, type=int)
    per_page   = min(request.args.get("per_page", 50, type=int), 200)
    action_f   = request.args.get("action")
    admin_f    = request.args.get("admin_id", type=int)

    q = AdminLog.query
    if action_f:
        q = q.filter(AdminLog.action == action_f.upper())
    if admin_f:
        q = q.filter(AdminLog.admin_id == admin_f)
    q = q.order_by(AdminLog.created_at.desc())

    total = q.count()
    logs  = q.offset((page - 1) * per_page).limit(per_page).all()

    result = []
    for lg in logs:
        result.append({
            "id":          lg.id,
            "admin_id":    lg.admin_id,
            "admin_name":  lg.admin.name if lg.admin else None,
            "action":      lg.action,
            "target_type": lg.target_type,
            "target_id":   lg.target_id,
            "details":     lg.details,
            "created_at":  lg.created_at.isoformat() if lg.created_at else None,
        })
    return jsonify({
        "logs":      result,
        "total":     total,
        "page":      page,
        "per_page":  per_page,
        "pages":     (total + per_page - 1) // per_page,
    })


# ─────────────────────────────────────────────────────────────
# DB INITIALISATION
# ─────────────────────────────────────────────────────────────

@app.cli.command("init-db")
def init_db():
    """Flask CLI command: flask init-db"""
    with app.app_context():
        db.create_all()
        ensure_schema_compatibility()
        seed_demo_if_empty()
        print("✓ Database ready.")


@app.cli.command("fix-demo-passwords")
def fix_demo_passwords():
    """Set password123 on bundled demo emails (if those rows exist)."""
    emails = (
        "admin@university.edu",
        "amara.osei@university.edu",
        "funmi.adeyemi@university.edu",
        "chidi.nwosu@student.edu",
        "amina.bello@student.edu",
        "tunde.fashola@student.edu",
    )
    with app.app_context():
        n = 0
        for email in emails:
            u = User.query.filter_by(email=email).first()
            if u:
                u.set_password("password123")
                n += 1
        db.session.commit()
    print(f"✓ Updated {n} demo account(s) to password123.")


# ─────────────────────────────────────────────────────────────
# IMPORT-TIME BOOTSTRAP  (see BOOTSTRAP_ERRORS below)
# ─────────────────────────────────────────────────────────────
#
# Create tables + demo users whenever the app is loaded (covers `flask run`).
#
# Each step is isolated, and none of them may kill module import. This used to
# be three bare calls, and on 2026-10-14 that turned a dependency problem into
# a total blackout: the import raised, so Flask never finished registering a
# single route and every path returned 500 -- including /login, which needs no
# database at all to render.
#
# The judgement here is about blast radius, not about hiding errors. A database
# that cannot be reached is a real failure and most pages genuinely cannot work
# without one. But "most pages" is not "the login page", and an operator who
# can still load a page and read an error is in a completely different position
# from one facing an opaque FUNCTION_INVOCATION_FAILED on every URL.
#
# So: the failure is recorded, surfaced at /healthz, and logged -- and routes
# that need the database fail individually, where the error can be seen.
#
# A LIST, not a single value: when the database is unreachable all three steps
# fail, and the first one is the informative one. Keeping only the last reports
# "seed_demo_if_empty failed" when the actual news is "create_all could not
# open the database".
#
# IMPORTANT LIMIT. This protects against the database being unreachable, the
# schema upgrade failing, and seeding failing. It does NOT protect against a
# missing driver: `db = SQLAlchemy(app)` above resolves the dialect and imports
# the DBAPI module, so a missing psycopg still kills the import before this
# block is reached -- which is exactly what happened on 2026-10-14. That case
# is addressed by pinning SQLAlchemy and naming the driver in the URL
# (config._database_url), not here. Wrapping the SQLAlchemy() call itself is
# not an option: every `class X(db.Model)` below would then have no base.
BOOTSTRAP_ERRORS: list[str] = []

with app.app_context():
    for _step, _fn in (
        ("create_all", db.create_all),
        ("ensure_schema_compatibility", ensure_schema_compatibility),
        ("seed_demo_if_empty", seed_demo_if_empty),
    ):
        try:
            _fn()
        except Exception as _exc:                 # noqa: BLE001 - see above
            _msg = f"{_step}: {type(_exc).__name__}: {_exc}"
            BOOTSTRAP_ERRORS.append(_msg)
            app.logger.error("bootstrap step failed -- %s", _msg)
            try:
                db.session.rollback()
            except Exception:
                pass
            # Keep going. A failed ensure_schema_compatibility() must not stop
            # seeding, and a failed seed must not stop the app from serving.
            continue


@app.route("/healthz")
def healthz():
    """
    Is this instance actually able to serve?

    Deliberately public and deliberately database-touching: the whole point is
    to be reachable when the database is not, and to say so plainly instead of
    leaving an operator to infer it from a 500 on every page.

    Returns 200 when the bootstrap completed and a trivial query succeeds, 503
    with the reasons otherwise. No schema details and no connection string --
    just which step failed and the exception type.
    """
    problems = list(BOOTSTRAP_ERRORS)
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:                      # noqa: BLE001
        db.session.rollback()
        problems.append(f"query: {type(exc).__name__}: {exc}")

    if problems:
        return jsonify({"ok": False, "problems": problems}), 503
    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    # Local only. Port 5000 is also used by run_student.py — do not run both.
    app.run(debug=True, host="0.0.0.0", port=5000)