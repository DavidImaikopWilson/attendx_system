"""
Test for `reported_uncertainty` in attendx_system/app.py (finding F5).

F5: both lecturer read paths served `round(r.accuracy, 1)` -- the handset's own
self-reported accuracy -- under the key `accuracy_m`, while the student's own
success response serves `acc_eff` under that same key name. So the one person
asked to REVIEW an AMBIGUOUS row was shown the single quantity robust_fix and
geofence_decision both exist to refuse. A stationary phone replaying one WiFi
fingerprint self-reports a confident 12m while the observed scatter says 80m;
the lecturer sees "±12m" next to a flagged row, concludes a 12-metre fix cannot
possibly be ambiguous, and dismisses the flag. Lecturer review of the AMBIGUOUS
band is the entire mechanism that makes the middle state honest.

The helper is extracted from the real app.py by AST rather than retyped, so the
test cannot drift from the code. Importing app.py is not an option here: it runs
db.create_all() at module import and needs a live DATABASE_URL.

Usage:  python docs/verification/app_reported_uncertainty_test.py attendx_system/app.py
"""

import ast
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "attendx_system/app.py"
NAME = "reported_uncertainty"

source = open(SRC, encoding="utf-8").read()
tree = ast.parse(source)

fn = next((n for n in tree.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == NAME), None)
if fn is None:
    raise SystemExit(f"FATAL: {NAME} not found at module level in {SRC} -- renamed or moved?")

ns: dict = {}
exec(compile(ast.Module(body=[fn], type_ignores=[]), SRC, "exec"), ns)
reported_uncertainty = ns[NAME]


class Row:
    """Only the two fields the helper reads."""
    def __init__(self, accuracy, acc_eff):
        self.accuracy = accuracy
        self.acc_eff = acc_eff


BAD = []
CHECKS = 0


def chk(label, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if not cond:
        BAD.append(f"{label}: {detail}" if detail else label)


# ── the case the finding is about ────────────────────────────────
# Eight fixes, one of which momentarily self-reports 12m, with scatter giving
# spread 80m. acc_eff = max(12, 80) = 80, so geofence_decision lands AMBIGUOUS.
r = Row(accuracy=12.0, acc_eff=80.0)
unc, device = reported_uncertainty(r)
chk("scatter binds the reported figure", unc == 80.0, f"got {unc}")
chk("and the device claim is kept alongside it", device == 12.0, f"got {device}")

# The teeth line. The pre-fix payload served r.accuracy under `accuracy_m`;
# post-fix serves this. If these two were ever equal the fix would be inert.
print(f"teeth: pre-fix served {round(r.accuracy, 1)}, post-fix serves {round(unc, 1)}")
chk("TEETH the fix changed the served number", round(unc, 1) != round(r.accuracy, 1),
    "pre-fix and post-fix agree, so this test proves nothing")

# ── the device figure binds ──────────────────────────────────────
# A genuinely coarse fix with tight scatter: acc_eff = max(45, 3) = 45. The
# device figure IS the uncertainty, so showing it twice is noise.
unc, device = reported_uncertainty(Row(accuracy=45.0, acc_eff=45.0))
chk("device binds", unc == 45.0, f"got {unc}")
chk("no second figure when they agree", device is None, f"got {device}")

# ── pre-Stage-1 rows: NULL means 'does not know' ─────────────────
# acc_eff is NULL on every row written before Stage 1. Falling back to the
# device figure never claims more than the row supports: with one sample,
# spread is 0.0 and acc_eff would have been max(acc, 0.0) == acc anyway.
unc, device = reported_uncertainty(Row(accuracy=33.0, acc_eff=None))
chk("legacy row falls back to the device figure", unc == 33.0, f"got {unc}")
chk("and does not invent a second one", device is None, f"got {device}")

# ── manual_mark rows ─────────────────────────────────────────────
# manual_mark writes accuracy = 0.0, acc_eff = None. The helper must leave
# these behaving exactly as they did before the fix.
unc, device = reported_uncertainty(Row(accuracy=0.0, acc_eff=None))
chk("manual row reports 0.0", unc == 0.0, f"got {unc}")
chk("manual row shows no device claim", device is None, f"got {device}")

# ── both NULL ────────────────────────────────────────────────────
# The payload does `round(unc, 1)` unconditionally, so a None here would raise
# a TypeError inside the lecturer's session view and 500 the whole page.
unc, device = reported_uncertainty(Row(accuracy=None, acc_eff=None))
chk("both NULL yields a roundable number", isinstance(unc, float), f"got {unc!r}")
chk("both NULL yields 0.0", unc == 0.0, f"got {unc}")
chk("both NULL shows no device claim", device is None, f"got {device}")
try:
    round(unc, 1)
    chk("both NULL survives round()", True)
except TypeError as e:
    chk("both NULL survives round()", False, str(e))

# ── rounding noise ───────────────────────────────────────────────
# Below the 0.05 threshold the two figures are the same number to one decimal
# place, so a second column would show "±30.0m (device ±30.0m)".
unc, device = reported_uncertainty(Row(accuracy=30.0, acc_eff=30.02))
chk("sub-0.05 difference is suppressed", device is None, f"got {device}")
unc, device = reported_uncertainty(Row(accuracy=30.0, acc_eff=30.4))
chk("a visible difference is kept", device == 30.0, f"got {device}")

print(f"checks run: {CHECKS}")
print("F5 BAD: " + (repr(BAD) if BAD else "[] -- all pass"))
sys.exit(1 if BAD else 0)
