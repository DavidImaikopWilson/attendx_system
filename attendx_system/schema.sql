-- =============================================================
-- ATTENDX SCHEMA (Calendar + Geofence Attendance)
-- Compatible with PostgreSQL and MySQL
-- =============================================================

-- USERS TABLE
CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(120)  NOT NULL,
    email         VARCHAR(180)  NOT NULL UNIQUE,
    password_hash VARCHAR(255)  NOT NULL,
    role          VARCHAR(20)   NOT NULL CHECK (role IN ('student', 'lecturer', 'admin')),
    is_superuser  BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- COURSES TABLE
CREATE TABLE courses (
    id            SERIAL PRIMARY KEY,
    course_code   VARCHAR(20)   NOT NULL UNIQUE,
    course_name   VARCHAR(200)  NOT NULL,
    lecturer_id   INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    location_lat  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    location_lng  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    radius        DOUBLE PRECISION NOT NULL DEFAULT 50.0,  -- geofence radius in metres
    created_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- SCHOOL PREMISES / VENUES
CREATE TABLE venues (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(120) NOT NULL UNIQUE,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    radius        DOUBLE PRECISION,  -- geofence radius in metres
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- PERSONALIZED LECTURER COURSE SCHEDULES (DATE-BASED)
CREATE TABLE course_schedules (
    id            SERIAL PRIMARY KEY,
    course_id     INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    class_date    DATE NOT NULL,
    start_time    TIME,
    venue_id      INTEGER REFERENCES venues(id) ON DELETE SET NULL,
  postpone_count INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (course_id, class_date, start_time)
);

-- ENROLLMENTS
CREATE TABLE enrollments (
    id            SERIAL PRIMARY KEY,
    student_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id     INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    enrolled_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, course_id)
);

-- ATTENDANCE SESSIONS
-- Note: sessions close automatically when current_time > end_time.
CREATE TABLE attendance_sessions (
    id            SERIAL PRIMARY KEY,
    course_id     INTEGER   NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    venue_id      INTEGER   REFERENCES venues(id) ON DELETE SET NULL,
    start_time    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_time      TIMESTAMP NOT NULL,
    status        VARCHAR(10) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ATTENDANCE RECORDS
-- Stores accepted submissions only. Rejected attempts are returned as API errors and not persisted.
CREATE TABLE attendance_records (
    id            SERIAL PRIMARY KEY,
    session_id    INTEGER   NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
    student_id    INTEGER   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    timestamp     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lat           DOUBLE PRECISION NOT NULL,
    lng           DOUBLE PRECISION NOT NULL,
    accuracy      DOUBLE PRECISION NOT NULL,
    distance_m    DOUBLE PRECISION NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'PRESENT' CHECK (status IN ('PRESENT')),
    UNIQUE (session_id, student_id)
);

-- ADMIN ACTIVITY LOGS
CREATE TABLE admin_logs (
    id          SERIAL PRIMARY KEY,
    admin_id    INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action      VARCHAR(60)  NOT NULL,   -- e.g. 'ADD_VENUE', 'PROMOTE_USER'
    target_type VARCHAR(30),             -- 'venue', 'course', 'enrollment', 'user'
    target_id   INTEGER,                 -- ID of the affected row
    details     TEXT,                    -- JSON: { "before": {...}, "after": {...} }
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- INDEXES
-- =============================================================
CREATE INDEX idx_courses_lecturer        ON courses(lecturer_id);
CREATE INDEX idx_enrollments_student     ON enrollments(student_id);
CREATE INDEX idx_enrollments_course      ON enrollments(course_id);
CREATE INDEX idx_venues_name             ON venues(name);
CREATE INDEX idx_schedule_course_date    ON course_schedules(course_id, class_date);
CREATE INDEX idx_schedule_date           ON course_schedules(class_date);
CREATE INDEX idx_sessions_course         ON attendance_sessions(course_id);
CREATE INDEX idx_sessions_status         ON attendance_sessions(status);
CREATE INDEX idx_records_session         ON attendance_records(session_id);
CREATE INDEX idx_records_student         ON attendance_records(student_id);

-- =============================================================
-- SEED DATA (DEMO)
-- Passwords are bcrypt hashes of "password123"
-- =============================================================

-- Passwords are bcrypt hashes of "password123"
INSERT INTO users (name, email, password_hash, role, is_superuser) VALUES
  ('System Admin',      'admin@university.edu',         '$2b$12$KGKI7XwWd6DVrHSb7VBtW.CuVpwaiIFRzFIvTQI7/TNxbBTmPmHJe', 'admin',    TRUE),
  ('Dr. Amara Osei',    'amara.osei@university.edu',    '$2b$12$KGKI7XwWd6DVrHSb7VBtW.CuVpwaiIFRzFIvTQI7/TNxbBTmPmHJe', 'lecturer', FALSE),
  ('Dr. Funmi Adeyemi', 'funmi.adeyemi@university.edu', '$2b$12$KGKI7XwWd6DVrHSb7VBtW.CuVpwaiIFRzFIvTQI7/TNxbBTmPmHJe', 'lecturer', FALSE),
  ('Chidi Nwosu',       'chidi.nwosu@student.edu',      '$2b$12$KGKI7XwWd6DVrHSb7VBtW.CuVpwaiIFRzFIvTQI7/TNxbBTmPmHJe', 'student',  FALSE),
  ('Amina Bello',       'amina.bello@student.edu',      '$2b$12$KGKI7XwWd6DVrHSb7VBtW.CuVpwaiIFRzFIvTQI7/TNxbBTmPmHJe', 'student',  FALSE),
  ('Tunde Fashola',     'tunde.fashola@student.edu',    '$2b$12$KGKI7XwWd6DVrHSb7VBtW.CuVpwaiIFRzFIvTQI7/TNxbBTmPmHJe', 'student',  FALSE);

INSERT INTO courses (course_code, course_name, lecturer_id) VALUES
  ('CSC401', 'Advanced Algorithms',          2),
  ('CSC403', 'Computer Networks',            2),
  ('CSC405', 'Database Management Systems',  3);

INSERT INTO enrollments (student_id, course_id) VALUES
  (4, 1), (4, 2), (4, 3),
  (5, 1), (5, 3),
  (6, 2), (6, 3);

INSERT INTO venues (name) VALUES
  ('NAS 1'),
  ('NAS 2'),
  ('NAS 3'),
  ('NAS 4'),
  ('NAS 5');

-- Example schedule rows (adjust dates as needed for your semester calendar):
-- INSERT INTO course_schedules (course_id, class_date, start_time, venue_id) VALUES
--   (1, '2026-04-01', '09:00:00', 1),
--   (2, '2026-04-01', '11:00:00', 2),
--   (1, '2026-04-03', '09:00:00', 1),
--   (3, '2026-04-03', '14:00:00', 3);
