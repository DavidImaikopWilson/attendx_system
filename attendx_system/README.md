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

Create `.env` in project root:

```env
SECRET_KEY=change-me
DATABASE_URL=sqlite:///attendance.db
```

### 3. Start app

```bash
python app.py
```

Then open `http://localhost:5000`.

### Demo Accounts

- Admin: `admin@university.edu` / `password123`
- Lecturer: `amara.osei@university.edu` / `password123`
- Student: `chidi.nwosu@student.edu` / `password123`

## Notes on Compatibility

The backend accepts legacy `ACTIVE` rows as open sessions while exposing OPEN/CLOSED semantics in API responses.
This keeps older databases usable while newer flows use OPEN/CLOSED language in UI and API.

## Scope Choices

- Included: calendar scheduling, venue-based location, postpone support, auto-close sessions
- Included: inline student course cards with fast mark action
- Not included: persisted rejected-attempt audit logs
- Not included: biometrics/device binding