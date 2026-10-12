# AttendX

Calendar-driven, location-validated attendance integrated into a school portal.

This README describes what the system **is**. For what **happened** — dated
milestones, what is live in production versus local-only, known drift and the
decision record — see [docs/PROJECT_LOG.md](../docs/PROJECT_LOG.md).

## System Goal

AttendX enforces practical classroom attendance integrity for web portals:

- Student must submit from classroom proximity
- Student must submit within session time window
- Student can submit only once per session
- Lecturer controls attendance windows by schedule/date

It is intentionally not a biometric or surveillance platform.

## Core Behavior

### Lecturer

1. Open lecturer dashboard calendar
2. Select a date with scheduled courses
3. For each course on that date:
   - Select venue (school premises)
   - Set duration
   - Start attendance (for today)
   - Postpone class once (change date/time/venue)
   - View attendance list for that course/date

### Student

1. Log in
2. See enrolled courses with OPEN/CLOSED attendance status
3. For OPEN course, click Mark Attendance
4. Browser requests fresh GPS at click time
5. Backend validates and returns success/error

### Session Lifecycle

- Start sets `start_time = now`, `end_time = now + duration`, `status = OPEN`
- Session auto-closes when time expires
- No manual close endpoint or manual close button

## Validation Pipeline

When student submits attendance:

1. Session exists and is OPEN
2. Current time is within session window
3. Student has not already submitted for the session
4. Student is enrolled in that course
5. Venue GPS is configured; the fence radius is resolved
6. GPS accuracy is acceptable — **relative to that radius**, not a flat cap
7. The geofence decision runs, producing one of **three** states
8. Neighbouring venues are checked — if another venue is a better match for
   the same fix, the submission is flagged
9. A QR scan is demanded **if and only if** GPS cannot settle the question

Accepted submissions are saved as attendance records.
Rejected attempts are returned as API errors and not stored.

### The three-state decision

Step 7 does not return a boolean. Given a distance `d`, an effective
uncertainty `a` and the fence radius `r`:

| State | Condition | Outcome |
|---|---|---|
| `INSIDE` | `d + a ≤ r` | accepted — certainly inside |
| `AMBIGUOUS` | neither of the others | **accepted and flagged for lecturer review** |
| `OUTSIDE` | `d − a > r` | rejected; nothing stored |

```
confidence = clamp( (r + a − d) / (2a), 0, 1 )
```

It is 1.0 at `d = r − a` and 0.0 at `d = r + a`, and degrades linearly
between. `a` is a 95%-confidence radius per the W3C Geolocation spec, so
adding it to the fence — the old rule — asked *is it impossible they are in
the room* rather than *are they in the room*.

**Confidence is capped at 0.7 at an uncalibrated venue, and such a venue can
never reach `INSIDE`.** Samples that agree tightly prove *precision*, not
*accuracy*: indoors the device is usually reading a WiFi fingerprint, and a
badly-surveyed building yields ten copies of the same systematic error. That
scores near-certain while being 100m wrong. Nothing inside the sensor can
detect it — only external ground truth can, which is what the learned fences
of Stage 5 supply.

Step 6's cap is `min(2 × radius, MAX_GPS_ACCURACY_METRES)`. A reading whose
uncertainty exceeds twice the fence carries essentially no information about
the fence; the absolute value survives only so that a very large venue radius
cannot switch the check off.

Each record stores `verify_state`, `confidence`, `sample_count`, `spread_m`
and `acc_eff` alongside `status`. **`status` is unchanged** — it is still
`"PRESENT"` for every accepted submission, so existing reports and queries
keep working.

### Buildings, floors, and neighbouring venues

A venue may carry a `building` name and a `floor` number. **Neither is ever
used in distance maths.** A floor cannot be measured from a browser — GNSS
vertical error runs 15–50m against a 3–4m floor spacing and is usually `null`
indoors, there is no barometer API, and H3 is strictly 2D. So a floor is
*declared*, not sensed, and the two fields do exactly three jobs: detect
venues stacked above one another, label reports, and warn the admin at setup.

Two venues can conflict in two different ways:

| Conflict | Test | Fixable? |
|---|---|---|
| **Overlapping** | centres closer than the sum of the radii | Yes — shrink one radius |
| **Stacked** | same building, centres within 15m, different floors | **No** — same footprint |

Overlap is a survey error. Stacking is physical reality, and no radius fixes
it: the rooms genuinely sit on the same ground. That is why the two are
reported separately — a stacked pair is what escalates a venue to requiring a
QR scan, since light does not pass through concrete and a code shown in room
302 cannot be scanned from room 202.

**At setup** — `POST /api/admin/venues/<id>/set-gps` returns `overlaps`,
`stacked` and human-readable `warnings` after saving, so the dashboard can
say *"LT-2 overlaps this venue by 34m (centres 66m apart). Reduce one of the
radii."* Overlaps under a metre are not reported; they are rounding artifacts
an admin cannot act on.

**At verification** — step 8 ranks every venue containing the fix by the
normalised margin `d / r`, where 0.0 is dead centre and 1.0 is on the fence.
Normalising is what makes a 200m quadrangle comparable with a 50m seminar
room. If the session's own venue is not the best match, or if any stacked peer
exists, the record is downgraded to `AMBIGUOUS` and `contested` names the
rival venue.

**Step 8 can only downgrade.** It never upgrades a state and never rejects. A
student standing in a real place with a real fix does not become absent
because the campus has two rooms close together — that is a mapping problem,
not theirs. Note that `confidence` is unchanged on a contested record: the
geofence confidence is genuinely the same, because the doubt is about *which*
venue, not about the distance.

### When a QR scan is required

GPS and QR fail in opposite directions, which is why the system uses both:

| | Resolves a room? | Relayable off-site? |
|---|---|---|
| **GPS** | No — 15–50m indoors, no floors | **No** — you cannot fake being on campus |
| **QR** | **Yes** — confined by the physics of light | Yes — a photo travels |

The rotating token has always existed, but the check read `if qr_token:` — a
client could omit the field and skip it entirely, so it protected exactly the
students honest enough to use it. Step 8b now *decides* whether a scan is
required, and refuses the submission with `403` and a `reasons` list when one
is needed and missing:

| Reason | Why GPS cannot answer |
|---|---|
| `venue_uncalibrated` | the fence has never been confirmed against reality |
| `stacked_venue` | another venue sits above or below; no radius separates floors |
| `concurrent_overlapping_session` | two live classes share ground |
| `contested_by_nearby_venue` | another venue explains the fix as well or better |
| `ambiguous_fix` | the geofence itself declined to be certain |
| `coarse_fix` | uncertainty exceeds half the fence radius |

The gate is **adaptive**, not always-on: a confidently-inside fix at a
calibrated, isolated venue still needs no scan. Always-on would charge every
student for a problem belonging to specific venues.

**Every venue starts uncalibrated, so every venue starts requiring a scan and
earns its way out.** That is the bootstrap: scans produce the verified
check-ins that a learned fence is computed from. Until calibration ships,
`is_calibrated()` is `False` everywhere, so in practice a scan is required
everywhere — stricter than before, and deliberately so.

Enforcement runs at step 8b rather than at token-validation time because
whether a token is *required* depends on the verdict and the contest, neither
of which exists earlier. An invalid token is still rejected immediately at
step 2b, required or not — silently ignoring a bad scan would tell a student
who did scan that their scan did not matter.

### QR as ground truth

At an **uncalibrated** venue a valid scan also overrules an `OUTSIDE` verdict,
within `min(500m, 3 × radius)`. This breaks a real deadlock: a venue whose
surveyed centre is systematically wrong rejects every honest fix taken from
inside its own room, rejected submissions store nothing, so no verified
check-ins accumulate, so the fence can never be corrected. The venue is
permanently broken and the system cannot notice.

A scanned token is better evidence than the survey it contradicts — the code
was on the projector *in that room*, while the survey is one admin's walk with
the same offset sensor. The override lands on `AMBIGUOUS`, never `INSIDE`: the
student is credited and the record kept, but the disagreement is preserved for
the lecturer. It never applies to a calibrated venue, where the fence was
learned from the students themselves rather than from a survey.

**Why the bound is relative.** It was a flat 500m, which at the default 50m
fence is a 10× widening — the same 550m effective fence this redesign exists to
remove, reintroduced by the mechanism meant to rescue it. `3 ×` is sized against
the failure it must absorb (a survey offset, tens of metres), not against how
far a photograph can travel, which is unbounded. The absolute 500m survives only
as a ceiling, so configuring a very large venue radius cannot quietly disable
the bound. Both acceptance paths — the override and the ordinary `AMBIGUOUS`
band — read the same `qr_ground_truth_limit()`, because a bound only one path
honours is not a bound.

A row admitted by the override is recorded with `qr_trusted = false` and is
**never** fed to venue calibration, at any distance. It is by definition a fix
the geofence calls provably outside; learning the fence from it would be the
fence rewriting itself from the evidence of its own failures.

### When a student cannot scan

`POST /api/lecturer/session/<id>/manual-mark` lets the lecturer record a
student present with no GPS and no scan — for a dead battery, a broken camera,
or a cracked screen. Mandatory scanning without this would turn a hardware
failure into an absence.

It requires `student_id` and a non-empty `reason`, verifies the lecturer owns
the course, and writes an audit row naming them. The record is marked
`verify_state = "MANUAL"` with `qr_verified = false`, `qr_trusted = false` and
`sample_count = 0`, so it is unmistakably an assertion rather than a measurement
and can never be mistaken for evidence of location — including by the
calibration that learns fences only from QR-verified rows.

`qr_trusted = false` is written **explicitly**, and that matters more than it
looks. Calibration filters on `qr_trusted`, not `qr_verified`; leaving the
column `NULL` would still exclude the row under a `.is_(True)` filter but would
admit it under an equally natural `!= False`. And this is the worst row in the
system to admit, because a manual record stores the venue's own surveyed centre
as its coordinates (`lat`/`lng` are `NOT NULL`). Feeding it back would pull the
learned centre towards the very survey error calibration exists to correct — so
the more a lecturer manually marks a badly-surveyed venue, the harder that
venue becomes to fix.

## Architecture

- Frontend: Flask templates + vanilla JS
- Backend: Flask + SQLAlchemy
- Database: SQLite (default) or PostgreSQL/MySQL

## Data Model

Main entities:

- `users`
- `courses`
- `venues`
- `course_schedules`
- `enrollments`
- `attendance_sessions`
- `attendance_records`

See [schema.sql](schema.sql) for full DDL.

## Important Configuration

In [config.py](config.py):

- `GEOFENCE_RADIUS_METRES = 50`
- `MAX_GPS_ACCURACY_METRES = 500`
- `MIN_SESSION_DURATION = 5`
- `MAX_SESSION_DURATION = 60`

> `MAX_GPS_ACCURACY_METRES` is now an **upper bound on the cap**, not the cap
> itself. The effective limit is `min(2 × GEOFENCE_RADIUS_METRES, 500)`, so at
> the default radius a fix is refused above 100m. The 550m effective fence
> recorded as D3 in [docs/PROJECT_LOG.md](../docs/PROJECT_LOG.md) is resolved.

## API Overview

### Auth

- `POST /api/auth/login`
- `GET /logout`

### Lecturer

- `GET /api/lecturer/venues`
- `GET /api/lecturer/calendar?year=YYYY&month=MM`
- `GET /api/lecturer/calendar/day?date=YYYY-MM-DD`
- `POST /api/lecturer/session/start`
- `POST /api/lecturer/schedule/<schedule_id>/postpone`
- `GET /api/lecturer/schedule/<schedule_id>/attendance?date=YYYY-MM-DD`
- `GET /api/lecturer/session/<session_id>`
- `POST /api/lecturer/session/<session_id>/manual-mark`
- `GET /api/lecturer/courses`

### Student

- `GET /api/student/courses`
- `POST /api/student/attendance/mark`

## Running Locally

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

If you are running Python 3.14+, PostgreSQL support via psycopg2-binary is skipped by default; SQLite and MySQL still work out of the box.

### 2. Configure environment (optional)

Create `.env` next to `app.py`:

```env
SECRET_KEY=change-me
DATABASE_URL=sqlite:///attendance.db
```

SQLite is fine locally. It is rejected on Vercel — see [Deploying to Vercel](#deploying-to-vercel).

### 3. Start app

```bash
python app.py
```

Then open `http://localhost:5000`. `/` redirects to the dashboard for your role.

### Local Ports

One Flask app, optionally served on three ports so each role gets its own
session cookie and you can stay logged in as all three at once:

| Port | Script | Cookie | Dashboard |
|------|--------|--------|-----------|
| 5000 | `run_student.py`  | `attendx_session_student`  | `/dashboard/student` |
| 5001 | `run_lecturer.py` | `attendx_session_lecturer` | `/dashboard/lecturer` |
| 5002 | `run_admin.py`    | `attendx_session_admin`    | `/dashboard/admin` |

Launch all three:

```bash
python run_all.py
```

**Do not run `python app.py` and `python run_student.py` at the same time —
both bind port 5000.** The second one fails with
`OSError: [WinError 10048] ... normally permitted`. `run_all.py` checks all
three ports first and exits with a message instead of half-starting.

Check what is holding a port (PowerShell):

```powershell
Get-NetTCPConnection -LocalPort 5000,5001,5002 -State Listen |
  Select-Object LocalPort, OwningProcess

# identify the process
Get-Process -Id (Get-NetTCPConnection -LocalPort 5000 -State Listen).OwningProcess

# free it
Stop-Process -Id <pid>
```

Verify each port answers:

```powershell
5000,5001,5002 | ForEach-Object {
  "$_ -> " + (Invoke-WebRequest "http://localhost:$_/login" -UseBasicParsing).StatusCode
}
```

The `run_*.py` scripts are **local only**. Vercel does not use them and does
not use ports — see below.

### Demo Accounts

- Admin: `admin@university.edu` / `password123`
- Lecturer: `amara.osei@university.edu` / `password123`
- Student: `chidi.nwosu@student.edu` / `password123`

These are seeded automatically on first start against an empty database —
including on Vercel. See [Demo seeding in production](#demo-seeding-in-production).

## Deploying to Vercel

One Vercel project. Every path routes to `attendx_system/app.py`, configured in
[vercel.json](../vercel.json):

```json
{
  "version": 2,
  "builds": [{ "src": "attendx_system/app.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "attendx_system/app.py" }]
}
```

There are **no ports in production**. The local 5000/5001/5002 split does not
exist on Vercel — there is one URL, and `/` redirects by role:

| Local | Vercel |
|-------|--------|
| `localhost:5000/dashboard/student`  | `https://<project>.vercel.app/dashboard/student` |
| `localhost:5001/dashboard/lecturer` | `https://<project>.vercel.app/dashboard/lecturer` |
| `localhost:5002/dashboard/admin`    | `https://<project>.vercel.app/dashboard/admin` |

Because it is one host, one session cookie is active at a time — you are logged
in as one role, not three. That is the expected production behaviour.

### 1. Provision a hosted Postgres

Vercel's filesystem is ephemeral and read-only at runtime, so **SQLite will not
persist** — any writes vanish when the function instance is recycled.
[config.py](config.py) refuses to boot rather than fail silently:

```
RuntimeError: Set DATABASE_URL to hosted Postgres on Vercel.
SQLite does not persist on the serverless filesystem.
```

Use Vercel Postgres, Neon, Supabase, or any hosted Postgres.

### 2. Set environment variables

In **Vercel → Project → Settings → Environment Variables**, for Production
(and Preview, if you use it):

| Variable | Value |
|----------|-------|
| `SECRET_KEY` | 64-char random hex — generate with the command below |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/dbname` |

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

`SECRET_KEY` signs session cookies **and the rotating QR token**. If it changes
between deploys, everyone is logged out.

> **The build now fails outright if this is unset, blank, or still the
> placeholder while `VERCEL=1`.** This is deliberate and it is stricter than it
> used to be, because the key's job changed. It previously signed only the
> session cookie, where a known value means forgeable sessions — bad, but
> ordinary hygiene. It now also signs the QR token, which is the thing that
> admits a student to a class. A known key computes valid tokens offline, from
> anywhere, indefinitely: no room, no projector, not even a photograph of the
> code. That is a complete bypass of the mechanism the whole verification
> redesign rests on, so `config.py` refuses to boot rather than warn. Local
> development is unaffected — the check only applies when `VERCEL=1`.

Handled for you in [config.py](config.py) when `VERCEL=1`:

- `postgres://` is rewritten to `postgresql://` (SQLAlchemy 2 dropped the short form)
- `?sslmode=require` is appended when absent
- `NullPool` — serverless functions must not hold a connection pool across invocations
- `SESSION_COOKIE_SECURE` is forced on
- `SECRET_KEY` is required to be a real value, as above

Redeploy after changing env vars; they are read at build/boot, not live.

### 3. Python version

Pinned to 3.12 in [.python-version](../.python-version). Vercel reads
`pyproject.toml`, `.python-version`, or `Pipfile.lock` — **not** `runtime.txt`.

### Demo seeding in production

`seed_demo_if_empty()` runs when `app.py` is imported, which on Vercel means the
first cold start writes the demo accounts above — including
`admin@university.edu` / `password123` — into your production database.

If this deployment is reachable publicly, change that password immediately after
the first deploy, or gate the seeding:

```python
if os.environ.get("SEED_DEMO_DATA", "").lower() in ("1", "true", "yes"):
    seed_demo_if_empty()
```

### Troubleshooting

| Symptom | Cause |
|---------|-------|
| `ModuleNotFoundError: No module named 'config'` | `app.py` puts its own folder on `sys.path` before importing `config`; check that edit survived |
| `TemplateNotFound` | Vercel's cwd is the project root, not `attendx_system/`; `template_folder` is set to an absolute path for this reason |
| 500 on first request only | Cold-start `db.create_all()` could not reach Postgres — check `DATABASE_URL` and that the host allows external connections |
| Logged out on every deploy | `SECRET_KEY` is unset or changing between builds |
| Build fails: `SECRET_KEY` … must be set | Intended. Set a real `SECRET_KEY` in the Vercel dashboard and redeploy — the placeholder now signs QR tokens, so it is refused in production |

## Notes on Compatibility

The backend accepts legacy `ACTIVE` rows as open sessions while exposing OPEN/CLOSED semantics in API responses.
This keeps older databases usable while newer flows use OPEN/CLOSED language in UI and API.

## Scope Choices

- Included: calendar scheduling, venue-based location, postpone support, auto-close sessions
- Included: inline student course cards with fast mark action
- Not included: persisted rejected-attempt audit logs
- Not included: biometrics/device binding