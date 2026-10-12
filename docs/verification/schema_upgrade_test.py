"""
Schema upgrade test — the local half of verification item #7.

The production risk is NOT a fresh database. On a fresh database
`db.create_all()` builds every table from the current models, so
`ensure_schema_compatibility()` finds every column present and executes zero
ALTERs. The risk is the *upgrade*: Neon already holds the schema as deployed at
commit 15dc187, `create_all()` is a no-op on existing tables, and
`ensure_schema_compatibility()` must then ALTER in sixteen new columns on the
first cold start after a push.

This test reproduces that exactly, using the real deployed code as the starting
point rather than a hand-written approximation:

  step 1  run the DEPLOY-BASELINE app (the 15dc187 clone) against a scratch
          SQLite file, so the file ends up with precisely the schema that is
          live in production today;
  step 2  run the CURRENT app against that same file, so create_all() is a
          no-op and ensure_schema_compatibility() has to do the upgrade;
  step 3  assert every expected column now exists, that the data written by
          step 1 survived, and that a second run is idempotent.

It cannot prove the Postgres behaviour — only Postgres can, and D7/D12 explain
why that cannot run on this machine. What it does prove is that the upgrade
path is exercised at all, which it previously never was.

Usage:  python docs/verification/schema_upgrade_test.py <repo_root> <baseline_repo_root>
"""

import os
import sqlite3
import subprocess
import sys
import tempfile

REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
BASELINE = os.path.abspath(
    sys.argv[2] if len(sys.argv) > 2
    else os.path.join(REPO, "..", "..", "attendx_deploy_only"))

DB = os.path.join(tempfile.gettempdir(), "attendx_upgrade_test.db")
URL = "sqlite:///" + DB.replace("\\", "/")

BAD = []
CHECKS = 0


def chk(label, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if not cond:
        BAD.append(f"{label}: {detail}" if detail else label)


def boot(app_dir, tag):
    """Import app.py from app_dir against the scratch DB, in its own process."""
    env = dict(os.environ)
    env["DATABASE_URL"] = URL
    env["SECRET_KEY"] = "test-only-not-the-placeholder-0123456789abcdef"
    env["PYTHONIOENCODING"] = "utf-8"
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import app;"
        "print('BOOTED')" % app_dir
    )
    r = subprocess.run([sys.executable, "-W", "ignore", "-c", code],
                       env=env, capture_output=True, text=True, timeout=180)
    ok = "BOOTED" in (r.stdout or "")
    if not ok:
        tail = ((r.stderr or "").strip().splitlines() or ["<no stderr>"])[-6:]
        print(f"  [{tag}] boot FAILED:")
        for line in tail:
            print("    " + line)
    return ok


def cols(table):
    con = sqlite3.connect(DB)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def table_names():
    con = sqlite3.connect(DB)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


# Every column the current models add beyond the deployed baseline.
EXPECTED = {
    "venues": ["building", "floor", "cal_lat", "cal_lng", "cal_radius",
               "cal_n", "cal_updated_at", "cal_review"],
    "attendance_records": ["sample_count", "spread_m", "acc_eff",
                           "verify_state", "confidence", "qr_verified",
                           "geo_state", "qr_trusted"],
}

if not os.path.isdir(BASELINE):
    raise SystemExit(f"FATAL: baseline repo not found at {BASELINE}\n"
                     f"Pass it as argv[2], or clone the deploy-baseline tag.")

baseline_app = os.path.join(BASELINE, "attendx_system")
current_app = os.path.join(REPO, "attendx_system")
for p in (baseline_app, current_app):
    if not os.path.isfile(os.path.join(p, "app.py")):
        raise SystemExit(f"FATAL: no app.py under {p}")

if os.path.exists(DB):
    os.remove(DB)

# ── step 1: the deployed schema, built by the deployed code ──────
print("step 1: booting the deploy-baseline app (15dc187) to build the live schema")
chk("baseline app boots", boot(baseline_app, "baseline"))
base_tables = table_names()
chk("baseline created the tables", "attendance_records" in base_tables,
    str(sorted(base_tables)))

base_venue_cols = cols("venues")
base_record_cols = cols("attendance_records")
print(f"  baseline venues:             {len(base_venue_cols)} columns")
print(f"  baseline attendance_records: {len(base_record_cols)} columns")

# Teeth: the baseline must genuinely LACK the new columns, or this test is
# measuring nothing. If the clone ever drifts forward, this fails loudly.
missing_at_baseline = 0
for table, wanted in EXPECTED.items():
    have = cols(table)
    for c in wanted:
        if c not in have:
            missing_at_baseline += 1
chk("TEETH baseline lacks all 16 new columns", missing_at_baseline == 16,
    f"only {missing_at_baseline} were missing - the baseline is not the deployed schema")

# Seed one row of real data so we can prove the upgrade preserves it.
con = sqlite3.connect(DB)
try:
    con.execute("INSERT INTO venues (name, latitude, longitude, radius) "
                "VALUES ('UpgradeHall', 6.5244, 3.3792, 50.0)")
    con.commit()
    vid = con.execute("SELECT id FROM venues WHERE name='UpgradeHall'").fetchone()[0]
finally:
    con.close()
chk("pre-upgrade row written", vid is not None)

# ── step 2: the current code upgrades that same file ─────────────
print("step 2: booting the CURRENT app against the same file (the upgrade)")
chk("current app boots against the old schema", boot(current_app, "current"))

# ── step 3: assert the upgrade happened and lost nothing ─────────
added = 0
for table, wanted in EXPECTED.items():
    have = cols(table)
    for c in wanted:
        present = c in have
        chk(f"{table}.{c} added", present)
        if present:
            added += 1
print(f"  columns added by the upgrade: {added}/16")

con = sqlite3.connect(DB)
try:
    row = con.execute("SELECT name, latitude, radius FROM venues "
                      "WHERE id=?", (vid,)).fetchone()
finally:
    con.close()
chk("the pre-upgrade row survived", row is not None and row[0] == "UpgradeHall",
    str(row))
chk("and its values are intact", row is not None and abs(row[2] - 50.0) < 1e-9,
    str(row))

# ── step 4: idempotency — a second cold start must be a no-op ────
print("step 4: booting again (idempotency)")
chk("current app boots a second time", boot(current_app, "current-again"))
for table, wanted in EXPECTED.items():
    have = cols(table)
    chk(f"{table} columns stable on re-run",
        all(c in have for c in wanted))

print(f"checks run: {CHECKS}")
print("UPGRADE BAD: " + (repr(BAD) if BAD else "[] -- all pass"))

try:
    os.remove(DB)
except OSError:
    pass
sys.exit(1 if BAD else 0)
