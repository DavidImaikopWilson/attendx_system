// Cross-layer test for the student dashboard's 30-second course poll.
//
// Findings under test (both confirmed against the source before fixing):
//
//   F3  The poll called renderCourses -> `grid.innerHTML = ...`, which detaches
//       every node in the grid. markAttendance holds `btn` across an 8-second
//       getGPS, and html5-qrcode holds the scanner container and its <video>.
//       A poll landing mid-capture therefore wrote progress into a detached
//       node while the VISIBLE replacement button sat enabled -- the student
//       tapped again, and of the two POSTs one returned 201 and the other 409,
//       so a student whose attendance WAS recorded was shown a failure. A poll
//       landing mid-scan orphaned a live MediaStream.
//
//   F4  buildCard hardcoded `Acquiring GPS...` with a waiting dot, and
//       prefetchGPS -- the only thing that ever wrote anything else -- ran
//       twice in the page's life. Thirty seconds after a student was told
//       "Location permission denied -- enable it for this site", the poll
//       replaced it with "Acquiring GPS..." for good.
//
// Rather than retype the logic, this file EXECUTES the real inline <script>
// lifted whole out of the template, against a scriptable DOM, a scriptable
// geolocation, a scriptable fetch and a virtual clock -- so a 30-second poll
// landing inside an 8-second capture is tested in microseconds.
//
// Every scenario is also run against a PRE variant, built by reversing the fix
// textually in that same extracted source (each reversal asserts it found what
// it was reversing, so it cannot silently become a no-op). A green suite proves
// nothing unless the old code goes red on the same inputs.
//
// Usage:  node docs/verification/student_dashboard_poll_test.js \
//              attendx_system/templates/student_dashboard.html

const fs = require("fs");

const TPL = process.argv[2];
if (!TPL) throw new Error("usage: node student_dashboard_poll_test.js <student_dashboard.html>");

// Normalise CRLF -> LF before anything else. This repo has core.autocrlf=true,
// so a Windows checkout writes the template with CRLF while the committed blob
// keeps LF. Every marker below that spans more than one line is written with
// "\n", so without this the extraction fails on a perfectly good file -- it
// threw "pre-fix reversal found nothing to reverse" after a `git checkout`
// rewrote the working tree, which looks exactly like the fix having vanished.
// It has not; the file is identical modulo line endings (verified by md5 on the
// normalised text). The line endings are irrelevant to execution, so
// normalising here costs nothing and makes the suite independent of checkout.
const src = fs.readFileSync(TPL, "utf8").replace(/\r\n/g, "\n");

// ── extract the real inline script ───────────────────────────────
const blk = src.indexOf("{% block scripts %}");
if (blk < 0) throw new Error("marker not found: {% block scripts %}");
const open = src.indexOf("<script>", blk);          // the inline one; the CDN tag has src=
const close = src.indexOf("</script>", open);
if (open < 0 || close < 0) throw new Error("inline <script> block not found");
const SCRIPT = src.slice(open + "<script>".length, close);

// If the template ever grows a Jinja expression inside this script the extract
// stops being executable JavaScript, and silently testing a broken parse would
// be worse than failing here.
const jinja = SCRIPT.match(/\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}/);
if (jinja) throw new Error("inline script now contains Jinja: " + jinja[0]);

// The fix introduced these. If any is renamed, fail loudly rather than pass a
// suite that is no longer pointed at anything.
for (const n of ["captureInFlight", "renderDeferred", "gpsStatus",
                 "renderBlocked", "resumeRender", "releaseCapture"]) {
  if (!SCRIPT.includes(n)) throw new Error(`${n} vanished from the template`);
}

// ── build the pre-fix variant by reversing the fix ───────────────
function reverse(text, from, to, what) {
  if (!text.includes(from)) throw new Error(`pre-fix reversal found nothing to reverse: ${what}`);
  return text.split(from).join(to);
}

let PRE = SCRIPT;
// F3(a) -- the poll's render guard
PRE = reverse(PRE,
  "  if (renderBlocked()) {\n    renderDeferred = true;\n    return;\n  }\n  renderDeferred = false;\n",
  "", "renderCourses guard");
// F3(b) -- the in-flight flag was never raised
PRE = reverse(PRE, "captureInFlight = true;", "captureInFlight = false;", "captureInFlight raise");
PRE = reverse(PRE, "if (captureInFlight) return;", "", "captureInFlight tap guard");
// F3(c) -- a rejected stop() stranded the scanned token
PRE = reverse(PRE, "scanner.stop().then(finishScan, finishScan);",
                   "scanner.stop().then(finishScan);", "stop() rejection handler");
// F4 -- the hardcoded indicator
PRE = reverse(PRE,
  '<span class="gps-dot ${gpsStatus.kind}" id="gpsDot-${c.course_id}"></span>',
  '<span class="gps-dot waiting" id="gpsDot-${c.course_id}"></span>', "buildCard dot");
PRE = reverse(PRE,
  '<span id="gpsText-${c.course_id}">${gpsStatus.text}</span>',
  '<span id="gpsText-${c.course_id}">Acquiring GPS...</span>', "buildCard text");

// ── virtual clock ────────────────────────────────────────────────
let NOW = 0, SEQ = 0;
let pending = new Map();
const setTimeout_  = (fn, ms) => { const id = ++SEQ; pending.set(id, {at: NOW + (ms || 0), fn}); return id; };
const setInterval_ = (fn, ms) => { const id = ++SEQ; pending.set(id, {at: NOW + ms, fn, every: ms}); return id; };
const clearTimer_  = (id) => { pending.delete(id); };

function advanceTo(t) {
  for (let guard = 0; guard < 100000; guard++) {
    let next = null;
    for (const [id, e] of pending) if (e.at <= t && (next === null || e.at < next[1].at)) next = [id, e];
    if (next === null) break;
    NOW = next[1].at;
    if (next[1].every) next[1].at = NOW + next[1].every; else pending.delete(next[0]);
    next[1].fn();
  }
  NOW = Math.max(NOW, t);
}

// Interleave microtask draining with clock steps, so the async bodies of
// loadCourses / markAttendance / handleScannedQr actually make progress.
async function tick(ms, step = 50) {
  const end = NOW + ms;
  let t = NOW;
  while (t < end) {
    t = Math.min(end, t + step);
    advanceTo(t);
    for (let k = 0; k < 8; k++) await Promise.resolve();
  }
}
const Date_ = { now: () => NOW };

// ── fake DOM ─────────────────────────────────────────────────────
// Only what the script touches. The point is element IDENTITY: a rebuilt grid
// hands out new objects, which is exactly the detachment the fix prevents.
function makeDom() {
  const byId = new Map();
  let born = 0;

  function mkEl(id) {
    const el = {
      id, _born: ++born, _html: "", textContent: "", className: "",
      disabled: false, style: {},
      classList: { add() {}, remove() {} },
      get innerHTML() { return this._html; },
      set innerHTML(v) { this._html = v; },
    };
    byId.set(id, el);
    return el;
  }

  // Elements outside the grid survive every render.
  for (const id of ["coursesGrid", "statTotal", "statActive", "statMarked", "toast", "gpsModal"]) mkEl(id);
  const grid = byId.get("coursesGrid");
  let gridIds = [];
  let renders = 0;

  Object.defineProperty(grid, "innerHTML", {
    get() { return this._html; },
    set(html) {
      this._html = html;
      renders++;                                          // the signal the tests read
      for (const id of gridIds) byId.delete(id);          // detach everything
      gridIds = [];
      for (const m of html.matchAll(/\bid="([^"]+)"/g)) { gridIds.push(m[1]); mkEl(m[1]); }
      // Seed the rendered values the assertions read back.
      for (const m of html.matchAll(/<span class="gps-dot ([^"]*)" id="(gpsDot-[^"]+)"><\/span>/g)) {
        byId.get(m[2]).className = `gps-dot ${m[1]}`;
      }
      for (const m of html.matchAll(/<span id="(gpsText-[^"]+)">([^<]*)<\/span>/g)) {
        byId.get(m[1]).textContent = m[2];
      }
      for (const m of html.matchAll(/<button[^>]*\bid="((?:scanBtn|markBtn)-[^"]+)"[^>]*>\s*([^<]*?)\s*<\/button>/g)) {
        const el = byId.get(m[1]);
        el.textContent = m[2];
        el._html = m[2];
      }
    },
  });

  const document_ = {
    getElementById: (id) => byId.get(id) || null,
    querySelectorAll: (sel) => {
      const m = /^\[id\^='([^']+)'\]$/.exec(sel);
      if (!m) throw new Error("fake DOM: unsupported selector " + sel);
      return [...byId.values()].filter(e => e.id.startsWith(m[1]));
    },
  };
  return { document: document_, byId, renders: () => renders };
}

// ── scriptable geolocation ───────────────────────────────────────
function makeGeo(script) {
  const state = { cleared: false };
  return {
    state,
    geo: {
      watchPosition(ok, fail) {
        for (const ev of script) setTimeout_(() => {
          if (state.cleared) return;
          if (ev.err) fail(ev.err);
          else ok({ coords: { latitude: ev.pos[0], longitude: ev.pos[1], accuracy: ev.pos[2] } });
        }, ev.t);
        return 1;
      },
      clearWatch() { state.cleared = true; },
    },
  };
}

// A good, varying signal: the capture runs its early-exit path and settles
// comfortably inside the 8s window.
const GOOD_FIXES = [];
for (let i = 0; i < 12; i++) GOOD_FIXES.push({ t: 300 + i * 600, pos: [6.5244 + i * 3e-6, 3.3792 + i * 2e-6, 9] });
const DENIED = [{ t: 200, err: { code: 1, message: "User denied Geolocation" } }];

// A signal that NEVER early-exits: every fix self-reports 30m, above
// GPS_GOOD_ACC_M = 20, so settled() is never satisfied and the capture runs the
// full 8-second window. This is what the capture-vs-poll test needs -- the
// capture must still be in flight when the 30s poll fires, which a fast good
// signal (settling at ~4.5s) cannot guarantee.
const SLOW_FIXES = [];
for (let i = 0; i < 20; i++) SLOW_FIXES.push({ t: 300 + i * 500, pos: [6.5244 + i * 1e-6, 3.3792, 30] });

// ── harness ──────────────────────────────────────────────────────
function boot(body, opts = {}) {
  NOW = 0; SEQ = 0; pending = new Map();
  const { document, byId, renders } = makeDom();
  const listeners = {};
  const calls = { mark: [], courses: 0 };
  let marked = false;

  const window_ = {
    addEventListener: (type, fn) => { listeners[type] = fn; },
    isSecureContext: true,
    location: { protocol: "https:", hostname: "example.org" },
  };

  const permState = opts.permission || "granted";
  const navigator_ = {
    geolocation: makeGeo(opts.fixes || GOOD_FIXES).geo,
    permissions: { query: () => Promise.resolve({ state: permState, addEventListener() {} }) },
  };

  const COURSE = () => ({
    course_id: 1, course_code: "CSC401", course_name: "Compilers", lecturer_name: "Dr Ada",
    session: { id: 10, status: "OPEN", seconds_remaining: 900, already_marked: marked },
  });

  async function fetch_(url, init) {
    if (url === "/api/student/courses") {
      calls.courses++;
      return { ok: true, json: async () => ({ courses: [COURSE()] }) };
    }
    if (url === "/api/student/attendance/mark") {
      const payload = JSON.parse(init.body);
      calls.mark.push(payload);
      if (calls.mark.length > 1) {
        // Exactly what the server does to the loser of a double submission.
        return { ok: false, status: 409,
                 json: async () => ({ error: "You have already submitted attendance for this session" }) };
      }
      marked = true;
      return { ok: true, status: 201, json: async () => ({ distance_m: 12.3 }) };
    }
    throw new Error("fake fetch: unexpected " + url);
  }

  const Qr = class {
    constructor(id) { this.id = id; this.started = false; Qr.last = this; }
    async start(cam, cfg, onDecode) { this.started = true; this.onDecode = onDecode; }
    async stop() {
      if (Qr.stopFails) throw new Error("scanner container not found");
      this.started = false;
    }
  };
  Qr.stopFails = !!opts.stopFails;
  Qr.last = null;

  const quiet = { log() {}, warn() {}, error() {} };

  const api = new Function(
    "window", "document", "navigator", "fetch", "setTimeout", "clearTimeout",
    "setInterval", "clearInterval", "Date", "Html5Qrcode", "URL", "console",
    body + "\nreturn { markAttendance, startScanner, handleScannedQr, loadCourses, renderCourses };"
  )(window_, document, navigator_, fetch_, setTimeout_, clearTimer_,
    setInterval_, clearTimer_, Date_, Qr, URL, quiet);

  return { api, document, byId, calls, listeners, Qr, renders, boot: () => listeners.DOMContentLoaded() };
}

const BAD = [];
let CHECKS = 0;
let FINISHED = false;
function chk(label, cond, detail) {
  CHECKS++;
  if (!cond) BAD.push(label + (detail ? ": " + detail : ""));
}

// A rejected promise with no handler kills Node outright, which is itself one
// of the behaviours under test (pre-fix, a rejected scanner.stop() had no
// rejection path). Record them instead of dying so the suite can assert on it.
let REJECTIONS = [];
process.on("unhandledRejection", (e) => { REJECTIONS.push(String(e && e.message || e)); });

// Awaiting a promise that only settles when the VIRTUAL clock advances empties
// the event loop, and Node then exits 0 having printed nothing further -- a
// half-run suite that looks like a pass. Never `await` a capture directly:
// start it, tick, then await. This guard makes the mistake fail loudly.
process.on("exit", (code) => {
  if (!FINISHED && code === 0) {
    console.error("\nFATAL: the suite exited before finishing -- almost certainly an\n" +
                  "       `await` on a promise that needs tick() to advance the clock.");
    process.exitCode = 1;
  }
});

const el = (h, id) => h.document.getElementById(id);
const txt = (h, id) => { const e = el(h, id); return e ? e.textContent : "<absent>"; };

// Yield to the REAL event loop. tick() only drains microtasks, but Node emits
// 'unhandledRejection' on a later macrotask -- without this the recorder is
// still empty when the assertion reads it.
const realTick = () => new Promise(r => setImmediate(() => setImmediate(r)));

// Bring the page up and settle the first render.
async function started(body, opts) {
  const h = boot(body, opts);
  h.boot();
  await tick(200);
  return h;
}

// ── scenarios ────────────────────────────────────────────────────
// A poll fires every 30s from boot. Tapping at t=25s against a signal that
// runs the full 8s window puts the poll at t=30s squarely mid-capture, and the
// second tap just after it -- the ~27%-of-attempts case, and the exact moment
// the student sees an apparently-idle replacement button and taps again.
const TAP_AT = 25000;

async function capturePath(body, tag, expect) {
  const h = await started(body, { fixes: SLOW_FIXES });
  await tick(TAP_AT - NOW);
  const before = el(h, "markBtn-1");
  const p1 = h.api.markAttendance(1, 10).catch(() => {});
  await tick(5500);                       // NOW~30500: poll has fired, still capturing

  const during = el(h, "markBtn-1");
  const sameNode = during === before;
  const enabledMid = during ? during.disabled === false : null;

  // The second tap lands HERE -- while the first capture is still in flight,
  // on whatever button is now on screen. This is the double-submission.
  const p2 = (during ? h.api.markAttendance(1, 10) : Promise.resolve()).catch(() => {});
  await tick(15000);                      // both captures + POSTs + reloads settle
  await p2;
  await p1;

  const posts = h.calls.mark.length;
  console.log(`${tag.padEnd(26)} -> node kept: ${String(sameNode).padEnd(5)}  ` +
              `enabled mid-capture: ${String(enabledMid).padEnd(5)}  POSTs: ${posts}`);
  expect({ sameNode, enabledMid, posts, h });
  return h;
}

async function gpsStatusPath(body, tag, expect) {
  const h = await started(body, { permission: "denied" });
  const first = txt(h, "gpsText-1");
  await tick(31000);                      // one poll
  const after = txt(h, "gpsText-1");
  const dot = el(h, "gpsDot-1") ? el(h, "gpsDot-1").className : "<absent>";
  console.log(`${tag.padEnd(26)} -> before: "${first}"  after poll: "${after}" (${dot})`);
  expect({ first, after, dot });
}

async function scannerPath(body, tag, expect) {
  const h = await started(body);
  await tick(5000);
  await h.api.startScanner(1, 10);
  await tick(200);
  const boxBefore = el(h, "scanner-1");
  await tick(31000);                      // a poll lands while the camera is up
  const boxAfter = el(h, "scanner-1");
  const label = txt(h, "scanBtn-1");
  const cameraLive = h.Qr.last ? h.Qr.last.started : null;
  console.log(`${tag.padEnd(26)} -> container kept: ${String(boxBefore === boxAfter).padEnd(5)}  ` +
              `button: "${label}"  camera live: ${cameraLive}`);
  expect({ kept: boxBefore === boxAfter, label, cameraLive, h });
}

async function scanSubmitPath(body, tag, opts, expect) {
  const h = await started(body, opts);
  await tick(5000);
  await h.api.startScanner(1, 10);
  await tick(200);
  h.Qr.last.onDecode("https://x.test/attend?t=TOKEN123&session=10");
  await tick(20000);                      // decode -> 8s capture -> POST -> reload
  const posts = h.calls.mark;
  const tokens = posts.filter(p => p.qr_token === "TOKEN123").length;

  // Prove the in-flight flag was RELEASED: a later poll must rebuild the grid.
  // Count renders rather than compare element identity -- a marked card has no
  // ids at all, so identity cannot tell "not rebuilt" from "rebuilt empty".
  const rendersBefore = h.renders();
  await tick(31000);
  const rebuilt = h.renders() > rendersBefore;

  console.log(`${tag.padEnd(26)} -> QR POSTs: ${tokens}  renders resumed: ${rebuilt}`);
  expect({ tokens, rebuilt, h });
}

(async () => {
  console.log("-- post-fix --");

  await capturePath(SCRIPT, "T1 capture vs poll", ({ sameNode, enabledMid, posts }) => {
    chk("T1 the poll did not detach the button", sameNode === true);
    chk("T1 the button stayed disabled mid-capture", enabledMid === false, "enabled=" + enabledMid);
    chk("T1 exactly one POST", posts === 1, "posts=" + posts);
  });

  await gpsStatusPath(SCRIPT, "T2 gps status vs poll", ({ first, after, dot }) => {
    chk("T2 the denied message was shown", /permission denied/i.test(first), first);
    chk("T2 and survived the poll", after === first, `"${after}"`);
    chk("T2 the dot stayed in the error state", dot === "gps-dot error", dot);
  });

  await scannerPath(SCRIPT, "T3 scanner vs poll", ({ kept, label, cameraLive }) => {
    chk("T3 the scanner container survived", kept === true);
    chk("T3 the button still reads Stop Scanner", /Stop Scanner/.test(label), label);
    chk("T3 the camera is still live and still shown", cameraLive === true);
  });

  await scanSubmitPath(SCRIPT, "T4 scan -> submit", {}, ({ tokens, rebuilt }) => {
    chk("T4 the scanned token was submitted", tokens === 1, "tokens=" + tokens);
    chk("T4 renders resumed afterwards", rebuilt === true);
  });

  // The whole QR path hangs off scanner.stop(). html5-qrcode rejects it when
  // the container is gone, and this is the only path that can succeed while
  // every venue is uncalibrated -- so a rejection must not eat the token.
  await scanSubmitPath(SCRIPT, "T5 scan, stop() rejects", { stopFails: true }, ({ tokens, rebuilt }) => {
    chk("T5 a rejected stop() still submits", tokens === 1, "tokens=" + tokens);
    chk("T5 renders resumed afterwards", rebuilt === true);
  });

  // Regression guard on the new flag itself: an early return must release it,
  // or the page stops re-rendering for good.
  {
    const h = await started(SCRIPT);
    await tick(5000);
    await h.api.startScanner(1, 10);
    await tick(200);
    h.Qr.last.onDecode("not-a-url");
    await tick(2000);
    const rendersBefore = h.renders();
    await tick(31000);
    const resumed = h.renders() > rendersBefore;
    console.log(`${"T6 malformed QR".padEnd(26)} -> renders resumed: ${resumed}`);
    chk("T6 a malformed QR releases the flag", resumed === true);
  }

  // A denial reached through the button must stick on the indicator.
  {
    const h = await started(SCRIPT, { fixes: DENIED });
    await tick(5000);
    const p = h.api.markAttendance(1, 10).catch(() => {});
    await tick(2000);
    await p;
    const immediate = txt(h, "gpsText-1");
    await tick(31000);
    const after = txt(h, "gpsText-1");
    console.log(`${"T7 denial via the button".padEnd(26)} -> "${immediate}" / after poll "${after}"`);
    chk("T7 the indicator shows the denial", /permission denied/i.test(immediate), immediate);
    chk("T7 and it survives the poll", after === immediate, after);
  }

  console.log("");
  console.log("-- pre-fix contrast: every line below MUST be the broken behaviour --");

  await capturePath(PRE, "P1 capture vs poll", ({ sameNode, enabledMid, posts }) => {
    chk("TEETH P1 old code detached the button", sameNode === false,
        "it did not, so T1 proves nothing");
    chk("TEETH P1 and left the replacement enabled", enabledMid === true, "enabled=" + enabledMid);
    chk("TEETH P1 so the second tap produced a second POST", posts === 2, "posts=" + posts);
  });

  // The measured pre-fix behaviour is WORSE than the finding described. The
  // finding said the denial survived for 30 seconds and was then lost to the
  // poll. In fact prefetchGPS's permissions.query resolves one microtask ahead
  // of loadCourses' fetch+json, so setGpsStatusAll writes the denial into the
  // DOM and the very first render overwrites it. The student never sees the
  // actionable string at all -- not for 30 seconds, not once.
  await gpsStatusPath(PRE, "P2 gps status vs poll", ({ first, after, dot }) => {
    chk("TEETH P2 old code never showed the denial", !/permission denied/i.test(first), first);
    chk("TEETH P2 the hardcoded label won", after === "Acquiring GPS...", `"${after}"`);
    chk("TEETH P2 and the dot stayed waiting", dot === "gps-dot waiting", dot);
  });

  await scannerPath(PRE, "P3 scanner vs poll", ({ kept, label, cameraLive }) => {
    chk("TEETH P3 old code destroyed the container", kept === false);
    chk("TEETH P3 and the button lied about the state", /Scan QR Code/.test(label), label);
    chk("TEETH P3 while the camera was still running", cameraLive === true,
        "the orphaned-MediaStream case did not reproduce");
  });

  REJECTIONS = [];
  await scanSubmitPath(PRE, "P5 scan, stop() rejects", { stopFails: true }, ({ tokens }) => {
    chk("TEETH P5 old code stranded the scanned token", tokens === 0, "tokens=" + tokens);
  });
  // `.then(finishScan)` with no rejection handler: the rejection was unhandled,
  // which in Node terminates the process and in a browser surfaces as an
  // uncaught error in the console while the student just sees nothing happen.
  await realTick();
  chk("TEETH P5 and left the rejection unhandled",
      REJECTIONS.some(m => /scanner container not found/.test(m)),
      JSON.stringify(REJECTIONS));

  // The post-fix path must NOT produce one. Re-run it last, with the recorder
  // clear, so a regression here cannot hide behind the pre-fix rejection.
  REJECTIONS = [];
  await scanSubmitPath(SCRIPT, "P6 post-fix, stop() rejects", { stopFails: true }, ({ tokens }) => {
    chk("P6 post-fix still submits", tokens === 1, "tokens=" + tokens);
  });
  await realTick();
  chk("P6 and handles the rejection", REJECTIONS.length === 0, JSON.stringify(REJECTIONS));

  console.log("");
  console.log("checks run: " + CHECKS);
  console.log("POLL BAD: " + (BAD.length ? JSON.stringify(BAD, null, 1) : "[] -- all pass"));
  FINISHED = true;
  process.exitCode = BAD.length ? 1 : 0;
})();
