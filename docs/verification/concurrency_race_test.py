"""
Concurrency test — the check-then-insert race on the attendance endpoints.

Both `/api/student/attendance/mark` and the lecturer's `manual-mark` check for
an existing record and then insert. The two are not atomic, and on the student
path the gap is an 8-second GPS capture wide. Two concurrent submissions from
one student both pass the check; the loser then violates
`UniqueConstraint("session_id", "student_id")` at commit.

Unhandled, that is a 500 on the busiest endpoint in the system — returned to a
student whose attendance the winning request just recorded successfully. The
endpoint already has the correct answer twelve steps earlier (409), so the fix
is to give it.

This mattered less locally than it does in production: the dev server handles
one request at a time, so the window barely exists, while Vercel runs
concurrent invocations in separate processes. Finding F3 removed the
client-side cause (a 30s poll handing the student an enabled duplicate button
mid-capture) but not the server-side possibility.

The race is forced deterministically rather than raced for: the conflicting row
is inserted on a second connection *behind the endpoint's back*, after its
duplicate check has already passed. That reproduces the loser's exact situation
every run, with no timing luck.

Usage:  python docs/verification/concurrency_race_test.py <repo_root>
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
sys.path.insert(0, os.path.join(REPO, "attendx_system"))

DB = os.path.join(tempfile.gettempdir(), "attendx_race_test.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB.replace("\\", "/")
os.environ["SECRET_KEY"] = "test-only-not-the-placeholder-0123456789abcdef"

import app as A  # noqa: E402
from app import (  # noqa: E402
    db, Venue, Course, User, AttendanceSession, AttendanceRecord, Enrollment,
    generate_qr_token,
)

BAD = []
CHECKS = 0


def chk(label, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if not cond:
        BAD.append(f"{label}: {detail}" if detail else label)


LAT, LNG = 6.5244, 3.3792

with A.app.app_context():
    lec = User(name="Lec", email="lec@r.test", role="lecturer")
    lec.set_password("x")
    stu = User(name="Stu", email="stu@r.test", role="student")
    stu.set_password("x")
    db.session.add_all([lec, stu])
    db.session.commit()

    course = Course(course_code="R101", course_name="Race", lecturer_id=lec.id)
    db.session.add(course)
    db.session.commit()
    db.session.add(Enrollment(student_id=stu.id, course_id=course.id))

    venue = Venue(name="RaceHall", latitude=LAT, longitude=LNG, radius=50.0)
    db.session.add(venue)
    db.session.commit()

    sess = AttendanceSession(
        course_id=course.id, venue_id=venue.id,
        start_time=datetime.utcnow() - timedelta(minutes=1),
        end_time=datetime.utcnow() + timedelta(minutes=30),
        status=A.get_open_write_status(),
    )
    db.session.add(sess)
    db.session.commit()
    sess_id, stu_id, lec_id, venue_id = sess.id, stu.id, lec.id, venue.id
    token = generate_qr_token(sess_id)

client = A.app.test_client()


def insert_conflicting_row():
    """
    Insert the row the winning request would have written, directly — standing
    in for a concurrent invocation that committed while this one was capturing
    GPS. Done on its own session so the endpoint's session knows nothing of it,
    which is exactly the production situation.
    """
    with A.app.app_context():
        db.session.add(AttendanceRecord(
            session_id=sess_id, student_id=stu_id,
            lat=LAT, lng=LNG, accuracy=10.0, distance_m=1.0, status="PRESENT",
            sample_count=5, spread_m=1.0, acc_eff=10.0,
            verify_state="AMBIGUOUS", geo_state="AMBIGUOUS", confidence=0.5,
            qr_verified=True, qr_trusted=True,
        ))
        db.session.commit()


def samples(n=6):
    return [{"lat": LAT, "lng": LNG, "acc": 8.0} for _ in range(n)]


# ── 1. the ordinary duplicate path still answers 409 ─────────────
# Not the race: the row exists before the request starts, so step 3 catches it.
insert_conflicting_row()
with client.session_transaction() as s:
    s["user_id"] = stu_id
r = client.post("/api/student/attendance/mark",
                json={"session_id": sess_id, "lat": LAT, "lng": LNG,
                      "accuracy": 8.0, "samples": samples(),
                      "qr_token": token})
print(f"pre-existing duplicate   -> {r.status_code}")
chk("a pre-existing duplicate is 409", r.status_code == 409,
    f"{r.status_code}: {r.get_data(as_text=True)[:100]}")

# ── 2. the race itself ───────────────────────────────────────────
# Clear the row, then make it appear *after* step 3 has passed, by hooking the
# geofence call that runs between the check and the insert.
with A.app.app_context():
    AttendanceRecord.query.filter_by(session_id=sess_id, student_id=stu_id).delete()
    db.session.commit()
    remaining = AttendanceRecord.query.count()
chk("table cleared for the race", remaining == 0, str(remaining))

original = A.geofence_decision
fired = {"n": 0}


def racing_geofence(*a, **kw):
    # Runs at step 7 — after step 3's duplicate check, before the insert.
    if fired["n"] == 0:
        fired["n"] = 1
        insert_conflicting_row()
    return original(*a, **kw)


A.geofence_decision = racing_geofence
try:
    r = client.post("/api/student/attendance/mark",
                    json={"session_id": sess_id, "lat": LAT, "lng": LNG,
                          "accuracy": 8.0, "samples": samples(),
                          "qr_token": token})
finally:
    A.geofence_decision = original

body = r.get_data(as_text=True)[:140]
print(f"lost the race            -> {r.status_code}")
chk("the race hook actually fired", fired["n"] == 1)
chk("losing the race is NOT a 500", r.status_code != 500, f"got 500: {body}")
chk("losing the race is a clean 409", r.status_code == 409, f"{r.status_code}: {body}")
chk("and it says what happened", "already submitted" in body.lower(), body)

# The winner's record must survive untouched — the loser's rollback must not
# have taken it with it.
with A.app.app_context():
    rows = AttendanceRecord.query.filter_by(
        session_id=sess_id, student_id=stu_id).all()
chk("exactly one record survives", len(rows) == 1, f"{len(rows)} rows")
chk("the winner's record is intact", rows and rows[0].qr_trusted is True)

# ── 3. the session is still usable afterwards ────────────────────
# A rolled-back session that stays poisoned would break the next request on
# this worker, which is the failure mode the rollback exists to prevent.
r2 = client.get("/api/student/courses")
print(f"next request after race  -> {r2.status_code}")
chk("the connection is usable after the rollback", r2.status_code == 200,
    f"{r2.status_code}: {r2.get_data(as_text=True)[:100]}")

# ── 4. the lecturer manual-mark path has the same guard ──────────
with A.app.app_context():
    AttendanceRecord.query.filter_by(session_id=sess_id, student_id=stu_id).delete()
    db.session.commit()

with client.session_transaction() as s:
    s["user_id"] = lec_id

original_log = A.log_admin_action
hit = {"n": 0}


def racing_log(*a, **kw):
    # log_admin_action runs after manual-mark's duplicate check, before commit.
    if hit["n"] == 0:
        hit["n"] = 1
        insert_conflicting_row()
    return original_log(*a, **kw)


A.log_admin_action = racing_log
try:
    r3 = client.post(f"/api/lecturer/session/{sess_id}/manual-mark",
                     json={"student_id": stu_id, "reason": "race test"})
finally:
    A.log_admin_action = original_log

b3 = r3.get_data(as_text=True)[:140]
print(f"manual-mark lost race    -> {r3.status_code}")
chk("manual-mark race hook fired", hit["n"] == 1)
chk("manual-mark does not 500", r3.status_code != 500, f"got 500: {b3}")
chk("manual-mark returns 409", r3.status_code == 409, f"{r3.status_code}: {b3}")

print(f"checks run: {CHECKS}")
print("RACE BAD: " + (repr(BAD) if BAD else "[] -- all pass"))

try:
    os.remove(DB)
except OSError:
    pass
sys.exit(1 if BAD else 0)
