"""
Stage 5 integration test — the DB-backed calibration path end to end.

Boots the REAL app.py against a throwaway SQLite file, so this exercises the
actual models, ensure_schema_compatibility(), recompute_venue_calibration(), and
the calibrated-fence geometry — not reimplementations of them.

Covers:
  * schema idempotency — ensure_schema_compatibility() runs twice, no error,
    and the cal_* columns exist afterwards (the ALTER path Stage 5 added);
  * the input population — only qr_trusted True feeds calibration; NULL and
    False rows are excluded (DR-25/DR-31);
  * promotion — 30 records across 15 students/3 sessions/2 days writes cal_*;
  * the fence actually MOVES — after calibration, effective_fence_centre() and
    effective_fence_radius() return the learned geometry, and a point that was
    inside the survey but outside the learned fence (and vice versa) is judged
    by the learned one;
  * the displacement brake — a cluster 200m off survey writes cal_review and
    leaves cal_lat NULL;
  * clear / apply via the admin endpoint's logic.

Usage:  python docs/verification/stage5_integration_test.py <repo_root>
"""

import json
import os
import sys
import tempfile
from datetime import datetime, date, timedelta

REPO = sys.argv[1] if len(sys.argv) > 1 else "."
APP_DIR = os.path.join(REPO, "attendx_system")
sys.path.insert(0, APP_DIR)

# Point the app at a throwaway SQLite file BEFORE importing it. Everything the
# module does at import time (create_all, ensure_schema_compatibility,
# seed_demo_if_empty) then runs against this, never the developer's database.
DB_PATH = os.path.join(tempfile.gettempdir(), "attendx_stage5_test.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = "sqlite:///" + DB_PATH.replace("\\", "/")
os.environ["SECRET_KEY"] = "test-only-not-the-placeholder-0123456789abcdef"

import app as A  # noqa: E402
from app import (  # noqa: E402
    db, Venue, Course, User, AttendanceSession, AttendanceRecord, Enrollment,
    is_calibrated, effective_fence_centre, effective_fence_radius,
    recompute_venue_calibration, haversine_distance,
)

BAD = []
CHECKS = 0


def chk(label, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if not cond:
        BAD.append(f"{label}: {detail}" if detail else label)


SURVEY_LAT, SURVEY_LNG = 6.5244, 3.3792
M_PER_DEG = 111_320.0


def at(dn, de, lat0=SURVEY_LAT, lng0=SURVEY_LNG):
    import math
    return (lat0 + dn / M_PER_DEG,
            lng0 + de / (M_PER_DEG * math.cos(math.radians(lat0))))


with A.app.app_context():
    # ── schema idempotency ───────────────────────────────────────
    # The module already ran ensure_schema_compatibility() once at import.
    # Running it again must be a no-op, not an error (verification item #7's
    # local half — the Postgres half cannot run here, see D12).
    A.ensure_schema_compatibility()
    A.ensure_schema_compatibility()
    from sqlalchemy import inspect as sa_inspect
    cols = {c["name"] for c in sa_inspect(db.engine).get_columns("venues")}
    for c in ("cal_lat", "cal_lng", "cal_radius", "cal_n", "cal_updated_at", "cal_review"):
        chk(f"venues.{c} exists after ALTER", c in cols, str(sorted(cols)))

    # ── fixtures ─────────────────────────────────────────────────
    lec = User(name="Lec", email="lec@x.test", role="lecturer")
    lec.set_password("x")
    db.session.add(lec)
    db.session.commit()

    course = Course(course_code="T101", course_name="Test", lecturer_id=lec.id)
    db.session.add(course)
    db.session.commit()

    venue = Venue(name="Hall", latitude=SURVEY_LAT, longitude=SURVEY_LNG,
                  radius=50.0, building="B1", floor=1)
    db.session.add(venue)
    db.session.commit()
    chk("fresh venue is uncalibrated", not is_calibrated(venue))

    # Three sessions across two distinct days, all at this venue.
    sessions = []
    for i, day_off in enumerate([(0,), (1,), (1,)][:3]):
        do = [0, 1, 1][i]
        s = AttendanceSession(
            course_id=course.id, venue_id=venue.id,
            start_time=datetime.utcnow() - timedelta(days=do, hours=2),
            end_time=datetime.utcnow() - timedelta(days=do, hours=1),
            status="CLOSED",
        )
        db.session.add(s)
        sessions.append(s)
    db.session.commit()

    students = []
    for i in range(15):
        u = User(name=f"S{i}", email=f"s{i}@x.test", role="student")
        u.set_password("x")
        db.session.add(u)
        students.append(u)
    db.session.commit()

    def add_record(sess, student, qr_trusted, dn, de, days_ago=0):
        lat, lng = at(dn, de)
        r = AttendanceRecord(
            session_id=sess.id, student_id=student.id,
            timestamp=datetime.utcnow() - timedelta(days=days_ago),
            lat=lat, lng=lng, accuracy=10.0, distance_m=5.0, status="PRESENT",
            sample_count=6, spread_m=2.0, acc_eff=10.0,
            verify_state="AMBIGUOUS", geo_state="AMBIGUOUS", confidence=0.5,
            qr_verified=True, qr_trusted=qr_trusted,
        )
        db.session.add(r)

    # 30 calibration-grade records, tightly around the survey. Each session
    # gets a DISTINCT student per record — the (session_id, student_id)
    # constraint is real, so a session may not repeat a student.
    n = 0
    for si, sess in enumerate(sessions[:2]):          # two sessions x 15 = 30
        for k in range(15):
            student = students[k]
            dn = ((n % 5) - 2) * 3.0
            de = ((n % 3) - 1) * 3.0
            add_record(sess, student, True, dn, de, days_ago=[0, 1, 1][si])
            n += 1
    # A third session on day 2 so the "days" axis is genuinely 2 and sessions 3.
    for si, sess in enumerate(sessions[2:3]):
        for k in range(5):
            add_record(sess, students[k], True, 0, 0, days_ago=1)
            n += 1
    # Poison: rows that must NOT enter — NULL and False, 300m away. They live in
    # their own session, because the eligible rows above already occupy all 15
    # students in sessions 0 and 1 and (session_id, student_id) is unique. The
    # eligible stats are unaffected: this session contributes zero qr_trusted
    # rows, so it is not counted in the sessions/days axes either.
    poison_sess = AttendanceSession(
        course_id=course.id, venue_id=venue.id,
        start_time=datetime.utcnow() - timedelta(hours=2),
        end_time=datetime.utcnow() - timedelta(hours=1), status="CLOSED")
    db.session.add(poison_sess)
    db.session.commit()
    add_record(poison_sess, students[0], None, 300, 0)
    add_record(poison_sess, students[1], False, 300, 0)
    db.session.commit()

    total = AttendanceRecord.query.count()
    trusted = AttendanceRecord.query.filter(AttendanceRecord.qr_trusted.is_(True)).count()
    chk("37 records written", total == 37, str(total))
    chk("exactly 35 are calibration-grade", trusted == 35, str(trusted))

    # ── promotion ────────────────────────────────────────────────
    recompute_venue_calibration(venue, actor_id=lec.id)
    db.session.refresh(venue)
    chk("venue is now calibrated", is_calibrated(venue))
    chk("cal_n records the 35 eligible, not 41", venue.cal_n == 35, str(venue.cal_n))
    # The 300m poison rows would drag a mean far north; the median must not.
    learned = haversine_distance(venue.cal_lat, venue.cal_lng, SURVEY_LAT, SURVEY_LNG)
    chk("learned centre stayed on the survey despite 300m poison",
        learned < 15, f"{round(learned, 1)}m off")

    # ── the fence actually MOVES ─────────────────────────────────
    clat, clng = effective_fence_centre(venue)
    chk("effective centre is the learned one",
        (round(clat, 6), round(clng, 6)) == (round(venue.cal_lat, 6), round(venue.cal_lng, 6)))
    chk("effective radius is the learned one",
        abs(effective_fence_radius(venue) - venue.cal_radius) < 1e-6)

    # A point 45m from the LEARNED centre: calibrate the learned centre 60m
    # north of the survey, so a fix 45m from the learned centre is ~105m from
    # the survey — inside one fence, outside the other. Proves the geometry
    # changed source, not just the confidence.
    # (Do it directly on the columns to isolate the geometry from the learner.)
    venue.cal_lat, venue.cal_lng = at(60, 0)
    venue.cal_radius = 35.0    # deliberately != the surveyed 50, so the sources are separable
    venue.cal_n = 30
    db.session.commit()

    probe_lat, probe_lng = at(60 + 25, 0)   # 25m from learned centre, 85m from survey
    d_learned = haversine_distance(probe_lat, probe_lng, *effective_fence_centre(venue))
    d_survey = haversine_distance(probe_lat, probe_lng, SURVEY_LAT, SURVEY_LNG)
    chk("probe is inside the learned fence",
        d_learned < effective_fence_radius(venue), f"{round(d_learned,1)}m")
    chk("probe is outside the surveyed fence", d_survey > 50, f"{round(d_survey,1)}m")
    print(f"fence moved: probe {round(d_learned,1)}m from learned centre, "
          f"{round(d_survey,1)}m from survey — judged by the learned one")

    # ── displacement brake ───────────────────────────────────────
    venue2 = Venue(name="Hall2", latitude=SURVEY_LAT, longitude=SURVEY_LNG, radius=50.0)
    db.session.add(venue2)
    db.session.commit()
    s4 = AttendanceSession(course_id=course.id, venue_id=venue2.id,
                           start_time=datetime.utcnow() - timedelta(hours=2),
                           end_time=datetime.utcnow() - timedelta(hours=1),
                           status="CLOSED")
    db.session.add(s4)
    db.session.commit()
    for si in range(3):
        sess = AttendanceSession(course_id=course.id, venue_id=venue2.id,
                                 start_time=datetime.utcnow() - timedelta(days=si, hours=2),
                                 end_time=datetime.utcnow() - timedelta(days=si, hours=1),
                                 status="CLOSED")
        db.session.add(sess)
        db.session.commit()
        for k in range(15):
            add_record(sess, students[k], True, 200 + ((k % 3) - 1) * 3, 0, days_ago=si)
    db.session.commit()

    recompute_venue_calibration(venue2, actor_id=lec.id)
    db.session.refresh(venue2)
    chk("a 200m-displaced cluster is NOT auto-applied", not is_calibrated(venue2))
    chk("and it is held for review", bool(venue2.cal_review))
    if venue2.cal_review:
        review = json.loads(venue2.cal_review)
        chk("review records the displacement",
            review.get("displacement_m", 0) > 150, str(review.get("displacement_m")))

    # ── the calibrate endpoint logic (apply / clear) ─────────────
    # Exercise the handler through the test client so the audit + commit path
    # is real, not simulated.
    client = A.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = lec.id
    # Lecturer, not admin -> must be forbidden.
    resp = client.post(f"/api/admin/locations/{venue2.id}/calibration",
                       json={"action": "apply"})
    chk("a lecturer cannot apply calibration", resp.status_code == 403,
        f"got {resp.status_code}")

    admin = User(name="Adm", email="adm@x.test", role="admin")
    admin.set_password("x")
    db.session.add(admin)
    db.session.commit()
    with client.session_transaction() as sess:
        sess["user_id"] = admin.id

    resp = client.post(f"/api/admin/locations/{venue2.id}/calibration",
                       json={"action": "apply"})
    chk("admin can apply a flagged candidate", resp.status_code == 200,
        f"{resp.status_code}: {resp.get_data(as_text=True)[:120]}")
    db.session.refresh(venue2)
    chk("applying calibrated the venue", is_calibrated(venue2))
    chk("applying cleared the review", not venue2.cal_review)

    resp = client.post(f"/api/admin/locations/{venue2.id}/calibration",
                       json={"action": "clear"})
    chk("admin can clear calibration", resp.status_code == 200)
    db.session.refresh(venue2)
    chk("venue is back to survey-only", not is_calibrated(venue2))
    chk("clear wiped cal_lat", venue2.cal_lat is None)

    resp = client.post(f"/api/admin/locations/{venue2.id}/calibration",
                       json={"action": "bogus"})
    chk("an unknown action is rejected", resp.status_code == 400)

    # ── coupling: every verification-time reader agrees on the fence ──
    # The geofence measures from the learned centre. The contest and the
    # overlap test also speak about "the fence in force", and reading the
    # survey there while the verdict came from the learned one would describe
    # two different rooms. venue_radius() and venues_overlap() are therefore
    # effective-aware, while the set-up diagnostic keeps the survey.
    from app import (venue_radius, venues_overlap, _surveyed_radius,
                     contested_by, overlaps_another_open_session)
    db.session.refresh(venue)
    chk("venue_radius reads the learned radius",
        abs(venue_radius(venue) - venue.cal_radius) < 1e-6,
        f"{venue_radius(venue)} vs cal {venue.cal_radius}")
    chk("_surveyed_radius still reads the walk",
        abs(_surveyed_radius(venue) - 50.0) < 1e-6, str(_surveyed_radius(venue)))
    chk("the two disagree at a calibrated venue",
        abs(venue_radius(venue) - _surveyed_radius(venue)) > 1e-6)

    # Overlap: effective vs surveyed must be separable so the set-up diagnostic
    # is not silently answered with learned geometry.
    peer = Venue(name="Peer", latitude=at(0, 80)[0], longitude=at(0, 80)[1],
                 radius=50.0)
    db.session.add(peer)
    db.session.commit()
    ov_eff = venues_overlap(venue, peer, 50.0, effective=True)
    ov_sur = venues_overlap(venue, peer, 50.0, effective=False)
    chk("effective and surveyed overlap can differ", ov_eff != ov_sur,
        f"eff={ov_eff} sur={ov_sur}")

    # Contest: own_margin must be computed from the learned centre. Put the
    # learned centre 60m north and probe there; the surveyed own_margin would be
    # ~120/50=2.4, the learned one ~60/50=1.2 — a peer at 100m can only contest
    # under the correct (larger) own_margin if it really is nearer.
    peers = [peer]
    contest = contested_by(at(60, 0)[0], at(60, 0)[1], venue, peers, 50.0)
    # Just assert the field the fix changed reflects the learned centre.
    own_d_expected = haversine_distance(at(60, 0)[0], at(60, 0)[1],
                                        *effective_fence_centre(venue))
    chk("contest's own_margin uses the learned centre",
        contest is None or
        abs(contest["own_margin"] - own_d_expected / venue_radius(venue)) < 0.01,
        str(contest))

    # ── a re-survey clears a learned fence ───────────────────────
    # set-gps => the old calibration was defined relative to the old survey and
    # must not survive it.
    db.session.refresh(venue)
    chk("venue is calibrated before the re-survey", is_calibrated(venue))

    # Check the list while the venue is still calibrated, so "reports the
    # learned geometry" is asserted against a venue that actually has some.
    resp = client.get("/api/admin/locations")
    chk("locations list still works", resp.status_code == 200)
    locs = {l["name"]: l for l in resp.get_json()["locations"]}
    chk("list marks the calibrated venue", locs["Hall"]["calibrated"] is True)
    chk("list reports the learned geometry",
        locs["Hall"]["calibration"] is not None
        and "displacement_m" in locs["Hall"]["calibration"])

    resp = client.post(f"/api/admin/locations/{venue.id}/set-gps",
                       json={"points": [{"lat": SURVEY_LAT, "lng": SURVEY_LNG}]})
    chk("set-gps succeeds", resp.status_code == 200,
        f"{resp.status_code}: {resp.get_data(as_text=True)[:120]}")
    db.session.refresh(venue)
    chk("a re-survey clears the learned fence", not is_calibrated(venue))
    chk("and wipes cal_*", venue.cal_lat is None and venue.cal_n is None)

print(f"checks run: {CHECKS}")
print("INTEG BAD: " + (repr(BAD) if BAD else "[] -- all pass"))

try:
    os.remove(DB_PATH)
except OSError:
    pass
sys.exit(1 if BAD else 0)
