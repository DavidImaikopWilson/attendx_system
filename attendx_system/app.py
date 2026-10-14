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
        pass

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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


def ensure_schema_compatibility() -> None:
    """
    Keep older databases compatible with newer models without requiring
    a dedicated migration tool for this project.
    """
    inspector = inspect(db.engine)

    try:
        schedule_columns = {
            c["name"] for c in inspector.get_columns("course_schedules")
        }
    except Exception:
        return

    if "postpone_count" not in schedule_columns:
        db.session.execute(text(
            "ALTER TABLE course_schedules "
            "ADD COLUMN postpone_count INTEGER NOT NULL DEFAULT 0"
        ))
        db.session.commit()

    # ── Add radius column to courses if it doesn't exist yet ──
    try:
        course_columns = {c["name"] for c in inspector.get_columns("courses")}
        if "radius" not in course_columns:
            db.session.execute(text(
                "ALTER TABLE courses "
                "ADD COLUMN radius FLOAT NOT NULL DEFAULT 50.0"
            ))
            db.session.commit()
    except Exception:
        pass

    # ── Add is_superuser column to users if missing ──
    try:
        user_columns = {c["name"] for c in inspector.get_columns("users")}
        if "is_superuser" not in user_columns:
            db.session.execute(text(
                "ALTER TABLE users ADD COLUMN is_superuser BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            db.session.commit()
    except Exception:
        pass

    # ── Create admin_logs table if missing ──
    try:
        AdminLog.__table__.create(bind=db.engine, checkfirst=True)
    except Exception:
        pass

    # ── Add radius column to venues if it doesn't exist yet ──
    try:
        venue_columns = {c["name"] for c in inspector.get_columns("venues")}
        if "radius" not in venue_columns:
            db.session.execute(text(
                "ALTER TABLE venues ADD COLUMN radius FLOAT"
            ))
            db.session.commit()
    except Exception:
        pass

    # ── Add venue_id column to attendance_sessions if missing ──
    try:
        session_columns = {c["name"] for c in inspector.get_columns("attendance_sessions")}
        if "venue_id" not in session_columns:
            db.session.execute(text(
                "ALTER TABLE attendance_sessions "
                "ADD COLUMN venue_id INTEGER REFERENCES venues(id)"
            ))
            db.session.commit()
    except Exception:
        pass

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
    """Validate a QR token -- accepts current window and previous window."""
    if not token:
        return False
    now = datetime.utcnow().timestamp()
    for offset in (0, -1, -2, -3):
        expected = generate_qr_token(session_id, now + offset * QR_TOKEN_INTERVAL)
        if hmac.compare_digest(token, expected):
            return True
    return False


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
            records.append({
                "student_name": r.student.name,
                "student_email": r.student.email,
                "timestamp": r.timestamp.strftime("%H:%M:%S"),
                "distance_m": round(r.distance_m, 1),
                "accuracy_m": round(r.accuracy, 1),
                "status": api_record_status(r.status),
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
        records.append({
            "student_name": r.student.name,
            "student_email": r.student.email,
            "timestamp":    r.timestamp.strftime("%H:%M:%S"),
            "distance_m":   round(r.distance_m, 1),
            "accuracy_m":   round(r.accuracy, 1),
            "status":       api_record_status(r.status),
        })

    return jsonify({
        "session_id":        sess.id,
        "course_code":       sess.course.course_code,
        "course_name":       sess.course.course_name,
        "status":            api_session_status(sess.status),
        "seconds_remaining": sess.seconds_remaining(),
        "enrolled":          enrolled_count,
        "present":           present_count,
        "attendance_rate":   round((present_count / enrolled_count * 100), 1) if enrolled_count else 0,
        "records":           records
    })


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

    Validation pipeline:
        1. Session exists and is OPEN
        2. Current time within allowed window
        3. Student has not already submitted
        4. Student is enrolled in the course
        5. GPS accuracy is acceptable
        6. Haversine distance ≤ geofence radius
    """
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    session_id = data.get("session_id")
    lat        = data.get("lat")
    lng        = data.get("lng")
    accuracy   = data.get("accuracy")

    # ── Step 0: presence check ──────────────────────────────────────
    if any(v is None for v in [session_id, lat, lng, accuracy]):
        return jsonify({"error": "session_id, lat, lng, and accuracy are required"}), 400

    try:
        session_id = int(session_id)
        lat = float(lat)
        lng = float(lng)
        accuracy = float(accuracy)
    except (TypeError, ValueError):
        return jsonify({"error": "session_id must be an integer and lat/lng/accuracy must be numeric"}), 400

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

    # ── Step 2b: QR token validation (when provided by scanner) ──────
    qr_token = data.get("qr_token")
    if qr_token:
        if not validate_qr_token(session_id, qr_token):
            return jsonify({
                "error": "Invalid or expired QR code. "
                         "Ask the lecturer to show the latest code."
            }), 403

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

    # ── Step 5: GPS accuracy check ────────────────────────────────────
    max_acc = app.config["MAX_GPS_ACCURACY_METRES"]
    if accuracy > max_acc:
        return jsonify({
            "error": f"GPS signal too weak (accuracy: {round(accuracy)}m). "
                     f"Please move to an open area and try again."
        }), 400

    # ── Step 6: Geofence check (Haversine) ───────────────────────────
    venue = sess.venue
    if not venue or not venue.latitude or not venue.longitude:
        return jsonify({"error": "Venue GPS not configured. Contact admin."}), 400

    distance = haversine_distance(lat, lng, venue.latitude, venue.longitude)
    base_radius = (
        venue.radius
        if (venue.radius and venue.radius > 0)
        else app.config["GEOFENCE_RADIUS_METRES"]
    )
    # Accuracy-aware: widen geofence by student's reported GPS accuracy
    # so WiFi positioning (100-300m accuracy) doesn't auto-fail.
    # On a real phone (accuracy ~10m), this adds only ~10m.
    effective_radius = base_radius + accuracy

    if distance > effective_radius:
        hint = ""
        if distance > 500:
            hint = (
                " Desktop/laptop browsers use IP-based location which can be "
                "off by kilometres. Use a phone with real GPS for accurate results."
            )
        return jsonify({
            "error": f"You are {round(distance)}m from the classroom "
                     f"(GPS accuracy: +/-{round(accuracy)}m). "
                     f"Attendance is only valid within {round(effective_radius)}m."
                     f"{hint}",
            "debug": {
                "your_coords": {"lat": round(lat, 6), "lng": round(lng, 6)},
                "venue_coords": {"lat": round(venue.latitude, 6), "lng": round(venue.longitude, 6)},
                "distance_m": round(distance, 1),
                "effective_radius_m": round(effective_radius, 1),
            }
        }), 400

    # ── All checks passed — record attendance ─────────────────────────
    record = AttendanceRecord(
        session_id = session_id,
        student_id = user.id,
        lat        = lat,
        lng        = lng,
        accuracy   = accuracy,
        distance_m = distance,
        status     = "PRESENT"
    )
    db.session.add(record)
    db.session.commit()

    # ── Step 7: Auto-close session if 100% attendance reached ───────
    total_enrolled = Enrollment.query.filter_by(course_id=sess.course_id).count()
    total_present = AttendanceRecord.query.filter_by(session_id=session_id).count()
    if total_enrolled > 0 and total_present >= total_enrolled:
        sess.status = CLOSED_DB_STATUS
        db.session.commit()

    return jsonify({
        "message":     "Attendance marked successfully",
        "status":      "present",
        "distance_m":  round(distance, 1),
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
        result.append({
            "id": v.id,
            "name": v.name,
            "latitude": v.latitude,
            "longitude": v.longitude,
            "radius": v.radius,
            "schedule_count": schedule_count,
            "created_at": v.created_at.isoformat() if v.created_at else None,
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

    venue = Venue(name=name, latitude=lat, longitude=lng)
    db.session.add(venue)
    db.session.flush()
    log_admin_action(user.id, "ADD_VENUE", "venue", venue.id,
                     {"name": name, "latitude": lat, "longitude": lng})
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
    before = {"name": venue.name, "latitude": venue.latitude, "longitude": venue.longitude}
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
    after = {"name": venue.name, "latitude": venue.latitude, "longitude": venue.longitude}
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

    Body:  { points: [{lat: float, lng: float}, ...] }
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
    db.session.commit()

    log_admin_action(user.id, "SET_VENUE_GPS", "venue", venue.id, {
        "center_lat": center_lat, "center_lng": center_lng,
        "radius_m": venue.radius, "points_used": n,
    })

    return jsonify({
        "message":    "GPS coordinates saved successfully",
        "venue_id":   venue.id,
        "center_lat": center_lat,
        "center_lng": center_lng,
        "radius_m":   venue.radius,
        "points_used": n,
    }), 200


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


# Create tables + demo users whenever the app is loaded (covers `flask run`).
with app.app_context():
    db.create_all()
    ensure_schema_compatibility()
    seed_demo_if_empty()


if __name__ == "__main__":
    # Local only. Port 5000 is also used by run_student.py — do not run both.
    app.run(debug=True, host="0.0.0.0", port=5000)