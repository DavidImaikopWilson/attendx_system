"""
Stage 5 calibration logic — verification item #5 (thresholds and poisoning
guards) plus the arithmetic of the learned fence.

The three pure functions that decide whether a venue gets calibrated, and from
what, are lifted out of app.py by AST and exec'd in isolation, so the test
cannot drift from the code and does not need a database or a DATABASE_URL. The
DB orchestrator recompute_venue_calibration() is NOT tested here — it is thin
glue over these three, and the behaviour that matters is theirs.

Usage:  python docs/verification/app_calibration_test.py attendx_system/app.py
"""

import ast
import math
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "attendx_system/app.py"
source = open(SRC, encoding="utf-8").read()
tree = ast.parse(source)

WANT_FUNCS = {"haversine_distance", "_median", "_percentile",
              "compute_venue_calibration", "calibration_decision"}
WANT_CONSTS = {"CAL_MIN_RECORDS", "CAL_MIN_STUDENTS", "CAL_MIN_SESSIONS",
               "CAL_MIN_DAYS", "CAL_RADIUS_PERCENTILE", "CAL_RADIUS_BUFFER_M",
               "CAL_MAX_DISPLACEMENT_M", "CAL_MAX_RADIUS_EXPANSION_FACTOR"}

picked = []
found_funcs = set()
found_consts = set()
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in WANT_FUNCS:
        picked.append(node)
        found_funcs.add(node.name)
    elif isinstance(node, ast.Assign):
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if names & WANT_CONSTS:
            picked.append(node)
            found_consts |= (names & WANT_CONSTS)

missing_f = WANT_FUNCS - found_funcs
missing_c = WANT_CONSTS - found_consts
if missing_f or missing_c:
    raise SystemExit(f"FATAL: not found in {SRC} -- funcs {missing_f}, consts {missing_c}")

# Preserve source order so the constants exist before the functions use them.
picked.sort(key=lambda n: n.lineno)
ns = {"math": math}
exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), ns)

haversine_distance       = ns["haversine_distance"]
compute_venue_calibration = ns["compute_venue_calibration"]
calibration_decision     = ns["calibration_decision"]

# Echo the thresholds we are testing against so a future tuning is visible in
# the output rather than silently changing what "pass" means.
print(f"thresholds: n>={ns['CAL_MIN_RECORDS']} students>={ns['CAL_MIN_STUDENTS']} "
      f"sessions>={ns['CAL_MIN_SESSIONS']} days>={ns['CAL_MIN_DAYS']}; "
      f"displacement cap {ns['CAL_MAX_DISPLACEMENT_M']}m; "
      f"radius cap {ns['CAL_MAX_RADIUS_EXPANSION_FACTOR']}x survey")

SURVEY_LAT, SURVEY_LNG = 6.5244, 3.3792
SURVEY_RADIUS = 50.0
M_PER_DEG = 111_320.0


def at(d_north, d_east, lat0=SURVEY_LAT, lng0=SURVEY_LNG):
    """A point d_north/d_east metres from (lat0, lng0)."""
    lat = lat0 + d_north / M_PER_DEG
    lng = lng0 + d_east / (M_PER_DEG * math.cos(math.radians(lat0)))
    return lat, lng


def make_records(n, students, sessions, days, spread_m=10.0,
                 centre_north=0.0, centre_east=0.0):
    """
    n records spread +/- spread_m around a centre that is centre_north/east
    metres from the survey, cycling through the given counts of distinct
    students / sessions / days so each axis can be driven independently.
    """
    recs = []
    for i in range(n):
        # Deterministic jitter in [-spread_m, +spread_m], no RNG.
        frac = (i / max(1, n - 1)) * 2 - 1       # -1 .. +1
        dn = centre_north + frac * spread_m
        de = centre_east + ((i % 5) / 4.0 * 2 - 1) * spread_m
        lat, lng = at(dn, de)
        recs.append({
            "lat": lat, "lng": lng,
            "student_id": i % students,
            "session_id": i % sessions,
            "day": f"D{i % days}",
        })
    return recs


BAD = []
CHECKS = 0


def chk(label, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if not cond:
        BAD.append(f"{label}: {detail}" if detail else label)


def decide(recs):
    cand = compute_venue_calibration(recs, SURVEY_LAT, SURVEY_LNG, SURVEY_RADIUS)
    return cand, calibration_decision(cand, SURVEY_RADIUS)


# ── _percentile sanity ───────────────────────────────────────────
_percentile = ns["_percentile"]
chk("p0 is the min", _percentile([5, 1, 9, 3], 0) == 1)
chk("p100 is the max", _percentile([5, 1, 9, 3], 100) == 9)
chk("p50 interpolates the middle", abs(_percentile([0, 10, 20, 30], 50) - 15) < 1e-9,
    _percentile([0, 10, 20, 30], 50))
chk("single value", _percentile([7], 90) == 7)

# ── centre is robust to an outlier (median, not mean) ────────────
recs = make_records(29, 15, 3, 2, spread_m=8.0)
recs.append({"lat": at(400, 0)[0], "lng": at(400, 0)[1],
             "student_id": 99, "session_id": 2, "day": "D1"})  # 400m outlier
cand, _ = decide(recs)
# The median centre must stay within a few metres of the survey despite the
# 400m outlier; a mean would be dragged ~13m north by that one point.
chk("median centre resists the 400m outlier",
    haversine_distance(cand["lat"], cand["lng"], SURVEY_LAT, SURVEY_LNG) < 10,
    f"{round(haversine_distance(cand['lat'], cand['lng'], SURVEY_LAT, SURVEY_LNG),1)}m off")

# ── verification item #5, line 1: 29 records -> still uncalibrated ─
cand, (outcome, reason) = decide(make_records(29, 15, 3, 2))
print(f"29 records              -> {outcome:12s} ({reason})")
chk("29 records is insufficient", outcome == "insufficient", reason)

# ── verification item #5, line 2: 30/15/3/2 -> calibrated ────────
cand, (outcome, reason) = decide(make_records(30, 15, 3, 2))
print(f"30/15/3/2 near survey   -> {outcome:12s} ({reason})")
chk("30/15/3/2 promotes", outcome == "promote", reason)
chk("promoted radius is sane (< 2x survey)", cand["radius"] < 2 * SURVEY_RADIUS,
    f"radius={round(cand['radius'],1)}")
chk("promoted centre is near survey", cand["displacement_m"] < 20,
    f"disp={round(cand['displacement_m'],1)}")

# ── verification item #5, line 3: clustered 200m away -> FLAG ────
cand, (outcome, reason) = decide(
    make_records(40, 15, 3, 2, centre_north=200.0))
print(f"40/15/3/2 @ 200m north  -> {outcome:12s} ({reason})")
chk("a 200m-displaced cluster is flagged, not applied", outcome == "flag", reason)
chk("and it is flagged for displacement", "centre" in reason, reason)
chk("the displacement is ~200m", abs(cand["displacement_m"] - 200) < 25,
    f"disp={round(cand['displacement_m'],1)}")

# ── each breadth axis gates independently ────────────────────────
for label, recs, axis in [
    ("only 10 students", make_records(30, 10, 3, 2), "students"),
    ("only 2 sessions",  make_records(30, 15, 2, 2), "sessions"),
    ("only 1 day",       make_records(30, 15, 3, 1), "days"),
]:
    _, (outcome, reason) = decide(recs)
    print(f"{label:23s} -> {outcome:12s} ({reason})")
    chk(f"{label} is insufficient", outcome == "insufficient", reason)
    chk(f"{label} names the {axis} axis", axis in reason, reason)

# ── radius brake: breadth met, centre on survey, but spread wide ─
# p90 of distances must exceed 2x survey (100m) so the radius guard trips while
# displacement stays ~0. A symmetric spread to ~160m does that.
cand, (outcome, reason) = decide(make_records(40, 15, 3, 2, spread_m=160.0))
print(f"wide spread on survey   -> {outcome:12s} ({reason})")
chk("an over-wide learned radius is flagged", outcome == "flag", reason)
chk("and it is flagged for radius", "radius" in reason, reason)
chk("displacement stayed small", cand["displacement_m"] < 25,
    f"disp={round(cand['displacement_m'],1)}")

# ── empty input ──────────────────────────────────────────────────
chk("no records -> None candidate",
    compute_venue_calibration([], SURVEY_LAT, SURVEY_LNG, SURVEY_RADIUS) is None)
chk("no records -> insufficient",
    calibration_decision(None, SURVEY_RADIUS)[0] == "insufficient")

# ── TEETH: the thresholds actually bind ──────────────────────────
# One fewer record than the floor must fail where the floor passes, or the
# count check is doing nothing.
_, (at_floor, _)    = decide(make_records(30, 15, 3, 2))
_, (below_floor, _) = decide(make_records(29, 15, 3, 2))
print(f"teeth: n=30 -> {at_floor}, n=29 -> {below_floor}")
chk("TEETH the record floor is exactly 30",
    at_floor == "promote" and below_floor == "insufficient",
    f"n30={at_floor} n29={below_floor}")

# The displacement cap must be the line between promote and flag: a cluster
# just inside 150m promotes, one well past it flags.
_, (near, _) = decide(make_records(40, 15, 3, 2, centre_north=120.0))
_, (far, _)  = decide(make_records(40, 15, 3, 2, centre_north=300.0))
print(f"teeth: @120m -> {near}, @300m -> {far}")
chk("TEETH the displacement cap bites", near == "promote" and far == "flag",
    f"120m={near} 300m={far}")

print(f"checks run: {CHECKS}")
print("CAL BAD: " + (repr(BAD) if BAD else "[] -- all pass"))
sys.exit(1 if BAD else 0)
