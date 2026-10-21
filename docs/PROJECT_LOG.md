# AttendX — Project Log

[README.md](../attendx_system/README.md) documents what the system **is**.
This file records what **happened**: when, in what order, and whether it is live.

## How to read this log

**One hard line is drawn at the deployment boundary.** Every entry carries a
scope of exactly `Local` or `Production`, because this project's most persistent
confusion is that the three-port role split and the `run_*.py` scripts exist on
a developer machine and **do not exist in production at all**. Scope is a
column, not a sentence someone might skip.

**Status vocabulary — fixed and small:**

| Status | Meaning |
|---|---|
| `SHIPPED` | merged to `main` |
| `LIVE` | observed working in production |
| `IN PROGRESS` | started, not merged |
| `PLANNED` | designed, not started |
| `BLOCKED` | cannot proceed; blocker named in the entry |
| `DROPPED` | deliberately abandoned; reason recorded |

`SHIPPED` and `LIVE` are deliberately different words. Commit `15dc187` was
merged at 12:28 on 2026-09-19 and the site still returned
`500 FUNCTION_INVOCATION_FAILED`, because the blocker was a dashboard
environment variable rather than code. A log that collapses those two into
"done" loses the only fact that mattered that day.

**Method.** Waterfall supplies the gates — a stage does not open until its entry
criteria are `LIVE`. Agile supplies the cadence — one stage at a time, each
independently shippable and independently revertible. The exactness comes from
every exit criterion being a numbered test in the plan's Verification section,
never a judgement call.

**Maintenance rule.** This log is updated in the *same commit* as the change it
describes. A stage is not done until its row moves. A log updated separately is
a log that stops being written.

---

## 1. Status board

The only place status is read, so it cannot disagree with itself.

| Phase | Scope | Status | Evidence | Closed |
|---|---|---|---|---|
| Baseline application | Local | `SHIPPED` | `bf9b3eb` | 2026-09-18 |
| Vercel single-project deploy | Production | `LIVE` | `865ee80`, `15dc187` + dashboard config | 2026-09-19 |
| Design study — accuracy, collision, floors | — | `SHIPPED` | plan `rustling-purring-chipmunk` | 2026-09-23 |
| Stage 0 — project record | — | `SHIPPED` | this file; all references verified | 2026-09-23 |
| Stage 1 — trustworthy GPS fix | Local + Production | `SHIPPED` | 17/17 checks; median centroid 0.71m off where the mean was 40.43m | 2026-09-25 |
| Stage 2 — three-state decision | Local + Production | `SHIPPED` | 17/17 unit + 23/23 end-to-end; a 300m fix the old rule accepted at 350m is now refused at 100m | 2026-09-26 |
| Stage 3 — building, floor, collision | Local + Production | `SHIPPED` | 17/17 collision + 16/16 contest + 20/20 end-to-end + 10/10 downgrade; a stacked venue now flags itself instead of silently accepting | 2026-09-26 |
| Stage 4 — adaptive QR gating | Local + Production | `SHIPPED` | 11/11 gate + 16/16 override + 6/6 overlap + 25/25 manual + 15/15 regression, then 8 defects found by self-review and remediated: 7/7 + 6/6 + 6/6 + 2/2 + 6/6 + 9/9 re-run clean, 47/47 adversarial probes repelled, 9/9 remediation regressions | 2026-09-30 |
| Client review — findings F1–F6 | Local + Production | `SHIPPED` | 6 findings from the client review; F1/F2/F6 (capture), F3/F4 (poll), F5 (lecturer accuracy) fixed. 68 checks across three suites, each with a pre-fix contrast. Author-confirmed, not independently verified (D11) | 2026-10-06 |
| Stage 5 — learned venue fences | Local + Production | `SHIPPED` | 25/25 pure-logic + 39/39 integration, author-written. Independent review attempted (`wf_d89a1d51-13d`) and failed 4/4 — HTTP 402 and DNS, zero results; its `{"raised":0}` is an artefact, not a pass (D11). Author self-review during the attempt found and fixed a real reader-coupling defect (DR-42) | 2026-10-07 |
| Schema upgrade hardening | Local + Production | `SHIPPED` | 26/26 upgrade test. DDL now one-statement-per-transaction with rollback (DR-44), closing a path where one failed `ALTER` on the first cold start after a push would 500 every route including `/login`. D12 count corrected 9→16 | 2026-10-11 |
| Blind-spot sweep | Local + Production | `SHIPPED` | Seven categories of environment-gap and untested-path defects enumerated and checked against source. Six clean or benign; one real — the check-then-insert race, now 409 instead of 500 (DR-45), proven by a forced-race test with a measured pre-fix contrast. Also names what it cannot cover | 2026-10-12 |
| Stage 6 — collusion detection | Local + Production | `PLANNED` | | |
| **Outage 2026-10-14 → boot fix (push 1 of 2)** | Production | `LIVE` | Total outage: every route 500, cause `ModuleNotFoundError: No module named 'psycopg'` at `db = SQLAlchemy(app)`. **Not caused by any application change** — the reverted baseline failed identically. Unpinned SQLAlchemy resolved a version routing bare `postgresql://` to psycopg3, which was never installed. Fixed by pinning SQLAlchemy, naming the driver in the URL, and making the import-time bootstrap non-fatal (D6 closed). Stage 1–5 held back for push 2 | 2026-10-15 |
| **Push 2 — Stage 1–5 onto main** | Production | `LIVE` | The boot fix cherry-picked onto `stage-1-5-work` (a merge silently deletes the stages — DR-49), plus two holes found in the process: the `SECRET_KEY` guard accepted the key this repo publishes (DR-51), and both client suites could not find their pre-fix contrasts on a CRLF checkout (DR-50). 182/182 across all eight suites. **Observed live 2026-10-21**: `/healthz` 200 `{"ok":true}` — the sixteen-column upgrade against Neon (D12) completed — `/login` 200, `/` 302, and `GET /api/admin/locations/1/calibration` 405 where the baseline 404s | 2026-10-21 |

---

## 2. Era I — Pre-deployment (CLOSED)

Local-only. No hosted database, no public URL. The three-port split was the
whole interaction model.

| Date | Event | Scope | Evidence |
|---|---|---|---|
| 2026-09-18 09:22 | Baseline captured: Flask 3.0.3 + Flask-SQLAlchemy 3.1.1, SQLite, three-port role split (5000 student / 5001 lecturer / 5002 admin), demo seeding on empty database, single-sample GPS capture, rotating HMAC QR tokens present but skippable, geofence effectively 550m | Local | `bf9b3eb` |

**What "before deployment" meant concretely** — recorded because none of it
survives into production:

- Database was `sqlite:///attendance.db`, a file on disk.
- Three processes, three session cookies, so all three roles could be logged in
  simultaneously. `run_all.py` guarded the ports.
- `SECRET_KEY` defaulted; sessions were forgeable and nobody cared.
- No SSL, no connection-pool discipline, no cold starts.

---

## 3. Era II — Deployment (the boundary)

| Date | Event | Scope | Evidence |
|---|---|---|---|
| 2026-09-19 03:55 | Single Vercel project. [vercel.json](../vercel.json) v2 `builds`/`routes` send every path to `attendx_system/app.py`. Python pinned to 3.12 via [.python-version](../.python-version) — Vercel reads `pyproject.toml`, `.python-version` or `Pipfile.lock`, **not** `runtime.txt`. SQLite refused when `VERCEL=1`; `NullPool` for serverless; `sslmode=require` appended when absent; `SESSION_COOKIE_SECURE` forced on | Production | `865ee80` |
| 2026-09-19 12:28 | `DATABASE_URL` validation moved into module-level `_resolve_database_url()` in [config.py](../attendx_system/config.py). Unusable URLs now fail with one readable `RuntimeError` instead of a psycopg2 traceback. Also fixed a `NameError`: a generator expression inside the `Config` class body cannot read class attributes, because a comprehension gets its own scope | Production | `15dc187`; 7 URL cases exercised — 5 refused, 2 accepted; local SQLite still resolved with `VERCEL` unset |
| 2026-09-19 (dashboard) | **Neon Postgres provisioned.** Region Washington D.C. (`iad1`), matching the default function region. Custom Prefix left **empty** so the variable is plain `DATABASE_URL` — a prefix is *prepended* to every name, producing `STORAGE_DATABASE_URL`, which `config.py` never reads. The integration first failed with *"This project already has an existing environment variable with name DATABASE_URL"*; the cause was a hand-created placeholder `postgresql://username:password@localhost:5432/attendance_db` copied from `.env.example`, which had to be deleted before the integration would attach. `SECRET_KEY` set to 64 random hex characters. Redeployed — **env vars are read at build time, not live** | Production | **not in git** — Vercel and Neon dashboard state |
| 2026-09-19 | Deployment confirmed serving | Production | user-observed |

### Why that third row is the reason this file exists

The region choice, the empty prefix, the deleted placeholder and the redeploy
requirement are the four facts that broke the deployment and then fixed it.
**Not one of them is recoverable from the repository.** `git log` shows two
commits that look like they should have worked, and no trace of the hours spent
on a placeholder connection string.

Anything configured in a dashboard belongs in this log or it is lost. That
includes, going forward: env-var changes, Neon branch or compute changes,
region changes, and anything toggled in Vercel project settings.

### What changed at the boundary

| | Local (Era I) | Production (Era II onward) |
|---|---|---|
| Database | SQLite file | Neon Postgres, pooled connection |
| Connection pooling | default pool | `NullPool` — functions must not hold pools across invocations |
| Ports | 5000 / 5001 / 5002 | **none** — one URL, `/` redirects by role |
| Concurrent roles | all three at once | one — one host, one session cookie |
| Cookies | plain | `SESSION_COOKIE_SECURE` forced |
| `run_*.py` | the entry points | **unused; Vercel never loads them** |
| Filesystem | writable | ephemeral and read-only at runtime |
| Config errors | traceback on request | `RuntimeError` at import, before first request |

---

## 4. Era III — Post-deployment (OPEN)

| Date | Event | Scope | Evidence |
|---|---|---|---|
| 2026-09-23 | Design study: GPS accuracy, venue collision, multi-floor verification. Established that the accuracy problem is arithmetic and capture strategy, not hardware. No code written | — | plan `rustling-purring-chipmunk` |
| 2026-09-23 | Stage 0 — this log created; D1 reconciled in the same pass | — | Verification item #10 passes |
| 2026-09-25 | Stage 1 — multi-sample GPS capture and robust fix. Client collects every fix over ~8s instead of resolving on the first; server collapses them with a component-wise median and derives an honest `acc_eff` | Local + Production | 17/17 end-to-end checks |
| 2026-09-26 | Stage 2 — the geofence stops returning a boolean. `INSIDE` / `AMBIGUOUS` / `OUTSIDE` plus a confidence score, confidence capped at uncalibrated venues, accuracy capped relative to the fence. D3 closed; D9 opened | Local + Production | 17/17 unit, 23/23 end-to-end |
| 2026-09-26 | Stage 3 — venues gain `building` and `floor`, neither ever used in distance maths. Overlap and stacking detected at setup time and reported to the admin; at verification time nearest-venue-wins can downgrade a submission to `AMBIGUOUS`, never reject it | Local + Production | 17/17 collision, 16/16 contest, 20/20 end-to-end, 10/10 downgrade |
| 2026-09-26 | Stage 4 — the QR token stops being optional. A scan is demanded only where GPS cannot decide, a scan overrules `OUTSIDE` at an uncalibrated venue, the relay window halves to ~60s, and a lecturer gains a manual override for students who cannot scan. D2, D4 and D9 closed | Local + Production | 11/11 gate, 16/16 override, 6/6 overlap, 25/25 manual, 15/15 regression |
| 2026-10-06 | Client review — six findings on the student page the server stages never touched. F1/F2/F6 repair the Stage-1 capture (window start, transient-error tolerance, early-exit over-trust); F3/F4 repair the 30s poll that detached a live capture and reset the GPS indicator; F5 serves the lecturer `acc_eff` instead of the device's self-report. D13 opened and resolved in the same pass; D11 widened to cover this review too | Local + Production | 68/68 across three pre-fix-contrast suites in `docs/verification/` |
| 2026-10-07 | Stage 5 — venue fences are learned from QR-verified check-ins instead of trusting one walked survey. Only `qr_trusted` rows feed the learner; the centre is a median and the radius a 90th percentile; promotion needs breadth on four axes; a displaced or over-wide candidate is flagged for an admin, never auto-applied. `effective_fence_centre()` makes the geometry read the learned fence — the half that was missing. Admin apply/clear endpoint added. Self-review found a reader-coupling defect (DR-42) and a re-survey gap (DR-43); the independent review failed 4/4. D14 opened | Local + Production | 25/25 pure-logic, 39/39 integration |
| 2026-10-11 | **`deploy-baseline` tag pushed** — annotated tag pinning `15dc187` on GitHub, so the deployment-only state stays downloadable regardless of where `main` moves. `main` not moved; no commits added. A local clone of that state also sits outside the working tree | Production (GitHub) | `git ls-remote origin` shows `refs/tags/deploy-baseline`; clone verified to contain zero Stage 1–5 symbols |
| 2026-10-11 | Schema upgrade hardening — DDL is now one statement per transaction with a rollback, tables are independent, and `get_open_write_status()`'s probe SELECT rolls back too (DR-44). The upgrade path is exercised locally for the first time: the deploy-baseline clone builds the live schema and the current app upgrades that same file. D12's column count corrected 9→16 | Local + Production | 26/26 schema upgrade; 158/158 across all six suites |
| 2026-10-12 | Blind-spot sweep — DR-44 was found by accident, so the *class* was enumerated deliberately: seven categories of "differs between test and production, or never exercised". Six came back clean or benign; the seventh was real — the check-then-insert race on both attendance endpoints returned a 500 to a student whose attendance had just been recorded (DR-45). Fixed to the 409 the endpoint already decided on, with a forced-race test. The sweep also records what it cannot cover: Postgres itself, real concurrency, and five endpoint families with no coverage at all | Local + Production | 12/12 race test (teeth: 500 before, 409 after); 170/170 across all seven suites |
| 2026-10-14 02:45 | **PRODUCTION OUTAGE — every route 500.** `ModuleNotFoundError: No module named 'psycopg'` raised from `db = SQLAlchemy(app)` at app.py module scope, so not one route was ever registered. **No application change caused this** — see below. It surfaced right after a Stage 1–5 push, which made the push look responsible | Production | Vercel runtime log, `dpl_7dCNkhv4…`, then `dpl_GdZ3u5YU…` after the revert |
| 2026-10-14 03:40 | `main` reverted to `15dc187` on the hypothesis that the new code broke the deploy. **The hypothesis was wrong and the revert did not help** — the baseline failed with the identical traceback. Reversible and the work was preserved, but unnecessary: it cost the project record and every deployed feature for nothing, and it was pushed while being described as the fix. Work kept on `stage-1-5-work`; `deploy-baseline` untouched | Production | `38199b3`; both trees hash to `f6da0b03…` |
| 2026-10-15 | **Boot fix (push 1 of 2).** Root cause: SQLAlchemy was never pinned, so every build resolved whatever was newest, and a bare `postgresql://` URL names no driver. One build routed it to psycopg2 (installed — worked three weeks); a later build routed it to psycopg3 (never installed). Three independent fixes: `SQLAlchemy==2.0.48` pinned in both requirements files; `config._database_url()` normalises every Postgres URL to `postgresql+psycopg2://`; the import-time bootstrap no longer kills module import and `/healthz` reports why. D7's version marker removed. **D6 closed.** Stage 1–5 held back so the boot path is proven against real Vercel on known-good code first | Production | `/login` renders with a dead database, measured; `/healthz` 503 naming all three failed steps |
| 2026-10-21 | **Push 2 — Stage 1–5 merged onto the proven boot path.** `main` was already serving (`/healthz` 200 `{"ok":true}`, `/login` 200, `/` 302) from push 1, so the boot fix was already live when the stages joined it. The boot fix was moved across by **cherry-pick, not merge** — `git merge main` into `stage-1-5-work` produces a tree containing the boot fix and *zero* Stage 1–5 code, because `main` carries the revert `38199b3` and git treats that removal as the latest intent. That merge was run, inspected, and aborted before anything was pushed (DR-49). Two further defects surfaced only on this tree: the `SECRET_KEY` guard accepted the key this repository publishes (DR-51), and both client suites silently lost their pre-fix contrasts under `core.autocrlf=true` (DR-50) | Production | `0dd5d46`; 182/182 across all eight suites |

### Stage blocks

Each stage opens a block when work starts. `Exit` names the numbered
Verification item from the plan; nothing closes on opinion.

```
### Stage N — <name>
Status   : PLANNED | IN PROGRESS | SHIPPED | LIVE | BLOCKED | DROPPED
Scope    : Local | Production | both
Entry    : which stages must be LIVE first
Exit     : Verification item #N passes
Evidence : commit(s), test output, observation
Closed   : <date, or blank>
```

#### Stage 0 — The project record

```
Status   : SHIPPED
Scope    : —
Entry    : none
Exit     : Verification item #10 — every file:line reference resolves,
           every commit hash exists, README/config drift reconciled
Evidence : this file; 13/13 reference checks pass; README corrected from
           MAX_GPS_ACCURACY_METRES = 30 to 500 and the 550m defect noted
Closed   : 2026-09-23
```

#### Stage 1 — Capture a trustworthy fix

```
Status   : SHIPPED
Scope    : both
Entry    : none
Exit     : Verification items #2 (centroid robustness), #6 (backward compat)
Evidence : 17/17 end-to-end checks through the real Flask endpoint, both
           payload shapes. #2: nine tight fixes + one 400m outlier -> median
           centroid 0.71m from truth, arithmetic mean 40.43m. #6: legacy flat
           lat/lng/accuracy with no samples and no qr_token still succeeds,
           stores sample_count=1, spread_m=0, status PRESENT unchanged.
           #7: ensure_schema_compatibility() run twice, idempotent, status
           column untouched. A device reporting +/-5m while its own fixes
           scattered 20m now yields acc_eff = 20m. A payload whose first fix
           claimed 300m accuracy 100m off-centre stored acc_eff 8.0m at
           distance 0.4m -- the coarse first fix no longer sets the position
           or the uncertainty.
Closed   : 2026-09-25
```

Client capture is an ~8s `watchPosition` (max 20 fixes) that keeps every
sample and exits early once the last five are all within 20m. A late
geolocation error is non-fatal if fixes already arrived; zero fixes falls
back to the single-fix path rather than failing closed.

**Harness note, recorded because it will recur.** An end-to-end test against
`sqlite:///:memory:` initially failed with *"Venue GPS not configured"*. Cause:
`get_open_write_status()` calls `inspect(db.engine).get_check_constraints(...)`,
and on in-memory SQLite one connection is shared, so the inspector's rollback
discarded an uncommitted venue UPDATE. A harness artifact, not an application
defect — no app code was changed for it. Call `get_open_write_status()` before
writing fixtures, not between a write and its commit.

#### Stage 2 — Three-state decision

```
Status   : SHIPPED
Scope    : both
Entry    : Stage 1 LIVE (else false rejects spike)
Exit     : Verification items #1 (decision rule), #3 (precision trap)
Evidence : 17/17 unit checks, 23/23 end-to-end through the real endpoint.
           #1: the rule table holds -- (10,5,50) INSIDE, (48,20,50)
           AMBIGUOUS, (200,10,50) OUTSIDE; confidence is exactly 1.0 at
           d = r - a and 0.0 at d = r + a, clamps at both ends, and is
           monotonic in distance.
           #3: ten samples agreeing within 3m but sitting 120m out do not
           reach INSIDE and their confidence is clamped. The load-bearing
           pair is (d=10, a=3, r=50): uncalibrated it gives geo_state
           INSIDE but verify_state AMBIGUOUS at 0.7, while the identical
           numbers at a calibrated venue give INSIDE at 1.0. The cap is
           doing real work rather than passing vacuously.
           #2 re-run with the outlier mid-window and plausible accuracy so
           the 3x-median filter could not trivially drop it: median centroid
           2.23m from truth, arithmetic mean 40.46m.
           End-to-end: legacy flat payload still 201; status still PRESENT;
           verdict persisted; a 120m tight cluster rejected OUTSIDE with no
           row stored; a 300m-accuracy fix refused at max_acc_m 100.0; new
           columns nullable with status still VARCHAR(10); lecturer view
           reports needs_review = 1 with the verdict and confidence visible.
Closed   : 2026-09-26
```

Three columns changed meaning at this stage and it is worth being exact about
which. `status` did **not** change — it is still `"PRESENT"` on every accepted
record, including ambiguous ones. The verification outcome lives in the new
`verify_state` and `confidence` columns. Anything reading `status` is
unaffected; anything wanting the new judgement must ask for it by name.

**`OUTSIDE` is the only rejecting state.** `AMBIGUOUS` is an accepted
submission carrying its own doubt, surfaced to the lecturer as
`needs_review`. This is deliberate: the stage tightens what the system
*claims*, not primarily what it *accepts*, and the reject boundary
`d - a > r` is algebraically identical to the old `d > r + a`. The real
tightening is in step 6 — capping accuracy at `2 x radius` rather than a flat
500m — which is what closes D3.

#### Stage 3 — Building, floor, and venue collision

```
Status   : SHIPPED
Scope    : both
Entry    : none
Exit     : Verification items #4 (overlap/stacked), #7 (schema idempotency)
Evidence : 17/17 collision + 16/16 contest + 20/20 end-to-end + 10/10
           downgrade checks.
           #4: the plan's two literal cases hold -- two venues 30m apart at
           r=50 overlap by 70.03m, and two venues at identical coordinates
           on floors 2 and 3 are stacked. Around them: disjoint pairs report
           nothing, asymmetric radii are handled, a venue with no GPS is
           skipped, same-floor and different-building pairs are not stacked,
           40m apart in one building is not stacked, null building and null
           floor never match, building names compare case- and
           space-insensitively, and a pair that is both overlapping and
           stacked is reported once -- as stacked, the worse diagnosis.
           Contest: ranking direction pinned explicitly (smaller d/r wins),
           a stacked peer contests on a numerically worse margin, ties go to
           the challenger, a peer that does not contain the fix cannot
           contest, the configured default radius is used when a peer has
           none, the strongest of several peers is the one returned, and the
           result does not depend on query order.
           #7: ensure_schema_compatibility() run twice against the live
           schema -- rows 2 -> 2, building and floor present, and status
           still VARCHAR(10). No CHECK constraint refused a write.
           End-to-end: columns present after create_all; an isolated venue
           is uncontested; a stacked venue is contested with reason
           "stacked"; the record persists verify_state AMBIGUOUS while
           status stays PRESENT; the lecturer view reports needs_review = 1
           with venue_label "Rm 202 · Faculty Block · floor 2"; set-gps
           returns one stacked conflict with a readable warning and persists
           floor = 3.
           Downgrade: the end-to-end run could not reach the downgrade line,
           because Stage 2 caps every uncalibrated venue to AMBIGUOUS before
           the contest runs and Stage 5's cal_* columns do not exist yet.
           Proved separately with is_calibrated forced true --
           calibrated + isolated: verify INSIDE, confidence 1.0, contested
           None; calibrated + stacked: geo INSIDE, verify AMBIGUOUS,
           confidence 1.0, contested by "Rm 302" reason "stacked".
Closed   : 2026-09-26
```

**Floor is never used in distance maths, and that is the whole design.** §1.3
of the plan closed every geometric avenue: GNSS vertical error runs 15–50m
against a 3–4m floor spacing and is usually null indoors, browsers expose no
barometer, Web Bluetooth cannot passively scan and is absent on iOS Safari,
and H3 is strictly 2D. So the two new columns are not an attempt to measure a
floor — they are a *declaration* of one, and they exist for three jobs only:
detect venues stacked above one another, label reports, and warn the admin at
setup.

**Overlapping and stacked are different problems.** Overlap is a design-time
error and fixable — shrink one of the radii and it goes away. Stacking is not
fixable at any radius, because the two rooms genuinely occupy the same ground
footprint; the only resolution is to stop asking GPS and escalate to the QR
token, which is what Stage 4 does with the `stacked` flag this stage produces.
The code keeps them apart: `venues_overlap` stays purely geometric, and the
significance threshold that suppresses sub-metre rounding artifacts lives in
the *reporting* function, not the predicate.

**The contest can only downgrade.** `contested_by` never upgrades a state and
never rejects a submission. A student standing in a real place with a real fix
does not become absent because the campus has two rooms close together — that
is the institution's mapping problem, not theirs. The consequence is visible
in the downgrade evidence: confidence stays at 1.0 on a contested record,
because the geofence confidence is genuinely unchanged. The doubt is about
*which venue*, not about the distance.

Confirmed in the same pass that Stages 1 and 2 still hold: legacy flat payload
still 201, D3's 300m fix still refused at `max_acc_m` 100.0, a 300m-out fix
still rejected with no row stored, a 48m/20m fix still accepted as
`AMBIGUOUS` at 0.551, and auto-close still fires at 2 of 2 present. The first
attempt at that run reported a spurious D3 failure; the cause was the test
fixture enrolling a single student, so the first submission auto-closed the
session and the second was refused at step 1 before the accuracy check ran.
Worth recording because the failure looked exactly like a regression in the
thing the log had just cited as shipped evidence.

#### Stage 4 — Adaptive QR gating

```
Status   : SHIPPED
Scope    : both
Entry    : Stages 2 and 3 LIVE
Exit     : Verification item #9 (adaptive gate)
Evidence : 11/11 gate + 16/16 override & window + 6/6 concurrent-overlap
           + 25/25 manual override + 15/15 regression, then a self-review
           that found 8 defects in this stage's own code, all remediated
           and re-verified -- see "Stage 4 remediation" below.
           #9 holds in the plan's own words. A confidently-inside fix at a
           calibrated, isolated venue is accepted with no token at all
           (201, INSIDE, confidence 1.0). The same fix is refused 403 at an
           uncalibrated venue (reasons: venue_uncalibrated, ambiguous_fix),
           at a stacked venue (stacked_venue, contested_by_nearby_venue,
           ambiguous_fix), when a second live session overlaps
           (concurrent_overlapping_session), and when the fix is coarse at
           30m against a 50m fence (coarse_fix). Each of those is accepted
           once a valid token is supplied.
           The overlap predicate was checked at unit *and* HTTP level, and
           correctly ignores a peer session that is nominally OPEN but
           already expired.
           D9: a fix 200m outside an uncalibrated 50m fence is rejected 400
           without a token and accepted 201 with one, as AMBIGUOUS and never
           INSIDE, stored with qr_verified = True so it can feed Stage 5.
           The same fix 900m out is still refused -- the 500m sanity bound
           holds -- and at a calibrated venue the token does not override at
           all.
           D2: window 0 and -1 accepted, -2 and -3 rejected, and a token
           minted for a different session is rejected.
           Manual override: other lecturers 403, students 403, anonymous
           401, a missing reason 400, a blank reason 400, a non-enrolled
           student 403, an unknown student 404 -- and nothing is stored on
           any refused path. A valid mark stores verify_state MANUAL,
           status PRESENT, qr_verified False, sample_count 0, confidence
           NULL, plus one audit row naming the lecturer and keeping the
           reason. Duplicates are blocked in both directions.
           #6: the legacy flat payload still returns 201 INSIDE at a
           calibrated isolated venue, and D3's 300m fix is still refused at
           max_acc_m 100.0.
           #7: schema pass run twice -- rows 1 -> 1, qr_verified BOOLEAN and
           nullable, status still VARCHAR(10), venue columns intact, and a
           write still succeeds afterwards.
Closed   : 2026-09-30
Note     : shipping this before Stage 5 means QR required everywhere —
           safe and stricter than today, but expect complaints
```

**The stage is mostly wiring, and that is the point.** Both halves already
existed: a rotating HMAC token and a geofence. What was missing was any rule
about *when* to demand the second one. `if qr_token:` meant the field could
simply be omitted, so the anti-relay system was opt-in by the party it
constrains — it protected exactly the students honest enough to use it.

**Enforcement had to move.** Token validation sits at step 2b, but whether a
token is *required* depends on the verdict and the venue contest, neither of
which exists until steps 7 and 8. So validation and enforcement are now two
different places: a bad token is still rejected immediately whether or not one
was needed, because silently ignoring a failed scan would tell a student who
did scan that their scan did not matter.

**The manual override was not in the plan as a deliverable — it was in the plan
as a question.** Stage 4's risk note said a student with a broken camera at a
stacked venue would have no path, and to *check whether a lecturer override
exists before shipping*. It did not: `AttendanceRecord(` appeared in exactly
one place in the file, the student's own submission. So mandatory scanning
would have converted a dead battery into an absence, and the override became
part of the stage rather than a follow-up.

That endpoint is the only place in the system that writes a record from no
measurement at all, so it carries three markers rather than one:
`verify_state = MANUAL`, `qr_verified = False`, and `sample_count = 0`. The
stored coordinates are the venue's own centre, because `lat`/`lng` are
`NOT NULL` and SQLite cannot drop that — which is the same shape as the
`demo_coords` fallback D5 flags as a latent backdoor. It is safe only because
of those markers, so the constraint this places on later work is explicit:
**anything filtering these rows must filter on `verify_state`, never on
coordinates.** Stage 6's cluster detector in particular must exclude `MANUAL`
rows, since they all share one point by construction and would otherwise look
like perfect collusion.

**What the bootstrap now costs.** `is_calibrated()` still returns `False` for
every venue, so in practice a scan is currently required everywhere. That is
stricter than the system has ever been and it is the intended direction — the
scans are what produce the verified check-ins Stage 5 learns from — but it is
the change most likely to generate complaints before Stage 5 lands, and it is
worth saying so plainly rather than discovering it in the field.

##### Stage 4 remediation — 2026-09-30

The stage passed every test written for it and was then reviewed against its
own code rather than its own tests. That found **eight defects, six of them in
code this stage had just added.** All are fixed and re-verified; the point of
recording them at this length is that the tests passing was not evidence of
much, because the tests were written by whoever wrote the bug.

| # | Defect | Fix | Verified by |
|---|---|---|---|
| F1 | The step-7b override bound was a flat 500m. Against the default 50m fence that is a **10× widening** — structurally the same 550m effective fence D3 exists to remove, reintroduced by the mechanism meant to rescue it | `QR_GROUND_TRUTH_RADIUS_FACTOR = 3.0`; the bound is now `min(500, 3r)` via `qr_ground_truth_limit()` | At r=50 the limit is 150m: d=149 → 201, d=151/300/499 → 400. The flat bound accepted all four |
| F2 | **Two acceptance paths, only one of which consulted a bound.** Step 7b checked 500m; the ordinary `AMBIGUOUS` band `d ≤ r + a` reaches `r + min(2r, 500)` and never looked at the 500m bound at all. Measured reach: r=200 → 600m, r=250 → 750m, r=400 → 900m. A bound one of two paths ignores is not a bound | `qr_is_calibration_grade()` reads the same `qr_ground_truth_limit()`, so both paths are governed by one function | r=200/d=580, r=250/d=700, r=300/d=780 all still store (201) but with `qr_trusted=False`; r=50/d=140 → `True` |
| F3 | **Override rows would have poisoned Stage 5.** A row admitted by 7b is by definition a fix the geofence calls provably outside. Feeding it to calibration is the fence rewriting itself from the evidence of its own failures — and worse, the promotion that poisoning earns sets `cal_*`, which flips `is_calibrated()` to `True`, which switches the override off. The loop would have closed behind itself | `qr_is_calibration_grade()` excludes override rows at **any** distance | An override row stores `qr_verified=True, qr_trusted=False`. §1.6's bootstrap is unaffected — verified below |
| F4 | An `AMBIGUOUS` row meant two unrelated things and the lecturer could not tell which: a fix at the room's edge, or one the geofence rejected outright that a token overruled. `distance_m` did not disambiguate, because the payload carried no radius to compare it against | New `geo_state` column preserves the geometric verdict; the lecturer payload gained `radius_m`, `qr_override`, `qr_trusted` | `geo_state=OUTSIDE, verify_state=AMBIGUOUS` identifies an override; payload returns `{"distance_m":120.0,"radius_m":50.0,"qr_override":true,"qr_trusted":false}` |
| F5 | **NaN switched off the entire geofence.** Every IEEE 754 comparison with NaN is `False`, so one unvalidated float disabled *both* fence inequalities and the accuracy cap simultaneously — `d + a <= r` false, `d - a > r` false, so `AMBIGUOUS`, from anywhere on earth | `_clean()` rejects NaN and infinity on both the `samples[]` and flat `lat`/`lng` paths | 11 probes (`NaN`, `-NaN`, `nan`, `inf`, `Infinity`, `-Infinity`, `1e400`, float inf, via `lat`, via `lng`, via `samples[]`) → all 400, nothing stored |
| F6 | `hmac.compare_digest` raises `TypeError` on a non-`str` and on any `str` containing non-ASCII, so `{"qr_token": {}}` or `"cafécafé"` returned **500 on the busiest endpoint in the system** | `isinstance(token, str) and token.isascii()` guard | 10 payload types (dict, non-ASCII, list, int, None, empty, NULs, bool, CJK, float) → all 403, zero 500s |
| F7 | The placeholder `SECRET_KEY` was still accepted in production. Stage 4 changed what that key *means*: it signs the session cookie **and** the QR token, so a known key computes valid tokens offline without ever being in the room. Acceptable before this stage, not after — the token went from decoration to the thing that admits a student | `_resolve_secret_key()` fails fast when `VERCEL=1` and the key is the placeholder or blank | 5 environment cases in subprocesses: local+placeholder loads; Vercel with placeholder / unset / blank all refuse; Vercel with a real key loads |
| F8 | The `OUTSIDE` rejection returned `venue_coords` — **the fence's own centre, to anyone, with no token required.** Submitting it back verbatim drives `d` to 0 from anywhere, which makes every distance-based bound in this file decorative | `venue_coords` removed; `distance_m` and `radius_m` retained | Absent from the body at bearing 37°. **Not closed, downgraded** — see D10 |

Two further defects were found in the *read* paths after the six above, in code
that no test had been pointed at because the tests all exercised the write path:

| # | Defect | Fix | Verified by |
|---|---|---|---|
| F9 | `manual_mark` wrote `qr_verified=False` with a comment claiming that kept the row out of calibration — but Stage 5 reads `qr_trusted`, which this writer left **NULL**. The guard was expressed on the wrong column. And this is the one row whose coordinates *are* the surveyed centre, so feeding it back would drag the learned centre towards the very offset §1.6 exists to correct: the more a lecturer manually marks a badly-surveyed venue, the harder that survey becomes to fix | `qr_trusted=False` written explicitly, `geo_state=None` made explicit | Three filter shapes a Stage 5 author might plausibly write — `.is_(True)`, `!= False`, `.isnot(False)` — now select 0 manual rows each. Before the fix the latter two selected it |
| F10 | The lecturer payload derived `qr_override` as `geo_state == OUTSIDE and ...`, which reads **False** on any row written before `geo_state` existed. Such a row may well have been an override under the old flat-500m rule, and there is no way to tell from the row — so the payload asserted a fact it did not have, which is the confident-but-unfounded claim this whole stage exists to remove | `None` when `geo_state` is NULL and the row is not `MANUAL`; `MANUAL` stays `False` because `manual` already explains it | Legacy row → `qr_override: null`; genuine override → `true`; manual row → `false` |

**The asymmetry between F9 and F10 is deliberate.** An unknown override is
reported as unknown; an unknown calibration eligibility is reported as
not-eligible. `bool(NULL)` is `False` in both cases, but only one of those is
the safe reading, and Stage 5 must act on `NULL` exactly as it acts on `False`.

**The bootstrap check that mattered most.** Excluding override rows (F3) could
have starved §1.6 of the very data it needs. It does not, and the reason is
worth stating because it is not obvious: the survey errors §1.6 describes are
tens of metres, comparable to `a_eff`, so `d − a > r` never fires and those
rows arrive as `AMBIGUOUS` with a valid token — calibration-grade, override
untouched. Measured: a 120m survey offset with `a=90` → 201, `geo_state=AMBIGUOUS`,
`qr_trusted=True`. Only rows the geofence calls *provably* outside are excluded.

**One boundary worth knowing about before Stage 5.** At `r=50, d=150, a=100` a
row stores `qr_trusted=True` with `confidence ≈ 5.6e-14`. Both are correct:
150 *is* 3r, and confidence is exactly 0 at `d = r + a`. But it means
calibration-grade and zero-confidence coexist, and the furthest such a row can
sit at a default venue is 150m — which equals Stage 5's own displacement cap
exactly. Stage 5 must therefore treat that cap as load-bearing, not as a
belt-and-braces check. Recorded as DR-30.

**What did not happen: an independent review.** The remediation was reviewed by
the same party that wrote it. A six-lens adversarial multi-agent review was
attempted on 2026-09-30 and **did not run** — all seven agents failed with
HTTP 402 (budget pool exhausted), zero completions, no `journal.jsonl` written.
The workflow returned `{"raised": 0}`, which is an artefact of total failure and
must not be read as a clean result. What stands in its place is executable: 47
adversarial probes across five attack families (widening the bound from the
request, reaching `qr_trusted` illegitimately, availability, token forgery, and
the `SECRET_KEY` fail-fast), all repelled. That is evidence, but it is evidence
gathered by the author, and the distinction is the reason this paragraph exists
rather than a claim that the review cleared.

**Correction, 2026-10-06.** The sentence above ("a six-lens review … did not
run … no `journal.jsonl` written") was true of *one* attempt and wrong as a
general claim, and D11 repeated the error. The workflow transcripts on disk show
what actually happened across several attempts, and it is more mixed than "it
failed":

- `wf_422082f3-407` — the **Stage-4 server** review. Four of its finder lenses
  completed and raised **18 findings** across five attack families
  (gate-bypass, override-abuse, manual-endpoint, state-ordering,
  data-integrity). What did *not* complete was the refutation pass: of the
  refute agents, one returned null verdicts and the rest failed, so **not one of
  the 18 was adversarially verified.** The findings are real enough — they
  substantially *are* DR-26 through DR-34, which remediated them — but they were
  triaged by the author, not by an independent refuter. So the honest shape of
  D11 is not "the review never ran"; it is "the review *raised* but was never
  *refuted*."
- `wf_01f9b7ed-bde` and `wf_c027cf96-6ff` — no completions (16 and 20 agents,
  zero results). These are the attempts D11's original wording described.

The through-line survives intact: no finding touching Stage 4 has been checked
by anyone but the code's author. What changes is the reason — the refuters, not
the finders, are what the budget pool could not pay for.


#### Client-layer review and remediation — findings F1–F6

```
Status   : SHIPPED
Scope    : both (F1–F4, F6 client; F5 server)
Entry    : Stages 1–4 SHIPPED
Exit     : three executable suites green, each with a pre-fix contrast that
           reproduces the defect it claims to fix
Evidence : docs/verification/ — 24 + 29 + 15 = 68 checks, all pass
Closed   : 2026-10-06
```

Stages 1–4 reworked the *server's* reasoning. None of it touched the student
page that feeds that reasoning, and a six-lens review of the client
(`wf_2de7edaf-ec5`) found that the capture rewrite Stage 1 called for had
introduced defects of its own, plus two the page carried from the start. One
finder lens completed and returned **six findings**; the other 24 agents failed
HTTP 402, and — as with the server review above — **no refutation pass ran.**
All six were therefore confirmed the only way left: by reading each against the
source it names. That is the same author-only standard D11 records, and it is
named here for the same reason.

The 8-second capture is the hinge. It is what makes an honest `acc_eff`
possible (§1.1), and it is also what turned three latent page bugs into
reliable ones — an operation that now takes eight seconds straddles a 30-second
poll about a quarter of the time, and spans the network-to-GNSS handover it
used to resolve before.

| F | Severity | Defect | Fix | Proof |
|---|---|---|---|---|
| F1 | high | The 8s collection timer started at `watchPosition` registration, so it ran *during* the permission prompt and pre-empted the 15s geolocation timeout. The first-ever tap — the one that raises the dialog — routinely expired with zero samples, called `clearWatch` (so a grant landing a second later had nowhere to go), and rejected with the generic message. The app "always failed the first time" | The collection window now starts on the browser's **first callback**, not at registration; a separate 45s permission timeout covers the unanswered-prompt case with its own message | `student_dashboard_gps_test.js` S1, S4; pre-fix P1 rejects a *granted* capture at 8000ms |
| F2 | high | A single transient `POSITION_UNAVAILABLE` after the first fix ended the capture. The routine Android sequence "coarse fix at 0.3s, GNSS-acquiring blip at 1.0s" collapsed Stage 1 to a **one-sample** submission built from the coarse network fix `robust_fix` exists to discard | Denial (code 1) is permanent and still ends the capture; every other error is transient and no longer does. The window closes only on its timer, the sample cap, or genuine settling | S2 keeps collecting past the blip; pre-fix P2 submits the lone 180m fix |
| F3 | high | The unguarded 30s poll rebuilt the grid with `grid.innerHTML = …`, detaching the button `markAttendance` holds across the capture and the `<video>` the scanner holds. A poll mid-capture left the student tapping an enabled *replacement* button → two POSTs, one 201 and one 409, so a recorded attendance showed as a failure. A poll mid-scan orphaned a live `MediaStream` | A render guard defers the rebuild while a capture or camera is live and resumes it after; a re-entrancy guard rejects the second tap regardless | `student_dashboard_poll_test.js` T1, T3; pre-fix P1 detaches the button and fires two POSTs, P3 orphans the camera |
| F4 | medium | `buildCard` hardcoded `Acquiring GPS…`; the only code that wrote anything else ran twice in the page's life. Thirty seconds after a blocked student was shown "Location permission denied — enable it for this site", the poll replaced it — permanently — with "Acquiring GPS…", reading as "the app is slow" rather than "change this setting" | The indicator is page state, and `buildCard` renders *from* that state, so it is idempotent under any number of re-renders; a `permissions` change listener keeps it live | T2 survives the poll; pre-fix P2 reverts to the hardcoded label. The real pre-fix bug is worse than reported — the denial never rendered at all |
| F5 | medium | Both lecturer read paths served the device's **self-reported** accuracy under `accuracy_m`, while the student's own response serves `acc_eff` under that same key. The one person asked to *review* an `AMBIGUOUS` row saw the single quantity `robust_fix` and `geofence_decision` exist to refuse — a confident "±12m" beside a row flagged on 80m of scatter, which reads as obviously-not-ambiguous and gets dismissed | New `reported_uncertainty()` serves `acc_eff` as `accuracy_m` at both paths, with the device figure in a separate field only when it differs | `app_reported_uncertainty_test.py` — teeth line: pre-fix served 12.0, post-fix serves 80.0 |
| F6 | low | The early-settle rule exited on each fix's self-reported accuracy alone, and `GPS_GOOD_ACC_M = 20` sits below the server's 25m coarse-fix trigger at the default radius — so an early-settled capture could never raise that reason. Latent today (every venue is uncalibrated), it becomes live the moment Stage 5 calibrates a 50m venue | Early exit now also requires positional agreement between the recent fixes, not just their self-reports; a frozen WiFi replay no longer satisfies it | S5 runs the full window on identical fixes; S6 confirms a genuinely good signal still exits early |

The suites are built to have teeth. Each lifts the **real** function out of its
file — the client ones by marker-extraction from the template and execution
against a virtual clock and a scriptable geolocation/DOM; the server one by
`ast` extraction of `reported_uncertainty` — and runs every scenario a second
time against a pre-fix variant, asserting the old code fails on the same input.
A green suite whose pre-fix twin also passed would prove nothing, so the pre-fix
contrast is itself checked (`TEETH` assertions). Two mistakes made while writing
them are recorded in Operating notes, because both are the kind that pass
vacuously.

**What did not happen here either: independent verification.** Same budget-pool
exhaustion, same consequence — six findings and three suites, all authored and
all confirmed by the author. These are client-layer and mechanical where Stage
4's were security-sensitive, so the exposure is smaller, but it is the same
exposure. D11 covers this pass too.


#### Stage 5 — Learn each venue's fence from verified check-ins

```
Status   : SHIPPED
Scope    : both
Entry    : Stage 1 SHIPPED; Stage 4 SHIPPED (enforces QR at uncalibrated venues)
Exit     : Verification item #5 (thresholds and poisoning guards)
Evidence : docs/verification/app_calibration_test.py — 25/25 (pure logic,
           incl. decay-edge teeth); stage5_integration_test.py — 39/39 (schema
           idempotency, population filter, promotion, fence geometry move,
           reader coupling, re-survey clearing, displacement brake, admin
           apply/clear). Independent review attempted and FAILED — see below.
Closed   : 2026-10-07
```

**What shipped.** The §1.6 inversion, and the only mechanism here that corrects
a systematic WiFi-fingerprint offset rather than averaging out noise:

- **Schema** — `Venue` gains `cal_lat`, `cal_lng`, `cal_radius`, `cal_n`,
  `cal_updated_at` (TIMESTAMP, portable to Postgres per D12) and `cal_review`
  (JSON text), all nullable, added by the `ensure_schema_compatibility()` ALTER
  pattern (DR-4). `cal_review` holds a candidate that met the thresholds but
  tripped a brake, for an admin to apply or discard.
- **Input population** — only `AttendanceRecord.qr_trusted.is_(True)`. `.is_(True)`
  exactly: never `== True`, never a bare truthiness test. `NULL` means
  not-eligible (DR-31), and only `.is_(True)` excludes it. This is the single
  line that decides whether the fence can be poisoned, and it is the one the
  test asserts against 300m-off NULL/False rows.
- **Centre and radius** — component-wise **median** centre (the same robust
  estimator `robust_fix` uses) and the **90th percentile** of per-record
  distances plus a 10m buffer. Never mean, never max: one student who scanned at
  the window must not set the fence for the room, and a handful of outliers must
  not drag the centre.
- **Promotion thresholds** — ≥30 records, ≥15 students, ≥3 sessions, ≥2 days.
  One session's atmospheric conditions must not become the permanent fence, so
  all four axes must clear at once. `calibration_decision()` checks breadth
  first, so a displacement computed from nine records never reaches an admin.
- **Poisoning brakes** — the learned centre must sit within
  `CAL_MAX_DISPLACEMENT_M` = 150m of the survey, and the learned radius must not
  exceed `CAL_MAX_RADIUS_EXPANSION_FACTOR` = 2× the survey. Tripping either
  writes `cal_review` and **never moves the fence**. The 150m cap is
  load-bearing (DR-30): at the default 50m venue the furthest a calibration-grade
  row may sit is `qr_ground_truth_limit` = 3×50 = 150m, exactly the cap, so a
  single admitted row can reach it.
- **The fence actually moves** — new `effective_fence_centre()` returns the
  learned centre at a calibrated venue, the survey otherwise, and
  `effective_fence_radius()` returns the learned radius. Both are read together
  at the geofence step: a learned radius measured against a surveyed centre
  would mean nothing. This is the half that was missing — `is_calibrated()`
  already gated confidence and the QR override, but every distance was still
  taken from the survey, so a venue whose survey sat 60m east stayed 60m east
  however many check-ins disagreed.
- **Triggers** — `AttendanceSession._on_closed()` is called at all three
  session-close sites (lazy expiry, the open-session cleanup on a new session,
  and the 100% auto-close). Idempotent: it reads the whole eligible population
  every time. It never raises into its caller — any failure rolls back only its
  own work and leaves committed attendance untouched.
- **Admin control** — `POST /api/admin/locations/<id>/calibration` with
  `action: apply | clear`. Apply promotes a flagged candidate (the deliberate
  human act the brake exists to force); clear wipes the learned fence and any
  candidate, returning the venue to its survey — the escape hatch the plan's
  risk note asks for. Both are audited. The locations list now reports
  `calibrated`, the learned geometry, and the survey delta in metres — the
  single number that says how wrong Google's map of the spot is.

**Known consequence, accepted.** A calibrated venue's radius is the 90th
percentile of observed reach, which is generally *larger* than a tight survey,
and a learned centre can sit tens of metres off the survey by design. That is
the point — the fence moves to where students actually report from — but it
means a venue that calibrates will accept some fixes its survey rejected, and
reject some it accepted. The 2× radius cap bounds the widening; the 150m
displacement cap bounds the move; the admin `clear` undoes both.

**What has NOT happened: independent verification.** A four-lens adversarial
review was launched against this stage (`wf_d89a1d51-13d`) and **did not run**:
all four finder agents failed — three with `HTTP 402 Budget pool quota has been
exhausted`, one with a DNS error — after ~2.3 hours and 649k subagent tokens,
leaving `journal.jsonl` with four `failed` lines and **zero** `result` lines.
The workflow returned `{"raised": 0}`, which is an artefact of total failure and
must not be read as a clean review. That is the identical trap D11's original
wording fell into, recorded here deliberately so the next reader does not fall
into it a third time. The 64 Stage-5 checks are author-written; D11 now covers
this stage too.

**A real defect the author's own review caught, while the workflow was failing.**
Worth recording because it is the class of bug this stage was most likely to
introduce, and because no test would have caught it — every suite was green
before and after.

The geofence now measures from `effective_fence_centre()`, but three *other*
verification-time readers still measured the same venue from its **surveyed**
geometry:

- `contested_by()` computed `own_margin` from `venue.latitude/longitude` and the
  surveyed radius, so at a calibrated venue the contest judged a different
  circle than the verdict it was downgrading;
- `venue_radius()` — used by the contest, the coarse-fix gate and the overlap
  test — ignored `cal_radius` entirely;
- `venues_overlap()` likewise, so `overlaps_another_open_session()` asked whether
  two *surveyed* circles collide when the question is whether the fences **in
  force** do.

The fix draws the line explicitly: **verification-time readers use the effective
fence; design-time diagnostics and physical claims use the survey.**
`venue_radius()` is now calibration-aware, `contested_by()` reads both its own
and each peer's effective centre, and `venues_overlap()` takes an `effective`
flag that `venue_conflicts()` (the set-gps diagnostic) passes `False` — so the
admin's walked-perimeter warning still speaks about walked geometry. A new
`_surveyed_radius()` is the deliberate way to ask for the survey, and the
calibration brakes use it so recalibration cannot ratchet (each learned radius
becoming the baseline the next is allowed to double). Recorded as DR-42.

The same pass found a second gap: a **re-survey** through `set-gps` left the old
learned fence in place, even though the learned geometry is defined *relative to
the survey* — the displacement cap measures against it. A venue could therefore
carry a calibration whose own provenance no longer held, and the next recompute
would measure displacement from the wrong baseline. `set-gps` now clears `cal_*`
and `cal_review`, and records `cleared_calibration` in the audit entry so the
loss is visible rather than looking like a venue that mysteriously went back to
demanding QR.

#### Schema upgrade hardening — the push-to-Vercel path

```
Status   : SHIPPED
Scope    : both
Entry    : Stages 1–5 SHIPPED
Exit     : the upgrade path runs end-to-end from the real deployed schema
Evidence : docs/verification/schema_upgrade_test.py — 26/26
Closed   : 2026-10-11
```

Prompted by a plain question — *would committing this work seam with Vercel?* —
and the answer turned out to be "probably, and the failure mode is total."

**What the five stages never tested.** Every suite ran against a *fresh*
database, where `db.create_all()` builds every table from the current models and
`ensure_schema_compatibility()` therefore executes **zero** ALTERs. Production is
the opposite case: Neon already holds the schema as deployed at `15dc187`,
`create_all()` is a no-op on existing tables, and the first cold start after a
push has to ALTER in sixteen columns. That path had never run, anywhere.

**The defect (DR-44).** Postgres aborts the whole transaction on a failed
statement; every later statement then raises `InFailedSqlTransaction` until
someone rolls back. The function had **seven** `except Exception: pass` handlers,
**zero** rollbacks, and a first `ALTER` with no `try` at all. So a single DDL
hiccup would poison the session, silently fail the other fifteen columns, and
then throw inside `seed_demo_if_empty()` — which runs at *import*, two lines
later:

```python
with app.app_context():
    db.create_all()
    ensure_schema_compatibility()
    seed_demo_if_empty()        # inherits the poisoned session
```

Import failure means every route returns 500, `/login` included. That is D6 and
D12 compounding, and SQLite could never have shown it, because SQLite does not
abort transactions that way.

**The fix** is structural, not a wider `try`: `_apply_ddl()` runs one statement
per transaction and rolls back on failure, so a column that cannot be added
costs exactly that column. Tables are independent too — the old early `return`
on an unreadable `course_schedules` meant one bad table silently blocked all
sixteen later columns. `get_open_write_status()`'s probe SELECT got the same
treatment; it sits on the session-start path and is about to INSERT.

**The test is only possible because the baseline was pinned.** It boots the
`15dc187` clone against a scratch SQLite file to produce *precisely* the live
schema (6 venue columns, 9 record columns), then boots the current app against
that same file and asserts all 16 land, the pre-upgrade row survives, and a
second boot is a no-op. A teeth check fails the suite if the baseline ever stops
genuinely lacking those columns, so the test cannot quietly start measuring
nothing.

**Still not proven: Postgres.** This is the SQLite half. The dialect difference
that *caused* the defect is the one thing SQLite cannot demonstrate, so
verification item #7 stays open — see D12.

#### Blind-spot sweep — "what else is invisible?"

```
Status   : SHIPPED
Scope    : both
Entry    : DR-44 (which was found by accident, not by method)
Exit     : the class enumerated, each category checked against source
Evidence : docs/verification/concurrency_race_test.py — 12/12 with a measured
           pre-fix contrast (500 before, 409 after)
Closed   : 2026-10-12
```

**Why this exists.** DR-44 was found because the user asked whether a push
would work on Vercel — not because anything here went looking for it. That is
luck, and luck does not generalise. The honest problem was that the *class* had
never been enumerated: **behaviour that differs between the test environment and
production, or paths the suites never exercise.** Five stages of green tests had
said nothing about either.

What the sweep checked, and what it found:

| # | Category | Method | Result |
|---|---|---|---|
| 1 | Swallowed DB errors without rollback (the DR-44 class) | AST-ish walk of every `except` whose `try` touches the DB | **Clean.** Two hits, both `inspect(db.engine)` — a *separate* connection from `db.session`, so they cannot poison it |
| 2 | `ORDER BY` on nullable columns | every `order_by` cross-checked against model nullability | **Theoretically present, practically unreachable.** SQLite puts NULLs first on ASC, Postgres last — opposite. But all 12 ordered columns are `nullable=False` or always-defaulted datetimes, so a NULL needs a raw insert |
| 3 | `LIKE` case-sensitivity (SQLite insensitive, Postgres sensitive) | grep | **Absent.** No `LIKE`/`ilike` anywhere |
| 4 | Raw SQL outside the DDL helper | grep | **Clean.** Two sites, both already known and both now rolling back |
| 5 | Unconverted client JSON reaching a numeric column (SQLite tolerates, Postgres raises) | traced every write path to `Float`/`Integer` columns | **Clean.** `lat`/`lng`/`accuracy` are coerced at the endpoint inside a `try` that 400s, and `normalise_samples._clean` rejects NaN/Inf and range-checks. The manual path writes literals |
| 6 | Process-lifetime assumptions (Vercel = fresh process per cold start) | grep for module-level mutable state and `global` | **One, benign.** `_open_write_status_cache` caches its fallback guess for the life of a process, but this schema has no status CHECK constraint, so the fallback is correct |
| 7 | Check-then-insert races | read both insert paths against the unique constraint | **REAL — fixed.** See below |

**The one real find (DR-45).** Both attendance endpoints check for an existing
record and then insert, and the two are not atomic. On the student path the gap
is an **8-second GPS capture** wide. Two concurrent submissions from one student
both pass the check; the loser violates
`UniqueConstraint("session_id", "student_id")` at commit. There were **zero**
`IntegrityError` handlers in the file, so that was a **500 on the busiest
endpoint in the system** — returned to a student whose attendance the winning
request had just recorded successfully.

Two things made this invisible rather than obvious:

- **The local dev server handles one request at a time**, so the window barely
  exists here. Vercel runs concurrent invocations in separate processes, so it
  is genuinely open — the same environment gap as DR-44, in a different place.
- **F3 removed the client-side cause** (the 30s poll handing the student an
  enabled duplicate button mid-capture), which made the symptom disappear from
  the UI without closing the server-side possibility. A cached old client, a
  double tap or a retry still reaches it.

The fix returns the 409 the endpoint already decided on twelve steps earlier,
after a rollback so the connection stays usable. `manual-mark` got the same
guard — narrower (it needs two lecturers, or one double-clicking) but the same
constraint and the same right answer.

**The test forces the race deterministically** rather than racing for it: the
conflicting row is inserted on a second session *behind the endpoint's back*,
from inside a hook on a function that runs between the check and the insert. No
timing luck, same result every run. Its teeth were measured by stripping the
handler and re-running — **500 before, 409 after** — and the file was restored
byte-identical afterwards.

**What this sweep does NOT cover, and cannot from here:**

- **Postgres itself.** Categories 1, 2 and 5 are all *reasoned* from dialect
  documentation, not executed. The dialect difference that caused DR-44 is
  precisely the kind SQLite cannot demonstrate. D12 stands.
- **Real concurrency.** The race is forced, not raced. Two genuinely parallel
  Vercel invocations against Neon may expose ordering the hook cannot.
- **Most endpoints.** The suites cover the attendance, calibration and admin
  location paths. The calendar, schedule, enrollment, user-management and
  audit-log endpoints have no automated coverage at all.
- **The orphaned `attend.html`** (D5) remains unexamined.

#### Outage 2026-10-14 — and the boot fix (push 1 of 2)

```
Status   : SHIPPED
Scope    : Production
Entry    : none — this was an incident, not a stage
Exit     : the app boots and /login renders even with no database
Evidence : measured locally — import survives a dead database, /healthz 503s
           naming all three failed steps, /login still returns 200
Closed   : 2026-10-15
```

**Symptom.** Every route returned `500 FUNCTION_INVOCATION_FAILED`, including
`/logout` — which is `session.clear()` plus a redirect and cannot fail on its
own. That alone located the fault at import rather than in any route.

**The traceback, which is the whole story:**

```
File "/var/task/attendx_system/app.py", line 44, in <module>
    db = SQLAlchemy(app)
  ...
File ".../sqlalchemy/dialects/postgresql/psycopg.py", line 497, in import_dbapi
    import psycopg
ModuleNotFoundError: No module named 'psycopg'
```

Note the dialect file: `postgresql/**psycopg**.py` is the **psycopg3** dialect.
`requirements.txt` installs **psycopg2**-binary. SQLAlchemy asked for a driver
this project has never shipped.

**Root cause: an unpinned transitive dependency.** `Flask-SQLAlchemy==3.1.1`
requires only `sqlalchemy>=2.0.16`, and nothing pinned it further — so every
Vercel build installed whatever was newest. A bare `postgresql://` URL names no
driver, so SQLAlchemy picks the default, and *which* default is a property of
the installed version:

| SQLAlchemy | `postgresql://` resolves to | psycopg2 installed? | outcome |
|---|---|---|---|
| the 19 Sept build | `postgresql.psycopg2` | yes | worked for three weeks |
| the Oct build | `postgresql.psycopg` (v3) | irrelevant — v3 absent | **total outage** |

The builds were never reproducible. Two facts from Era II rule out the
alternatives: `SECRET_KEY` was set to 64 hex characters, so DR-33's fail-fast
was not involved; and Python was pinned to 3.12, so `psycopg2-binary`'s
`python_version < "3.14"` marker did install the driver. psycopg2 was present
and simply never asked for.

**What this was not.** It was not caused by Stage 1–5, or by the schema
upgrade, or by anything in the application code. The reverted baseline — code
that had served for three weeks — failed with a byte-identical traceback. That
is the single most important fact in this entry, because the revert was
performed on the opposite assumption.

**A wrong call, recorded.** `main` was reverted to `15dc187` before the
traceback was read, on the inference that the new code was at fault, and that
revert was described to the user as the fix. It was not: the baseline failed
identically. It was reversible and no work was lost, but it removed the project
record and every deployed feature for no benefit, and it was pushed on a guess.
Two earlier guesses in the same incident were also wrong — first `SECRET_KEY`,
then Neon being unreachable. Three inferences, three misses, while the
traceback sat one click away in Runtime Logs. **Read the traceback first.**

**The fix — three independent legs**, because any one of them alone leaves the
hole open:

1. **Pin the ORM.** `SQLAlchemy==2.0.48` in *both* requirements files. The
   resolved dialect can no longer change without someone editing that line.
2. **Name the driver.** `config._database_url()` now rewrites every Postgres
   URL — `postgres://`, `postgresql://`, `postgresql+psycopg://` — to
   `postgresql+psycopg2://`. This matters because the URL comes from the Neon
   integration, not from this repo, so the suffix cannot be assumed. Verified
   across all five input forms, with SQLite left untouched.
3. **Remove the version marker.** `psycopg2-binary==2.9.10`, no
   `python_version` condition. The marker (D7) made the driver vanish silently
   on any Python bump; without it, a Python with no wheel fails the **build**,
   loudly, instead of producing a deployment that 500s every request.

**And D6, finally closed.** The import-time bootstrap was three bare calls, so
*any* failure in them killed module import and took `/login` with it. It is now
a loop where each step is isolated, failures are collected into
`BOOTSTRAP_ERRORS`, logged, and surfaced at a new public `/healthz` (200, or
503 listing what failed). Measured: with an unreachable database, all three
steps fail, `/healthz` returns 503 naming each one, and **`/login` still
returns 200**. Before, that same scenario was `FUNCTION_INVOCATION_FAILED` on
every URL.

**The limit of D6, stated plainly.** It does **not** cover a missing driver.
`db = SQLAlchemy(app)` resolves the dialect and imports the DBAPI at line 44,
*before* the bootstrap block, so this exact outage would still have been a
blackout with D6 in place. Legs 1–3 are what address that; D6 addresses the
database being unreachable and the schema upgrade failing. Wrapping the
`SQLAlchemy()` call is not an option — every `class X(db.Model)` below it would
lose its base class.

**Why Stage 1–5 is held back (push 2).** The boot path has never succeeded on
real Vercel, and the one genuinely untested step remains: the sixteen-column
schema upgrade against Neon (D12). Pushing the fix on known-good baseline code
first proves the boot path with one variable changed instead of two. With D6 in
place, if that upgrade then stumbles on push 2, the result is degraded
dashboards and a readable `/healthz` rather than another blackout.

Only `docs/PROJECT_LOG.md` is restored to `main` in this push. The eight
verification suites stay with the code they exercise — most of them test Stage
1–5 symbols that do not exist on this branch and would fail here.

#### Push 2 — the stages join the proven boot path

```
Status   : LIVE
Scope    : Production
Entry    : push 1 LIVE — /healthz 200 {"ok":true}, /login 200, / 302
Exit      : after the push, /healthz stays 200 with Stage 1–5 present, and the
           sixteen-column upgrade completes against Neon (D12)
Evidence : 0dd5d46 (code), eeb5999 (log), a90505a (merge); 182/182 across all
           eight suites. Live observations 2026-10-21, see below
Closed   : 2026-10-21
```

**Observed live, 2026-10-21.** Vercel serves the previous deployment until the
new build is ready, so `/healthz` returning 200 proved nothing on its own — it
is 200 on both builds. The distinguishing probe is a route that exists only in
Stage 5: `GET /api/admin/locations/1/calibration` returns **405** (the path
exists, POST only) where the baseline returns **404**. `POST` to the same path
returns 302 to the login page, which is the correct unauthenticated answer.

`/healthz` returning `{"ok":true}` is the fact that matters. It performs a live
`SELECT 1` and reports every recorded `BOOTSTRAP_ERRORS` entry, so a 200 means
`create_all`, `ensure_schema_compatibility` and `seed_demo_if_empty` all
completed against Neon. **That is the sixteen-column upgrade running on
Postgres for the first time — D12's unrun half, now run.**

**Why this was a cherry-pick and not a merge.** `main` is a linear history that
runs *through* `2ef258e` (Stage 1–5), then `38199b3` (revert everything), then
`38707ca` (boot fix on the reverted tree). Merging `main` into `stage-1-5-work`
therefore asks git to combine "Stage 1–5 present" with "Stage 1–5 deleted, and
that deletion is the most recent word on the matter" — and it resolves in
favour of the deletion. The merge was run, the resulting tree *inspected*, and
found to contain the boot fix and no Stage 1–5 code at all. It was aborted
before it could be committed or pushed.

`git cherry-pick 38707ca -n` replays the diff instead of combining histories,
so it never asks the question that gets answered wrongly. It conflicted only on
`docs/PROJECT_LOG.md` (add/add, because both sides rewrote it), resolved by
taking the pushed version, which already contains every stage record.

To return the result to `main`, a merge commit with `-s ours` is used — parent
1 the Stage 1–5 tree, parent 2 `38707ca`, tree taken wholly from parent 1. The
boot fix reaches `main` as its own content (cherry-picked) rather than through
the discarded history, and the revert stays in the record instead of being
erased by a force-push. `deploy-baseline` still pins `15dc187`.

**Two holes found on this tree, neither present on `main` alone.**

The first is a guard that had never actually fired: `_resolve_secret_key`
refused `_PLACEHOLDER_SECRET_KEY`, while its own comment described the value as
"shipped in `.env.example`". `.env.example` ships a *different* string —
`change-this-to-a-long-random-string`. The guard therefore rejected a value
nobody would paste and accepted the one everybody would, which is the precise
inversion of its purpose and the one case where a known key is dangerous (DR-51).

The second is a test harness that lied about itself. Both client suites locate
the code they test by marker and reverse-apply known fixes to prove the tests
have teeth. Every marker is written with `"\n"`. On this checkout,
`core.autocrlf=true` had written the template with CRLF, so the multi-line
reverse literals matched nothing and the suites reported *"pre-fix reversal
found nothing to reverse"* — a failure that reads like a missing fix but is a
line-ending mismatch. Content was verified byte-identical modulo line endings
before changing anything. Both suites now normalise CRLF → LF on read (DR-50).

**What was still not proven — and now is.** The sixteen-column upgrade against
Neon (D12) had only ever run against a SQLite file built by the real baseline
app. It ran for the first time on the first cold start after this push, and
`/healthz` answered `{"ok":true}`, which is only possible if all three
bootstrap steps — including the upgrade — completed. The mitigation was in
place but was not needed: had the upgrade failed, D6 would have degraded the
dashboards rather than blacked them out, `/healthz` would have named the failed
step, and `/login` would still have rendered. That is the trade the two-push
sequence was chosen to make, and it is worth stating that it did not have to be
taken.

#### Stage 6 — Collusion detection

```
Status   : PLANNED
Scope    : both
Entry    : Stage 1 LIVE (needs spread_m for zero-scatter detection)
Exit     : both detectors flag a seeded case and neither auto-rejects
Evidence :
Closed   :
```

---

## 5. Known drift

Undocumented drift is what a log is for. Each item is a place the code and its
description disagree, or a known defect carried deliberately.

> **Line numbers below are as of 2026-09-30 and are a convenience, not the
> reference — the named symbol is.** Every citation in this table drifted once
> already: Stage 4's docstrings pushed `qr_required` from 1146 to 1222 and
> `db.create_all()` from 3061 to 3258 without a single behavioural change, so a
> reader following the old numbers landed in unrelated code and would have
> reasonably concluded the log was wrong about the defect too. Verification item
> #10 exists for this. Grep the symbol; correct the number in the same commit.

| # | Drift | Status |
|---|---|---|
| D1 | `README.md` stated `MAX_GPS_ACCURACY_METRES = 30`; [config.py:146](../attendx_system/config.py#L146) is `500`. The README described a far better system than the one deployed — and `500` is exactly the value producing the 550m effective fence | **Resolved** 2026-09-23 — README corrected to `500`. Superseded 2026-09-26: the constant is now an upper bound on a relative cap, `min(2 × radius, 500)`; README updated to say so |
| D2 | `validate_qr_token`'s docstring claimed it accepts the current and previous window; the code accepted offsets `(0, -1, -2, -3)` — roughly 120 seconds, a generous window for relaying a photo of the QR | **Resolved** 2026-09-26 — Stage 4. Now `(0, -1)` in `validate_qr_token` at [app.py:1135](../attendx_system/app.py#L1135), ~60s. The code was the wrong half of the disagreement: a QR's only security property is confinement by light, so the acceptance window *is* the relay window |
| D3 | Geofence was `base_radius + accuracy`, so the worst accepted reading passed at 550m. The rule asked "is it impossible they are in the room", not "are they in the room" | **Resolved** 2026-09-26 — Stage 2. Note the reject boundary itself is unchanged (`d − a > r` ⟺ `d > r + a`); what closes this is the accuracy cap becoming `min(2 × radius, MAX_GPS_ACCURACY_METRES)`. A 300m fix the old rule accepted at 350m is now refused at 100m |
| D4 | QR validation was skipped entirely when the field was absent (`if qr_token:`). The anti-relay system was opt-in by the party it constrains, so it protected exactly the students honest enough to use it | **Resolved** 2026-09-26 — Stage 4. Validation still only runs when a token is present ([app.py:2427](../attendx_system/app.py#L2427)), but `qr_required()` at [app.py:1222](../attendx_system/app.py#L1222) now decides whether one was *needed*, enforced at [app.py:2568](../attendx_system/app.py#L2568) after the verdict exists |
| D5 | `attend.html` is orphaned — it calls a non-existent `/api/attend/info` and carries a `demo_coords` fallback that would return the venue's own coordinates. Dead today; **a backdoor if ever wired up** | Open — out of scope; delete or gate before any real use |
| D6 | `db.create_all()` + `ensure_schema_compatibility()` + `seed_demo_if_empty()` ran at module import, so any database hiccup returned 500 for every route, including `/login`. Carried as "out of scope" since Stage 0 | **Resolved** 2026-10-15 — and it stopped being out of scope the day it caused a total outage. The three calls are now an isolated loop; failures collect into `BOOTSTRAP_ERRORS`, are logged, and surface at a public `/healthz` (200, or 503 listing each failed step). Measured: with an unreachable database all three fail and `/login` still returns 200. **Known limit:** `db = SQLAlchemy(app)` imports the DBAPI before this block, so a *missing driver* still kills the import — that case is covered by the SQLAlchemy pin and the explicit `+psycopg2` suffix, not by this |
| D7 | `psycopg2-binary` was pinned `; python_version < "3.14"`, so the driver would vanish silently on a Python bump with no build error | **Resolved** 2026-10-15 — marker removed, `psycopg2-binary==2.9.10`. A Python with no wheel now fails the *build* rather than yielding a deployment that 500s every request. The 2026-10-14 outage was the same shape through a different route — the driver was installed and simply never asked for (D16), which is why the marker was not the only leg that needed closing |
| D8 | Demo credentials — including `admin@university.edu` / `password123` — are seeded into the **production** database on cold start by `seed_demo_if_empty()` | **Accepted**, see DR-5 |
| D9 | **Calibration bootstrap deadlock.** Found while implementing Stage 2, not before it. `OUTSIDE` always rejects and nothing is stored. So a venue whose WiFi fingerprint is systematically offset — say 120m, exactly the §1.1 case Stage 5 exists to fix — rejects every student, accumulates zero records, and can therefore *never* be calibrated. The stage designed to cure systematic offset is unreachable at precisely the venues that have it | **Resolved** 2026-09-26 — Stage 4, `qr_ground_truth_override()` at [app.py:1290](../attendx_system/app.py#L1290). A valid scan at an **uncalibrated** venue overrules `OUTSIDE` within `QR_GROUND_TRUTH_MAX_M` = 500m, landing on `AMBIGUOUS` rather than `INSIDE`, and never applies once a venue is calibrated. Stage 2 was deliberately shipped unchanged rather than weakened to work around this |

| D10 | **The QR token is not bound to the student who submits it.** It is `HMAC(SECRET_KEY, "session_id:window")` — no student id, no device, no IP, no position, and not single-use. One person in the room can therefore serve the current code to any number of people outside it, and every one of those submissions is indistinguishable from theirs. Stage 4's F8 fix (removing `venue_coords` from the rejection body) raises the cost of *pairing* that token with a passing position from one request to three plus trilateration — it does not remove it | Open — **mitigated, not closed, and not closable in this design.** The durable fix is binding the token to the submitting student, which a single code projected to a whole room cannot express. Any real fix changes the interaction model (per-student codes, or the student displaying and the lecturer scanning) |
| D11 | **No finding touching Stage 4, the client page, or Stage 5 has been checked by anyone but the code's author.** The original wording ("a six-lens review was attempted 2026-09-30 and never ran — zero completions, no journal") was true of two attempts (`wf_01f9b7ed-bde`, `wf_c027cf96-6ff`) and wrong as a general claim: `wf_422082f3-407` raised 18 server findings and `wf_2de7edaf-ec5` raised the 6 client findings, both with journals on disk. What consistently failed was the **refutation** pass, not the finders — so findings were *raised* across the board and *refuted* nowhere. Stage 5's attempt (`wf_d89a1d51-13d`, 2026-10-07) failed at the *finder* stage instead: 4/4 agents errored (3× HTTP 402, 1× DNS), zero results, and its `{"raised": 0}` is an artefact of total failure. Across four attempts the refuters have never once run | Open — re-run when budget allows. Until then the strongest claim for anything Stage-4-or-later is "every test written for it passes, and the tester wrote the code." Note the author's own review is not worthless: it produced DR-26–DR-34, F1–F6, and DR-42, all real. It simply shares the author's blind spots by construction |
| D12 | **`ensure_schema_compatibility()` has never been run against Postgres.** Stages 1–5 add **sixteen** columns across two tables by guarded `ALTER TABLE` (DR-4), and every execution to date has been SQLite. *Count corrected 2026-10-11* — this entry previously said "nine columns" for Stages 1–4 and then fifteen overall; the actual figures are ten for Stages 1–4 (eight on `attendance_records`, two on `venues`) plus six for Stage 5, so sixteen. The two dialects differ where it matters: SQLite ignores most `ALTER` constraint semantics, and — the defect this surfaced — SQLite does not abort a transaction on a failed statement while Postgres does | **Resolved live 2026-10-21.** The upgrade ran against Neon on the first cold start after push 2. `/healthz` answered `200 {"ok":true}`, which is only possible if `ensure_schema_compatibility()` completed — the route reports every recorded `BOOTSTRAP_ERRORS` entry alongside a live `SELECT 1`, so a single failed `ALTER` would have produced a 503 naming it. The local half was already exercised end-to-end (`docs/verification/schema_upgrade_test.py`, 26/26: baseline clone builds the live schema, current app upgrades it, 16/16 columns land, pre-upgrade data survives, second boot is a no-op), and the transaction-poisoning hole it exposed was fixed in advance (DR-44). **What this does not prove:** that the new columns hold the values the stages expect — only that they exist and no `ALTER` failed. Stage-level behaviour on real data is still tested on SQLite only |
| D13 | **The client review's six findings (F1–F6).** Three were introduced by the Stage-1 capture rewrite (F1 window-start, F2 transient-error, F6 early-exit over-trust); two the page always carried (F3 poll-detaches-capture, F4 indicator-reset); one is server-side display (F5 lecturer shown the device self-report, not `acc_eff`) | **Resolved** 2026-10-06 — see the "Client-layer review and remediation" block. Fixes in [student_dashboard.html](../attendx_system/templates/student_dashboard.html) (F1–F4, F6) and `reported_uncertainty` in [app.py](../attendx_system/app.py) (F5); proof in `docs/verification/`. Author-confirmed only — D11 applies |
| D14 | **A calibrated venue's fence is learned, so it moves off the survey — and can differ from it in both directions.** The centre is the median of verified check-ins and the radius their 90th-percentile reach + 10m. By design this corrects a bad survey (§1.6), but it means a calibrated venue accepts some fixes its survey rejected and rejects some it accepted, and the learned radius is generally *larger* than a tight one. The 150m displacement cap and the 2× radius cap bound how far; the admin `clear` action undoes it entirely | **Accepted** 2026-10-07 — the alternative is a fence that never improves, which is the state this stage exists to end. Bounded, audited, and reversible; the survey delta is surfaced to the admin rather than hidden |
| D15 | **Five endpoint families have no automated coverage at all.** The eight suites exercise the attendance mark path, the calibration logic, the schema upgrade, the boot/config path, the client capture/render layer and the admin location endpoints. Untouched: the lecturer calendar and day view, schedule postpone, enrollment management, user promote/demote, and the audit-log reader. Surfaced by the 2026-10-12 blind-spot sweep, which is also the reason this entry exists — the previous status board reported 170 passing checks without saying what they do *not* reach. The count is now 182 and the gap is unchanged: an eighth suite that tests the boot path does not test the calendar | Open — the untested families are mostly CRUD over already-validated models, which is why they were deprioritised rather than forgotten. Two carry real risk and should be covered first: schedule postpone (mutates `postpone_count`, the one `NOT NULL DEFAULT` column added by ALTER) and promote/demote (changes `is_superuser`, an authorisation boundary) |
| D16 | **Dependency resolution was never reproducible, and it caused the only total outage this project has had.** `Flask-SQLAlchemy==3.1.1` requires just `sqlalchemy>=2.0.16`, so each Vercel build installed whatever was newest. A bare `postgresql://` URL names no driver, so the dialect came from the installed version: one build chose psycopg2 (present, worked three weeks), a later one chose psycopg3 (absent) and every route returned 500. The same risk applies to every unpinned transitive dependency here — Werkzeug, Jinja2, itsdangerous and greenlet all arrive unpinned through Flask and SQLAlchemy | **Mitigated** 2026-10-15 for the driver specifically: SQLAlchemy pinned, the driver named in the URL, the version marker dropped. **Open in general** — nothing pins the rest, and no lockfile exists. The durable fix is a hash-pinned lockfile (`pip-compile`/`uv lock`) so a build cannot silently differ from the one that was tested. Until then, any build may resolve differently from the last |

The two `requirements.txt` files (repo root and `attendx_system/`) are currently
identical apart from a trailing newline. They must stay in sync; Stage 6 adds
`h3` to both.

Stage 5 also sharpens D12: it adds **six more** `ALTER TABLE` statements
(`venues.cal_*`) that have likewise only ever run against SQLite. Verification
item #7 now covers fifteen columns across two tables, and the Postgres half
still cannot be attempted on this machine (D7/D12).

---

## 6. Decision record

Choices a later reader would otherwise mistake for accidents.

| # | Date | Decision | Reasoning |
|---|---|---|---|
| DR-1 | 2026-09-19 | One Vercel project routing all paths to `attendx_system/app.py`; do **not** recreate the 5000/5001/5002 split in production | Vercel has no ports. The split exists to hold three session cookies on one machine, which a single hosted URL cannot do anyway |
| DR-2 | 2026-09-19 | Keep `run_*.py` as local-only development entry points rather than deleting them | They remain the fastest way to test all three roles at once; they are simply never imported in production |
| DR-3 | 2026-09-19 | Fail fast at import on an unusable `DATABASE_URL` rather than letting SQLAlchemy raise on first query | A one-line `RuntimeError` in the build log is diagnosable; a psycopg2 traceback on a cold start is not. This did not fix the deploy — it made the failure legible while the real cause was fixed in the dashboard |
| DR-4 | 2026-09-19 | Evolve the schema with guarded `ALTER TABLE` inside `ensure_schema_compatibility()` rather than adopting Alembic | The pattern already exists and is idempotent. Introducing migrations mid-deployment adds a failure mode for no benefit at this size |
| DR-5 | 2026-09-19 | Accept demo seeding writing known credentials into the production database | Stated basis: this deployment is a temporary demonstration and comes down once it has served its purpose; the acceptance criterion given was that roles and ports must not conflict on deployment. **This acceptance expires if the deployment becomes durable or publicly advertised** — gate `seed_demo_if_empty()` behind `SEED_DEMO_DATA` and rotate the admin password at that point |
| DR-6 | 2026-09-23 | Treat "collision" as **both** overlapping venue geofences and student collusion | They compound: overlapping fences make collusion harder to distinguish from ordinary proximity, so fixing either alone leaves the other looking like noise |
| DR-7 | 2026-09-23 | QR enforcement **adaptive** — required only when the situation is genuinely ambiguous | Always-on QR punishes every student for a problem specific to stacked and overlapping venues. Never-on is the current defect |
| DR-8 | 2026-09-23 | Model floors as `building` + `floor` columns; never as altitude, and never in distance maths | Every geometric avenue is closed: GNSS vertical error is 15–50m against 3–4m floor spacing and usually `null` indoors; browsers expose no barometer; Web Bluetooth cannot passively scan and is absent on iOS Safari. Floor labels reports and detects stacking — nothing more |
| DR-9 | 2026-09-23 | H3 is for collusion clustering and analytics only — **never** as a replacement for the haversine geofence check | H3 is an index over coordinates already held. A fix that is 300m wrong snaps to a hexagon that is 300m wrong, and then looks authoritative. That is worse than looking uncertain |
| DR-10 | 2026-09-23 | Confidence at an uncalibrated venue is capped, regardless of how tightly samples agree | Sample agreement measures *precision*. A systematically offset WiFi fingerprint yields ten copies of the same error, scoring near-perfect confidence while being 100m wrong. Nothing internal to the sensor can detect this; only external ground truth can |
| DR-11 | 2026-09-23 | Learn venue fences from QR-verified check-ins rather than trusting the admin's walked circle | A QR-verified check-in is a labelled observation: *this device reported these coordinates while provably inside this room*. It is the only mechanism available here that corrects systematic offset, as opposed to reducing noise |
| DR-12 | 2026-09-23 | Collusion detectors **flag for review, never auto-reject** | False positives are certain — two students genuinely sitting side by side share a 3.6m cell. Silently marking a present student absent is far more damaging than a flag a lecturer dismisses |
| DR-13 | 2026-09-26 | `AMBIGUOUS` is **accepted and flagged**, not rejected | The old rule's failure was over-acceptance, but the fix for "a binary test has false rejects" is not a stricter binary — it is to stop returning a binary. Rejecting ambiguity would trade one silent error for a louder one, and a lecturer with a marked list can resolve in seconds what no threshold can resolve at all |
| DR-14 | 2026-09-26 | Record the verdict in new `verify_state` / `confidence` columns; leave `status` alone | `status` is `String(10)`, so `"PRESENT_UNVERIFIED"` does not fit, and `get_open_write_status()` shows this database may carry CHECK constraints on status columns — a new enum value risks an insert failure on Neon. Separate columns keep every existing report working and make the new judgement opt-in by name |
| DR-15 | 2026-09-26 | The accuracy cap is **relative** — `min(2 × radius, MAX_GPS_ACCURACY_METRES)` | A fixed 500m limit is meaningless against a 50m fence: the uncertainty is ten times the thing being measured. The absolute term is kept only as a ceiling, so that configuring a very large venue radius cannot quietly disable the check altogether |
| DR-16 | 2026-09-26 | Ship Stage 2 unchanged despite discovering D9 mid-implementation | The deadlock is real but its fix belongs where QR logic already lives. Weakening `OUTSIDE` here to work around it would have removed the only rejecting state before the mechanism that justifies rejecting less — QR as ground truth — existed to replace it |
| DR-17 | 2026-09-26 | Rank competing venues by the **normalised** margin `d / r`, not raw distance | Raw distance cannot compare a 200m quadrangle against a 50m seminar room; normalising asks "how deep inside its own fence is this fix", which is comparable across venues. 0.0 is dead centre, 1.0 is on the fence. The known consequence is accepted: where one venue *nests* inside another, the larger venue wins for any fix near the inner edge, because being 40m into a 200m yard is a deeper claim than being 40m into a 50m room. That produces an `AMBIGUOUS` flag, not a rejection, which is the correct outcome for genuinely nested geometry |
| DR-18 | 2026-09-26 | A **stacked** peer contests regardless of margin, and ties go to the challenger | Both are deliberate asymmetries against the session's own venue. For stacking, the margin is not evidence of anything: the two rooms share a footprint, so identical coordinates are expected and a better margin is meaningless. For ties, the comparison is `<=` rather than `<` — if two venues are equally good candidates, the honest answer is that GPS cannot tell them apart, so the doubt is recorded rather than resolved by whichever row the database returned first |
| DR-19 | 2026-09-26 | The venue contest can **downgrade** to `AMBIGUOUS` but can never upgrade a state and never reject | A student in a real place with a real fix must not become absent because the institution mapped two rooms close together. That is a data-quality problem belonging to whoever surveyed the venues. Note that `confidence` stays unchanged on a contested record — the geofence confidence is genuinely the same; the ambiguity is about *which* venue, not about the distance, and conflating the two would make the score unreadable |
| DR-20 | 2026-09-26 | Suppress overlaps below `OVERLAP_MIN_SIGNIFICANT_M = 1.0` when **reporting**, not when **computing** | Two venues 99.89m apart with a combined 100m reach do overlap, by 11cm. The predicate was right; surfacing it as an admin warning was not, because an admin who cannot act on a warning learns to ignore warnings. The threshold therefore lives in `venue_conflicts` and `venues_overlap` stays exactly geometric, so nothing downstream inherits a fudged number |
| DR-21 | 2026-09-26 | Validate a QR token where it arrives (step 2b); decide whether one was *required* after the verdict exists (step 8b) | The two questions have different inputs. Validity depends only on the token; necessity depends on the verdict, the venue's calibration and the contest, none of which exist at step 2b. Splitting them also fixes a smaller wrong: an invalid token is now rejected whether or not one was needed, because accepting a submission that carried a failed scan tells the student their scan was pointless |
| DR-22 | 2026-09-26 | `qr_required()` returns a **reason list**, not a boolean | A student told only "scan the code" with no reason assumes the app is broken. More importantly the reasons are not equivalent: `stacked_venue` is structural and permanent, `coarse_fix` clears by walking to a window, and `venue_uncalibrated` disappears on its own once the venue has been used enough. A lecturer fielding complaints needs to know which of those they are looking at |
| DR-23 | 2026-09-26 | A valid QR scan **overrules** an `OUTSIDE` verdict at an uncalibrated venue, bounded at 500m, landing on `AMBIGUOUS` | This is D9's fix and it is a deliberate bypass of the only rejecting state, so the reasoning matters. The token is better evidence than the survey it contradicts: the code was on the projector *in that room*, while the survey is one admin's walk with the same offset sensor that produced the rejection. Without it a venue with a bad survey rejects every honest fix, stores nothing, and can therefore never accumulate the records needed to correct itself — the stage designed to cure systematic offset would be unreachable at exactly the venues that have it. It lands on `AMBIGUOUS` rather than `INSIDE` so the disagreement between geometry and the token is preserved, and it is bounded because a photographed code travels while a survey error does not exceed campus scale |
| DR-24 | 2026-09-26 | Ship a lecturer manual override **as part of** Stage 4, not after it | The plan asked whether one already existed; it did not — `AttendanceRecord(` appeared in exactly one place, the student's own submission. Mandatory scanning with no escape hatch turns a dead battery or a cracked screen into an absence, which is a worse failure than the one the enforcement fixes. Deliberately a *different mechanism* rather than a looser geofence: it requires a human who can see the room and names that human in the audit log |
| DR-25 | 2026-09-26 | A manual record carries three redundant markers, and consumers must filter on `verify_state` — never on coordinates | It stores the venue's own centre because `lat`/`lng` are `NOT NULL` and SQLite cannot drop that, which is structurally the same as the `demo_coords` backdoor D5 warns about. `verify_state = MANUAL`, `qr_verified = False` and `sample_count = 0` each independently say "this is an assertion, not a measurement". The redundancy is the safeguard: calibration feeds only on `qr_verified`, Stage 6's zero-scatter rule needs `sample_count ≥ 5`, and its cluster rule must exclude `MANUAL` rows, which all share one point by construction |
| DR-26 | 2026-09-30 | The QR ground-truth override bound is **relative** — `min(500, 3 × radius)` via `qr_ground_truth_limit()` | DR-23 set it at a flat 500m, which against the default 50m fence is a 10× widening: structurally the same 550m effective fence D3 exists to remove, reintroduced by the mechanism meant to rescue it. `3 ×` is chosen against the failure it must absorb — §1.6's systematic survey offset, tens of metres — not against how far a photograph can travel, which is unbounded. The absolute 500m is kept only as a ceiling, so a very large configured radius cannot quietly disable the bound |
| DR-27 | 2026-09-30 | **Both** acceptance paths read the *same* limit function | The override path checked 500m while the ordinary `AMBIGUOUS` band `d ≤ r + a` reached `r + min(2r, 500)` — 600m at a 200m venue, 900m at a 400m one — and never consulted the bound at all. A bound only one of two paths honours is not a bound, and the two reaches *inverted* with radius, which is exactly the kind of thing no single test case catches. One function, both callers |
| DR-28 | 2026-09-30 | Calibration eligibility is a **stored column** (`qr_trusted`), decided and frozen at write time, not re-derived when Stage 5 reads | Re-deriving would mean the eligibility of historical rows silently changes whenever a venue's radius is edited — the one thing an audit trail must not do: a row that fed calibration in March would stop having done so in April, with no record of either. Frozen at write time, the column says what was true when the observation was made. The cost is accepted: a bound tightened later does not retroactively evict rows admitted under the looser one |
| DR-29 | 2026-09-30 | An override row is excluded from calibration at **any** distance, not merely beyond the bound | A row admitted by the override is by definition a fix the geofence calls *provably* outside. Feeding it to calibration is the fence rewriting itself from the evidence of its own failures. Worse, it closes a loop behind itself: poisoning earns promotion, promotion sets `cal_*`, `is_calibrated()` flips true, and the override switches off — leaving the poisoned fence in place with the corrective mechanism disabled. The §1.6 bootstrap is unaffected, and this was measured rather than assumed: survey offsets are comparable to `a_eff`, so those rows arrive `AMBIGUOUS`, not `OUTSIDE`, and stay eligible |
| DR-30 | 2026-09-30 | Stage 5's 150m displacement cap is **load-bearing**, not a belt-and-braces check | At the default 50m venue the furthest a calibration-grade row can sit is `3r` = 150m, which equals that cap exactly. The cap does not sit comfortably outside the range of admitted data — it sits precisely on its edge, and one row is enough to reach it. Recorded because a later author tuning either number in isolation would break the other, and because such a row stores `qr_trusted = True` alongside `confidence ≈ 5.6e-14`: both correct, and jointly surprising |
| DR-31 | 2026-09-30 | `NULL` in `qr_trusted` means *not eligible*; `NULL` in `qr_override` means *unknown*. The asymmetry is deliberate | `bool(None)` is `False`, so a boolean column cannot distinguish "no" from "never recorded" — and the right resolution differs by column. For eligibility, not-eligible **is** the safe reading and Stage 5 must act on `NULL` exactly as on `False`, so `bool()` is correct. For the override flag a lecturer reads as fact, `False` would assert "this was not an override" about a row written before the column existed — which may well have been one, under the old flat-500m rule. The payload therefore returns `None` there. Reporting a guess as a fact is the precise failure this redesign exists to remove; reintroducing it in the view would be perverse |
| DR-32 | 2026-09-30 | The `OUTSIDE` rejection body no longer returns `venue_coords` | It returned the fence's own centre, to anyone, with no token required — and submitting it back verbatim drives `d` to 0 from anywhere, which makes every distance-based bound in this file decorative. `distance_m` and `radius_m` are retained, because a student who cannot see *how far off* they are cannot act on the rejection. This is a cost increase, not a fix: three requests and trilateration still recover the centre. See D10 |
| DR-33 | 2026-09-30 | `SECRET_KEY` fails fast at import when `VERCEL=1` and the value is the placeholder, blank, or unset | Stage 4 changed what that key *means*. It previously signed only the session cookie; it now also signs the QR token, which is the thing that admits a student. A known key therefore computes valid tokens offline, from anywhere, indefinitely — no room, no projector, no photograph. The same placeholder that was ordinary hygiene before this stage is a complete bypass of the stage's central mechanism after it, so the check refuses to boot rather than warn |
| DR-34 | 2026-09-30 | A manual record writes `qr_trusted = False` **explicitly**, not by omission | The writer set `qr_verified = False` with a comment claiming that kept the row out of calibration; Stage 5 reads `qr_trusted`, which it left `NULL`. Under DR-31 that is still not-eligible — but only for as long as the filter reads `.is_(True)`. A future `!= False` or `isnot(False)` would admit it, and this is the worst row in the system to admit: its coordinates *are* the surveyed centre (DR-25), so feeding it back drags the learned centre towards the very offset §1.6 exists to correct, and the more a lecturer manually marks a badly-surveyed venue the harder that survey becomes to fix. A guarantee resting on which of three equivalent-looking filters a later author happens to write is a guarantee held by accident |
| DR-35 | 2026-09-30 | Stage 4 moved to `SHIPPED` on author-run evidence, with the missing independent review recorded as D11 rather than the row held back | The row's own prior text said it would move "when the adversarial review clears", and the review did not run (7/7 HTTP 402). Two honest options existed: leave the row `IN PROGRESS` indefinitely on an infrastructure failure, or move it and name the substitution. The second was chosen because the first makes the status board lie in the other direction — the code *is* merged and serving — and because a blocked row with no stated reason decays into a row nobody rechecks. The substitution is explicit: 47 adversarial probes and four green suites, all written by the author of the code |
| DR-36 | 2026-10-07 | The learned fence reads `.is_(True)` on `qr_trusted` — never `== True`, never a truthiness test | The single line that decides whether the fence can be poisoned. `NULL` means not-eligible (DR-31/DR-34), and only `.is_(True)` excludes it while admitting eligible rows. This is DR-34's "guarantee held by accident" made deliberate: the filter the design depends on is written exactly once, in one query, and the integration test asserts it against 300m-off NULL and False rows so a later loosening fails a test rather than silently opening the loop |
| DR-37 | 2026-10-07 | The learned centre is the **median** and the learned radius the **90th percentile**, never the mean or the max | This loop writes the security boundary it enforces, so a single record must not be able to move it. A mean is dragged by one bad fix and a max is *defined* by one student who scanned at the window — either lets one row set the fence for the room. The median resists the first and the percentile the second, and the percentile is chosen over a trimmed max because it degrades smoothly as records accumulate rather than jumping when the 30th lands |
| DR-38 | 2026-10-07 | Promotion requires breadth on four axes — ≥30 records, ≥15 students, ≥3 sessions, ≥2 days — checked **before** the poisoning brakes | The two guards answer different questions. Breadth asks "is this evidence yet?": a displacement computed from nine records is not, and flagging on it would put noise in front of an admin who then learns to ignore flags (the same reasoning as DR-20). The brakes ask "given enough evidence, does it say something dangerous?". Ordering breadth first is what keeps the flag queue worth reading |
| DR-39 | 2026-10-07 | A candidate that trips a brake is **held**, not applied and not discarded — `cal_review` plus an admin `apply`/`clear` | The brakes cannot decide whether a 200m displacement means the survey was wrong or the data was gamed, and that distinction is exactly what a human who can see the room supplies. Discarding would throw away a genuine survey correction; auto-applying would hand the fence to whatever moved it. Holding is the only option that keeps the information and the decision separate, and `apply` makes the admin's override of the brake an explicit audited act rather than a silent one |
| DR-40 | 2026-10-07 | `effective_fence_centre()` is new; the geofence reads centre **and** radius from the same source | Stage 5 as planned would have set `cal_*` and flipped `is_calibrated()` — but every distance was still measured from the surveyed centre, so the fence would not have moved at all: only its confidence ceiling and QR override would have changed. A learned radius measured against a surveyed centre is worse than either alone. The two accessors are deliberately separate functions with a shared precondition, and the integration test asserts the fence geometry actually changes (a probe inside the learned fence and outside the survey) rather than trusting that it did |
| DR-41 | 2026-10-07 | Calibration runs on session close at all three close sites, is idempotent, and never raises into its caller | There is no explicit close endpoint, so "session closed" happens lazily and implicitly; missing a site would mean a venue that never recalibrates depending on which path closed the session. Reading the whole eligible population each time makes multiple triggers harmless. And attendance must never fail because calibration did — the recalculation is strictly secondary, so it swallows its own errors, and a lecturer's manual mark or a student's submission is committed before the hook runs |
| DR-42 | 2026-10-07 | **Verification-time readers use the effective (learned) fence; design-time diagnostics and physical claims use the survey.** `venue_radius()` and `venues_overlap()` are calibration-aware, `contested_by()` reads effective centres for itself and every peer, and `venue_conflicts()` passes `effective=False`; `_surveyed_radius()` is the deliberate way to ask for the walk | Found by author self-review, and the class of bug this stage was always most likely to produce: Stage 5 moved the *geofence* onto the learned fence and left three other readers of the same venue on the survey, so at a calibrated venue the contest downgraded against one circle while the verdict came from another — with every suite green, because nothing tested two readers against each other. The split is not cosmetic: a set-gps overlap warning must describe the perimeter the admin just walked (they can act on that), while `overlaps_another_open_session` must describe the fences actually in force (that is what a student's fix is judged against). Keeping one function for both would force one of the two callers to be wrong. `_surveyed_radius()` exists so the calibration brakes measure against the original survey and recalibration cannot ratchet — each learned radius becoming the baseline the next is allowed to double |
| DR-43 | 2026-10-07 | A re-survey through `set-gps` **clears** any learned fence and any pending candidate, and says so in the audit entry | The learned geometry is defined relative to the survey — the displacement cap measures against it — so a calibration that outlived its survey would be a fence whose own provenance no longer held, and the next recompute would measure displacement from the wrong baseline. An admin re-walking a perimeter has just asserted the old geometry was wrong; keeping a fence derived from it would silently overrule them. The venue re-earns calibration from check-ins, and `cleared_calibration` in the log stops the loss looking like a venue that mysteriously went back to demanding QR |
| DR-44 | 2026-10-11 | Schema DDL runs **one statement per transaction with a rollback on failure** (`_apply_ddl`), tables are independent of each other, and the same rollback discipline applies to `get_open_write_status()`'s probe SELECT | Found while answering "would a push be fine on Vercel?", and it is the one defect here whose blast radius was the whole application. Postgres aborts the entire transaction on a failed statement and every later statement raises `InFailedSqlTransaction` until someone rolls back; the old code had **seven** `except Exception: pass` handlers and **zero** rollbacks, plus a completely unguarded first `ALTER`. So one DDL hiccup on the first cold start after a push would poison the session, silently fail the remaining fifteen columns, and then throw inside `seed_demo_if_empty()` — which runs at *import*, so module import fails and **every route 500s, including `/login`**. SQLite never showed any of this, which is exactly why it survived five stages of green tests. The fix is structural rather than a wider `try`: a column that cannot be added now costs exactly that column. The early `return` on an unreadable `course_schedules` is also gone — it meant one bad table silently blocked all sixteen later columns |
| DR-45 | 2026-10-12 | Both attendance inserts catch `IntegrityError`, roll back, and return the **409 the endpoint already decided on** rather than letting a unique-constraint violation become a 500 | The check-then-insert gap on the student path is an 8-second GPS capture wide, and the file had zero `IntegrityError` handlers — so losing a race meant a 500 served to a student whose attendance the *winning* request had just recorded. Two things kept it hidden: the local dev server is single-request, so the window barely exists off Vercel; and F3's client fix removed the visible cause (a poll handing out an enabled duplicate button) without closing the server-side hole, so the symptom left the UI while the defect stayed. Answering a race with the same 409 the code reaches twelve steps earlier is not a new policy — it is the policy, applied on the one path that skipped it. The rollback matters as much as the status: a poisoned session would break the next request on that worker |
| DR-46 | 2026-10-15 | The database driver is named **explicitly** in the URL (`postgresql+psycopg2://`) **and** SQLAlchemy is pinned — two defences for one failure | Either alone leaves the hole open, and they fail in different directions. The pin stops a version bump rerouting the dialect, but cannot help if the `DATABASE_URL` itself names a different driver — and that URL comes from the Neon integration, not this repo, so its suffix cannot be assumed. The explicit suffix stops the URL deciding, but cannot help if a future SQLAlchemy drops the psycopg2 dialect. Belt and braces is the right call for a line whose failure mode is every route returning 500 before any route exists |
| DR-47 | 2026-10-15 | The import-time bootstrap is **non-fatal**, and `/healthz` reports what failed | Not an exercise in hiding errors — a database that cannot be reached is a real failure and most pages genuinely cannot work without one. But "most pages" is not "the login page", and `/login` needs no database to render. An operator who can load a page and read `{"ok":false,"problems":[...]}` is in a completely different position from one facing an opaque `FUNCTION_INVOCATION_FAILED` on every URL — which is exactly the position this project was in for a day. The errors are collected as a **list**, because when the database is down all three steps fail and the *first* is the informative one |
| DR-48 | 2026-10-15 | Read the traceback before forming a hypothesis, and never describe an untested inference as the fix | Written as a decision because the incident produced three wrong guesses in a row — `SECRET_KEY`, then Neon being unreachable, then the new application code — and `main` was reverted and pushed on the third while it was being called the fix. The actual cause was in the Runtime Log the whole time, one click away, and named itself in two lines. The revert was reversible and cost nothing permanent, but it removed the project record and every deployed feature for no benefit. Reproducing a plausible failure locally is not evidence that it is *this* failure |
| DR-49 | 2026-10-21 | To bring a fix from `main` onto `stage-1-5-work`, **cherry-pick the fix; do not merge the branch** — and never trust a merge's exit code without reading the resulting tree | `main` contains a revert of Stage 1–5, so a merge treats "the stages are gone" as the most recent decision on those files and produces a tree with the boot fix and no Stage 1–5 code at all. Git reported success. Nothing about the command's output or exit status distinguished it from a correct merge; only `git diff` against the branch tip did. The correct mental model is that a merge combines *histories*, and a history that records a deletion carries that intent forward, whereas a cherry-pick replays a *diff* and cannot. This is the same class of error as DR-48 — a plausible action taken without checking what it actually produced |
| DR-50 | 2026-10-21 | The client test harnesses normalise the template to LF before any marker extraction | `core.autocrlf=true` means a Windows checkout differs from the committed blob in line endings only, and every multi-line marker in these suites is written with `"\n"`. The symptom was not a line-ending error but *"pre-fix reversal found nothing to reverse"* — which reads as "the fix is missing from the source" and invites someone to go looking for a defect that is not there. Content was first verified byte-identical modulo line endings (`md5sum` matched after `tr -d '\r'`), so the change is to the harness, not the application. Normalising on read makes extraction independent of checkout settings; the alternative — writing every marker with `\r?\n` — would have to be remembered on every future edit |
| DR-51 | 2026-10-21 | `SECRET_KEY` placeholders are refused by an explicit **set** of every public value, and a test reads `.env.example` to keep the set honest | The guard's comment claimed to refuse "the value shipped in `.env.example`" while naming a different string, so the value actually published in this repository passed straight through — into a key that signs both the session cookie and the rotating QR token. A single constant cannot express "all known-public values", and reading `.env.example` at import is not an option because that file is not deployed and a missing-file fallback would empty the guard silently. The explicit set plus `boot_config_test.py` reading what `.env.example` actually ships means the two cannot drift again without a test failing — which is what makes this a fix rather than a second comment that can rot |

---

## 7. Operating notes

Facts about this environment that have already cost time.

- **The C: drive runs at under 1 GB free and has hit exactly zero once.** On
  2026-09-26 it reached 0 bytes on a 119 GB volume and *source-file* writes began
  failing with `ENOSPC`, not just test output. Reclaimed 1,034 MB from
  `AppData\Local\npm-cache` (nothing here uses npm). Measured again 2026-09-30:
  **0.90 GB free** — so the reclaim held, and that is all it did. A further
  6.3 GB sits in `C:\Windows\SoftwareDistribution\Download` and needs an
  **elevated** shell — 43,437 items are access-denied otherwise:
  `Stop-Service wuauserv,bits; Remove-Item "C:\Windows\SoftwareDistribution\Download\*" -Recurse -Force; Start-Service wuauserv,bits`.
- **Scratch files are fine at this headroom; the earlier heredoc-only rule was
  over-broad.** *Corrected 2026-09-30.* This note previously said to write
  throwaway test scripts only through a stdin heredoc (`python - <<'EOF'`) and
  never to scratch files. That was a correct response to a 0-byte drive and a
  wrong generalisation: with ~0.9 GB free, five real script files under `%TEMP%`
  (`attendx_reg4`, `attendx_suite4`, `attendx_adv4`, `attendx_reg89`,
  `attendx_f8`) were written, edited, re-run and `sed`-patched repeatedly with
  no failure — and being editable is what let a false-positive assertion get
  *fixed* rather than worked around. **Write scratch files to `%TEMP%`, never
  into the repo** (they show up in `git status` and get committed by accident),
  and re-check free space before writing anything large.
- **Keep heredocs small if you use one at all.** A ~180-line suite piped to
  `python -` failed twice with `unexpected EOF while looking for matching '`
  while a 2-line probe succeeded. The cause is command length, not quoting:
  truncation lands inside a quote. This is the reason to prefer a real file, not
  a reason to split a heredoc.
- **Never run `app.py` and `run_student.py` together** — both bind port 5000.
  The second fails with `OSError: [WinError 10048]`. `run_all.py` pre-checks all
  three ports and exits cleanly rather than half-starting.
- **Vercel env vars are read at build time.** Changing one without redeploying
  changes nothing.
- **Vercel's cwd is the project root**, not `attendx_system/`. The entrypoint is
  loaded by file path, so its own directory is not on `sys.path` — hence the
  explicit `sys.path` insertion before `import config`, and the absolute
  `template_folder`.
- **Client IP on Vercel** must come from `x-vercel-forwarded-for` or
  `x-real-ip`, which the edge sets. Raw `X-Forwarded-For` is client-injectable
  and must not be trusted.
- **A virtual-clock suite can pass vacuously in two distinct ways, and the F1–F6
  suites hit both.** Recorded because a green run is exactly what each produced
  while testing nothing.
  - *Reading the clock after the drain loop.* The GPS suite captured each
    promise's settle time by reading `NOW` after draining all timers — which is
    always the horizon (30000/60000/90000ms), never the settle time. Every
    timing assertion passed on a number that could not have been wrong. The fix
    is to capture `at: NOW` **inside** the `.then`/`.catch` callback, at the
    instant the promise settles.
  - *Awaiting a clock-driven promise.* The poll suite `await`ed a capture
    directly. A promise that only resolves when the virtual clock is advanced by
    `tick()` never resolves under a bare `await` — the real event loop empties,
    Node exits 0, and the suite prints half its output and reports success. The
    fix is start-then-tick-then-await (`const p = fn(); await tick(…); await p`),
    plus a `process.on("exit")` guard that fails the run if the end-of-suite
    sentinel was never reached. A suite that exits 0 without printing its
    summary is now a failure, not a pass.
- **The deployment baseline is pinned as the `deploy-baseline` tag on GitHub**
  (annotated, at `15dc187`, pushed 2026-10-11). It exists because the repo has
  exactly **one** remote and both the working copy and the deploy-only clone
  point at it — so before the tag, the only thing marking the deploy state on
  GitHub was `main` *happening* to sit there, and a single push of Stage 1–5
  would have erased that marker. The tag is independent of `main`:
  `.../archive/refs/tags/deploy-baseline.zip` keeps returning the deploy-only
  state forever. `docs/verification/schema_upgrade_test.py` depends on a clone
  of it, so this is load-bearing for the test suite, not just for the record.
- **A multi-agent review can burn hours and hundreds of thousands of tokens and
  still return nothing — check the journal, never the return value.** Stage 5's
  review (`wf_d89a1d51-13d`) ran 2 hours 20 minutes and 649k subagent tokens
  before all four agents failed (3× HTTP 402, 1× DNS), and it returned
  `{"raised": 0}` — indistinguishable from a clean review if you read only the
  result. The journal is the ground truth: four `failed` lines, zero `result`
  lines. **A review's verdict is only meaningful if `journal.jsonl` contains at
  least one `result` line**, and D11 exists because that check was skipped once
  already.
- **The disk is still at the edge: 589 MB free on the 119 GB C: volume
  (2026-10-06), 100% by the OS's rounding.** Enough for the source edits and the
  five `docs/verification/` suites, which are kilobytes. The 6.3 GB in
  `C:\Windows\SoftwareDistribution\Download` (elevated-shell reclaim, noted
  above) is still the next lever if a larger write is needed.
