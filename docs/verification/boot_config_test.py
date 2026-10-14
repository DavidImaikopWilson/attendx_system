"""
Boot-path test — can the app start in a Vercel-like environment?

This is the test whose absence let a total production outage through. Every
other suite here imports `app` with a SQLite `DATABASE_URL` and no `VERCEL`
flag, which is the one environment where none of the production fail-fast
guards are active. So five stages of green tests said nothing about whether the
thing could *boot* on Vercel.

It tests the config layer in isolation, in a subprocess per scenario so each
gets a clean import, and asserts which environments are fatal and which are
survivable. It deliberately does NOT import `app` — that needs a live Postgres
(D7/D12 explain why that cannot run here). Config is where the import-time
`raise`s live, so it is where the boot either succeeds or dies.

Run it before any push that touches config.py or the import-time bootstrap.

Usage:  python docs/verification/boot_config_test.py <repo_root>
"""

import os
import re
import subprocess
import sys

REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
APP_DIR = os.path.join(REPO, "attendx_system")
CFG = os.path.join(APP_DIR, "config.py")
ENV_EXAMPLE = os.path.join(APP_DIR, ".env.example")

BAD = []
CHECKS = 0


def chk(label, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if not cond:
        BAD.append(f"{label}: {detail}" if detail else label)


GOOD_KEY = "a" * 64
GOOD_PG = "postgresql://u:p@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require"


def boot_config(env_overrides):
    """
    Import config.py in a clean subprocess under the given environment.
    Returns (ok, message). ok=False means it raised at import — which in
    production means every route returns 500, because app.py imports config.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("VERCEL", "SECRET_KEY", "DATABASE_URL")}
    env["PYTHONIOENCODING"] = "utf-8"
    for k, v in env_overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "try:\n"
        "    import config\n"
        "    print('BOOT_OK')\n"
        "except BaseException as e:\n"
        "    print('BOOT_FAIL:' + type(e).__name__ + ':' + str(e).replace(chr(10), ' ')[:160])\n"
    ) % APP_DIR
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    if "BOOT_OK" in out:
        return True, "ok"
    m = re.search(r"BOOT_FAIL:(.*)", out)
    return False, (m.group(1).strip() if m else out.strip()[:160])


# ── 1. local development must always boot ────────────────────────
# No VERCEL flag: a developer with nothing configured gets a working app, which
# is the entire reason the placeholder exists.
ok, msg = boot_config({"VERCEL": None, "SECRET_KEY": None, "DATABASE_URL": None})
print(f"local, nothing set            -> {'BOOTS' if ok else 'FAILS'}  {msg if not ok else ''}")
chk("local dev boots with nothing configured", ok, msg)

# ── 2. production with a missing SECRET_KEY is FATAL ─────────────
# This is the outage. os.environ.get falls back to the guarded placeholder, so
# an unset variable is indistinguishable from the placeholder and raises.
# Recorded as a test because the same variable was HARMLESS before DR-33: the
# deploy that worked and the deploy that did not can have identical env.
ok, msg = boot_config({"VERCEL": "1", "SECRET_KEY": None, "DATABASE_URL": GOOD_PG})
print(f"VERCEL=1, SECRET_KEY unset   -> {'BOOTS' if ok else 'FAILS'}  {msg if not ok else ''}")
chk("an unset SECRET_KEY refuses to boot on Vercel", not ok,
    "it booted - DR-33's guard is not active")
chk("and says SECRET_KEY in the message", (not ok) and "SECRET_KEY" in msg, msg)

for label, val in (("blank", ""), ("whitespace", "   ")):
    ok, msg = boot_config({"VERCEL": "1", "SECRET_KEY": val, "DATABASE_URL": GOOD_PG})
    print(f"VERCEL=1, SECRET_KEY {label:10s} -> {'BOOTS' if ok else 'FAILS'}")
    chk(f"a {label} SECRET_KEY refuses to boot", not ok)

# ── 3. every PUBLICLY KNOWN placeholder must be refused ──────────
# The point of DR-33 is that a known signing key forges admin cookies and QR
# tokens. So the guard has to cover the values that are actually public -- above
# all the one this repo ships in .env.example, which is the single most likely
# value for someone to paste into Vercel.
shipped = None
if os.path.isfile(ENV_EXAMPLE):
    m = re.search(r"^SECRET_KEY=(.*)$", open(ENV_EXAMPLE, encoding="utf-8").read(), re.M)
    if m:
        shipped = m.group(1).strip()
print(f"\n.env.example ships SECRET_KEY = {shipped!r}")

guarded = None
m = re.search(r'_PLACEHOLDER_SECRET_KEY\s*=\s*"([^"]+)"', open(CFG, encoding="utf-8").read())
if m:
    guarded = m.group(1)
print(f"config guards              = {guarded!r}")

chk("the shipped placeholder is discoverable", shipped is not None)
if shipped:
    ok, msg = boot_config({"VERCEL": "1", "SECRET_KEY": shipped,
                           "DATABASE_URL": GOOD_PG})
    print(f"VERCEL=1, key from .env.example -> {'BOOTS' if ok else 'FAILS'}")
    chk("THE SHIPPED .env.example KEY MUST BE REFUSED", not ok,
        "it booted - a publicly-known key is accepted in production, which is "
        "exactly what DR-33 exists to prevent")

if guarded:
    ok, msg = boot_config({"VERCEL": "1", "SECRET_KEY": guarded,
                           "DATABASE_URL": GOOD_PG})
    print(f"VERCEL=1, key config guards     -> {'BOOTS' if ok else 'FAILS'}")
    chk("the guarded placeholder is refused", not ok)

# ── 4. a real key plus a real Postgres URL must boot ─────────────
ok, msg = boot_config({"VERCEL": "1", "SECRET_KEY": GOOD_KEY,
                       "DATABASE_URL": GOOD_PG})
print(f"\nVERCEL=1, good key + good PG -> {'BOOTS' if ok else 'FAILS'}  {msg if not ok else ''}")
chk("a correctly configured production env boots", ok, msg)

# ── 5. the DATABASE_URL guards still bite ────────────────────────
for label, url in (
    ("sqlite",            "sqlite:///attendance.db"),
    ("localhost postgres", "postgresql://u:p@localhost/db"),
    ("unset",             None),
):
    ok, msg = boot_config({"VERCEL": "1", "SECRET_KEY": GOOD_KEY,
                           "DATABASE_URL": url})
    print(f"VERCEL=1, DATABASE_URL {label:18s} -> {'BOOTS' if ok else 'FAILS'}")
    chk(f"{label} DATABASE_URL is refused on Vercel", not ok)

print(f"\nchecks run: {CHECKS}")
print("BOOT BAD: " + ("\n  " + "\n  ".join(BAD) if BAD else "[] -- all pass"))
sys.exit(1 if BAD else 0)
