// Cross-layer test for getGPS in student_dashboard.html.
//
// It executes the REAL function, lifted verbatim out of the template, against a
// scriptable navigator.geolocation and a virtual clock -- so a 45-second
// permission timeout is tested in microseconds. Extraction is by marker, so if
// getGPS or the constants block is renamed this file throws instead of quietly
// testing nothing.
//
// Every scenario is also run against the pre-fix source (PRE below). A green
// suite proves nothing unless the old code goes red on the same inputs.

const fs = require("fs");
const path = require("path");

const TPL = process.argv[2];
// CRLF -> LF, for the same reason as the poll suite: core.autocrlf=true means a
// Windows checkout differs from the committed blob in line endings only, and
// every marker here is written with "\n". Normalising makes extraction
// independent of how the file was checked out.
const src = fs.readFileSync(TPL, "utf8").replace(/\r\n/g, "\n");

function grab(a, b) {
  const i = src.indexOf(a);
  if (i < 0) throw new Error(`marker not found: ${a}`);
  const j = src.indexOf(b, i);
  if (j < 0) throw new Error(`end marker not found after: ${a}`);
  return src.slice(i, j);
}

const RULE    = "───";
const helpers = grab("function geolocationNeedsSecureContext", "// " + RULE);
const consts  = grab("const GPS_COLLECT_MS", "function getGPS");
const getgps  = grab("function getGPS", "// " + RULE);

for (const n of ["GPS_EARLY_MIN_MS", "GPS_EARLY_AGREE_M", "GPS_PERMISSION_MAX_MS"]) {
  if (!consts.includes(n)) throw new Error(`constant ${n} vanished from the template`);
  if (!getgps.includes(n)) throw new Error(`${n} is declared but NOT CONSUMED by getGPS`);
}

// ── the code as it stood before this fix ─────────────────────────
const PRE = `
function getGPS(onProgress) {
  return new Promise((resolve, reject) => {
    const requestPosition = () => {
      const samples = [];
      let watchId = null, timer = null, done = false;
      const finish = (errMessage) => {
        if (done) return;
        done = true;
        if (timer !== null) clearTimeout(timer);
        if (watchId !== null) navigator.geolocation.clearWatch(watchId);
        if (samples.length === 0) {
          reject(new Error(errMessage || "Could not get a GPS reading. Please try again."));
          return;
        }
        const best = samples.reduce((a, b) => (b.acc < a.acc ? b : a));
        resolve({lat: best.lat, lng: best.lng, accuracy: best.acc, samples: samples});
      };
      watchId = navigator.geolocation.watchPosition(
        pos => {
          samples.push({lat: pos.coords.latitude, lng: pos.coords.longitude, acc: pos.coords.accuracy});
          const settled = samples.length >= GPS_EARLY_MIN_N &&
                          samples.slice(-GPS_EARLY_MIN_N).every(s => s.acc <= GPS_GOOD_ACC_M);
          if (samples.length >= GPS_MAX_SAMPLES || settled) finish();
        },
        err => {
          if (samples.length > 0) { finish(); return; }
          const msgs = {1:"Location access denied.",2:"Location unavailable.",3:"Location request timed out."};
          finish(msgs[err.code] || "GPS error.");
        },
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
      );
      timer = setTimeout(() => finish(), GPS_COLLECT_MS);
    };
    requestPosition();
  });
}
`;

// Free globals of the real source become injected parameters, so the virtual
// clock substitutes for the host environment without touching the source.
const PARAMS = ["navigator", "window", "setTimeout", "clearTimeout", "Date"];
const BODY     = new Function(...PARAMS, helpers + consts + getgps + "\nreturn getGPS();");
const PRE_BODY = new Function(...PARAMS, consts + PRE + "\nreturn getGPS();");

// ── virtual clock ────────────────────────────────────────────────
let NOW = 0, SEQ = 0;
const pending = new Map();
const setTimeout_ = (fn, ms) => { const id = ++SEQ; pending.set(id, {at: NOW + ms, fn}); return id; };
const clearTimeout_ = (id) => { pending.delete(id); };
function advanceTo(t) {
  for (;;) {
    let next = null;
    for (const [id, e] of pending) if (e.at <= t && (next === null || e.at < next[1].at)) next = [id, e];
    if (next === null) break;
    pending.delete(next[0]);
    NOW = next[1].at;
    next[1].fn();
  }
  NOW = Math.max(NOW, t);
}
const Date_ = { now: () => NOW };

// ── scriptable geolocation ───────────────────────────────────────
// clearWatch is final: once called, nothing more is delivered. That is exactly
// the behaviour that made the old permission bug unrecoverable -- the window
// expired, cleared the watch, and the grant landing a second later had nowhere
// to go.
function makeGeo(script) {
  const state = {cleared: false};
  const geo = {
    watchPosition(ok, fail) {
      for (const ev of script) {
        setTimeout_(() => {
          if (state.cleared) return;
          if (ev.err) fail(ev.err);
          else ok({coords: {latitude: ev.pos[0], longitude: ev.pos[1], accuracy: ev.pos[2]}});
        }, ev.t);
      }
      return 1;
    },
    clearWatch() { state.cleared = true; },
  };
  return {state, geo};
}

const BAD = [];
let CHECKS = 0;
function chk(label, cond, detail) {
  CHECKS++;
  if (!cond) BAD.push(label + (detail ? ": " + detail : ""));
}

// `at` is the VIRTUAL time the promise settled, captured inside the callback.
// Reading NOW after the drain loop only ever reports the horizon -- an earlier
// version of this file did that and its timing assertions passed vacuously.
async function runWith(body, script, horizonMs) {
  NOW = 0; SEQ = 0; pending.clear();
  const {geo} = makeGeo(script);
  const navigator = {geolocation: geo};   // no .permissions -> straight to requestPosition
  const window = {isSecureContext: true, location: {protocol: "https:", hostname: "example.org"}};
  let out = null;
  const p = body(navigator, window, setTimeout_, clearTimeout_, Date_)
    .then(v => { out = {ok: v, at: NOW}; }, e => { out = {err: e.message, at: NOW}; });
  for (let t = 0; t <= horizonMs; t += 50) { advanceTo(t); await Promise.resolve(); }
  await p.catch(() => {});
  return out || {err: "NEVER SETTLED", at: null};
}
const run    = (s, h) => runWith(BODY, s, h);
const runPre = (s, h) => runWith(PRE_BODY, s, h);

function show(name, r) {
  const what = r.ok
    ? `${r.ok.samples.length} samples`
    : `REJECT "${String(r.err).slice(0, 46)}"`;
  console.log(`${name.padEnd(21)} -> ${what}${r.at === null ? "" : `  @ ${r.at}ms`}`);
}

// ── scenarios ────────────────────────────────────────────────────
const SC = {};
// Chrome Android's three-option prompt, answered after ten seconds. Until it
// is answered the browser delivers no callback of either kind.
SC.late = [];
for (let i = 0; i < 12; i++) SC.late.push({t: 10000 + i * 700, pos: [6.5244 + i * 1e-6, 3.3792, 14]});

// The common Android sequence: coarse fused fix, then POSITION_UNAVAILABLE
// while GNSS is still acquiring, then the real fixes.
SC.blip = [
  {t: 300,  pos: [6.5244, 3.3792, 180]},
  {t: 1000, err: {code: 2, message: "position unavailable"}},
];
for (let i = 0; i < 9; i++) SC.blip.push({t: 2000 + i * 600, pos: [6.52441 + i * 2e-6, 3.37921, 11]});

SC.denied = [{t: 200, err: {code: 1, message: "User denied Geolocation"}}];
SC.none   = [];   // prompt never answered

// A stationary phone replaying one WiFi-fingerprint estimate: byte-identical
// fixes at a confident 15m. Spread 0.0, acc_eff 15, which is under the
// server's coarse-fix trigger of 0.5 x 50m -- so the reason could never fire.
SC.frozen = [];
for (let i = 0; i < 6; i++) SC.frozen.push({t: 200 + i * 200, pos: [6.5244, 3.3792, 15]});

// A genuinely good, genuinely varying signal.
SC.good = [];
for (let i = 0; i < 10; i++) SC.good.push({t: 300 + i * 600, pos: [6.5244 + i * 3e-6, 3.3792 + i * 2e-6, 9]});

SC.flood = [];
for (let i = 0; i < 25; i++) SC.flood.push({t: 100 + i * 100, pos: [6.5244 + i * 4e-6, 3.3792, 40]});

(async () => {
  // F1 -- the collection window must start on the first callback, not at
  // watch registration.
  const s1 = await run(SC.late, 60000);
  show("S1 late-permission", s1);
  chk("S1 resolved", !!s1.ok, s1.err);
  chk("S1 collected >1 sample", s1.ok && s1.ok.samples.length > 1,
      s1.ok ? "n=" + s1.ok.samples.length : "");

  // F2 -- a transient code-2 must not end the capture.
  const s2 = await run(SC.blip, 30000);
  show("S2 transient-blip", s2);
  chk("S2 resolved", !!s2.ok, s2.err);
  chk("S2 kept collecting past the blip", s2.ok && s2.ok.samples.length >= 5,
      s2.ok ? "n=" + s2.ok.samples.length : "");
  chk("S2 reached the real GNSS fixes", s2.ok && s2.ok.samples.some(s => s.acc === 11));

  // Denial is permanent. Transient tolerance must not swallow code 1.
  const s3 = await run(SC.denied, 60000);
  show("S3 denied", s3);
  chk("S3 rejected", !!s3.err, "resolved instead");
  chk("S3 says access denied", !!s3.err && /access denied/i.test(s3.err), s3.err);
  chk("S3 fails fast", s3.at !== null && s3.at < 1000, "at " + s3.at + "ms");

  // No callback at all. Must settle, and must say what is actually wrong.
  const s4 = await run(SC.none, 90000);
  show("S4 unanswered", s4);
  chk("S4 rejected", !!s4.err, "did not settle");
  chk("S4 names the permission prompt",
      !!s4.err && /permission was not answered/i.test(s4.err), s4.err);
  chk("S4 waited GPS_PERMISSION_MAX_MS", s4.at === 45000, "at " + s4.at + "ms");

  // F6 -- self-reported accuracy alone must not trigger an early exit.
  const s5 = await run(SC.frozen, 30000);
  show("S5 frozen-identical", s5);
  chk("S5 resolved", !!s5.ok, s5.err);
  chk("S5 did not early-exit", s5.at >= 4000, "settled at " + s5.at + "ms");
  chk("S5 ran the full window", s5.at === 8200, "settled at " + s5.at + "ms");

  // ...but a real good signal still exits early, so the fix did not simply
  // disable early exit and charge every student the full eight seconds.
  const s6 = await run(SC.good, 30000);
  show("S6 good-signal", s6);
  chk("S6 resolved", !!s6.ok, s6.err);
  chk("S6 still exits early", s6.at < 8300, "settled at " + s6.at + "ms");
  chk("S6 waited GPS_EARLY_MIN_MS", s6.at >= 4000, "settled at " + s6.at + "ms");

  const s7 = await run(SC.flood, 30000);
  show("S7 flood", s7);
  chk("S7 capped at GPS_MAX_SAMPLES", s7.ok && s7.ok.samples.length <= 20,
      s7.ok ? "n=" + s7.ok.samples.length : "");

  console.log("");
  console.log("-- pre-fix contrast: every line below MUST be the broken behaviour --");

  const p1 = await runPre(SC.late, 60000);
  show("P1 late-permission", p1);
  chk("TEETH P1 old code rejects a granted capture",
      !!p1.err && /Could not get a GPS reading/.test(p1.err),
      "old code did NOT fail, so S1 proves nothing");

  const p2 = await runPre(SC.blip, 30000);
  show("P2 transient-blip", p2);
  chk("TEETH P2 old code submits the lone coarse fix",
      p2.ok && p2.ok.samples.length === 1,
      p2.ok ? "n=" + p2.ok.samples.length : p2.err);
  chk("TEETH P2 and that fix is the 180m one",
      p2.ok && p2.ok.samples.length === 1 && p2.ok.samples[0].acc === 180);

  const p4 = await runPre(SC.none, 90000);
  show("P4 unanswered", p4);
  chk("TEETH P4 old code gives the generic message",
      !!p4.err && /Could not get a GPS reading/.test(p4.err), p4.err);
  chk("TEETH P4 old code gives up at GPS_COLLECT_MS", p4.at === 8000, "at " + p4.at + "ms");

  const p5 = await runPre(SC.frozen, 30000);
  show("P5 frozen-identical", p5);
  chk("TEETH P5 old code early-exits on the self-report",
      p5.ok && p5.at < 2000, "settled at " + p5.at + "ms");

  console.log("");
  console.log("checks run: " + CHECKS);
  console.log("CLIENT BAD: " + (BAD.length ? JSON.stringify(BAD, null, 1) : "[] -- all pass"));
  process.exitCode = BAD.length ? 1 : 0;
})();
