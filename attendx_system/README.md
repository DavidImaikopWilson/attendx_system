# AttendX

Calendar-driven, location-validated attendance integrated into a school portal.

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
5. GPS accuracy is acceptable
6. Distance is within fixed geofence radius

Accepted submissions are saved as attendance records.
Rejected attempts are returned as API errors and not stored.

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
- `MAX_GPS_ACCURACY_METRES = 30`
- `MIN_SESSION_DURATION = 5`
- `MAX_SESSION_DURATION = 60`

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

`SECRET_KEY` signs session cookies. If it is left at the default, sessions are
forgeable; if it changes between deploys, everyone is logged out.

Handled for you in [config.py](config.py) when `VERCEL=1`:

- `postgres://` is rewritten to `postgresql://` (SQLAlchemy 2 dropped the short form)
- `?sslmode=require` is appended when absent
- `NullPool` — serverless functions must not hold a connection pool across invocations
- `SESSION_COOKIE_SECURE` is forced on

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

## Notes on Compatibility

The backend accepts legacy `ACTIVE` rows as open sessions while exposing OPEN/CLOSED semantics in API responses.
This keeps older databases usable while newer flows use OPEN/CLOSED language in UI and API.

## Scope Choices

- Included: calendar scheduling, venue-based location, postpone support, auto-close sessions
- Included: inline student course cards with fast mark action
- Not included: persisted rejected-attempt audit logs
- Not included: biometrics/device binding