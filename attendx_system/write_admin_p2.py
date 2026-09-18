import pathlib

JS = r"""{% block scripts %}
<script>
// ══════════════════════════════════════════════
//  STATE
// ══════════════════════════════════════════════
const IS_SUPERUSER = {{ 'true' if user.is_superuser else 'false' }};
let state = {
  locations: [], courses: [], enrollments: [],
  users: [], lecturers: [], students: [],
  logsPage: 1, logsTotalPages: 1,
};

// ══════════════════════════════════════════════
//  INIT
// ══════════════════════════════════════════════
document.addEventListener("DOMContentLoaded", () => {
  if (IS_SUPERUSER) {
    document.querySelectorAll(".su-only").forEach(el => el.classList.add("visible"));
  }
  // Tab nav
  document.querySelectorAll(".adm-nav-item[data-tab]").forEach(el => {
    el.addEventListener("click", () => switchTab(el.dataset.tab));
  });
  document.getElementById("btnAddLocation").addEventListener("click", openAddLocation);
  document.getElementById("btnAddCourse").addEventListener("click", openAddCourse);
  document.getElementById("btnAddEnrollment").addEventListener("click", openAddEnrollment);

  // Close modal on backdrop click
  document.querySelectorAll(".modal-bg").forEach(bg => {
    bg.addEventListener("click", e => { if (e.target === bg) bg.classList.remove("open"); });
  });

  loadAll();
});

function switchTab(name) {
  document.querySelectorAll(".adm-tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".adm-nav-item").forEach(n => n.classList.remove("active"));
  document.getElementById("tab-" + name).classList.add("active");
  document.querySelector(`.adm-nav-item[data-tab="${name}"]`).classList.add("active");
  if (name === "logs") loadLogs(1);
}

// ══════════════════════════════════════════════
//  API HELPERS
// ══════════════════════════════════════════════
async function api(method, url, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data };
}

function toast(msg, ok = true) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = ok ? "ok" : "err";
  el.style.display = "block";
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.display = "none"; }, 3500);
}

function closeModal(id) { document.getElementById(id).classList.remove("open"); }
function openModal(id)  { document.getElementById(id).classList.add("open"); }

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}
function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) +
         " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

// ══════════════════════════════════════════════
//  LOAD ALL (initial)
// ══════════════════════════════════════════════
async function loadAll() {
  await Promise.all([
    loadLocations(),
    loadCourses(),
    loadEnrollments(),
    loadUsers(),
  ]);
  updateStats();
}

function updateStats() {
  document.getElementById("sLocations").textContent = state.locations.length;
  document.getElementById("sCourses").textContent = state.courses.length;
  document.getElementById("sEnrollments").textContent = state.enrollments.length;
  document.getElementById("sUsers").textContent = state.users.length;
}

// ══════════════════════════════════════════════
//  LOCATIONS
// ══════════════════════════════════════════════
async function loadLocations() {
  const { ok, data } = await api("GET", "/api/admin/locations");
  if (ok) { state.locations = data.locations; renderLocations(); }
}

function renderLocations() {
  const q = document.getElementById("locSearch").value.toLowerCase();
  const list = state.locations.filter(l => l.name.toLowerCase().includes(q));
  const tbody = document.getElementById("locBody");
  if (!list.length) { tbody.innerHTML = `<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:1.5rem">No locations found.</td></tr>`; return; }
  tbody.innerHTML = list.map(l => `
    <tr>
      <td><strong>${esc(l.name)}</strong></td>
      <td style="font-family:var(--font-mono);font-size:.82rem">${l.latitude.toFixed(6)}</td>
      <td style="font-family:var(--font-mono);font-size:.82rem">${l.longitude.toFixed(6)}</td>
      <td><span class="badge badge-active">${l.schedule_count}</span></td>
      <td><div class="act-btns">
        <button class="btn btn-outline btn-sm" onclick="openEditLocation(${l.id})">Edit</button>
        <button class="btn btn-danger btn-sm" onclick="deleteLocation(${l.id},'${esc(l.name)}')">Delete</button>
      </div></td>
    </tr>`).join("");
}

function openAddLocation() {
  document.getElementById("locId").value = "";
  document.getElementById("locName").value = "";
  document.getElementById("locLat").value = "";
  document.getElementById("locLng").value = "";
  document.getElementById("modalLocationTitle").textContent = "Add Location";
  openModal("modalLocation");
}

function openEditLocation(id) {
  const l = state.locations.find(x => x.id === id);
  if (!l) return;
  document.getElementById("locId").value = l.id;
  document.getElementById("locName").value = l.name;
  document.getElementById("locLat").value = l.latitude;
  document.getElementById("locLng").value = l.longitude;
  document.getElementById("modalLocationTitle").textContent = "Edit Location";
  openModal("modalLocation");
}

async function saveLocation() {
  const id = document.getElementById("locId").value;
  const body = {
    name: document.getElementById("locName").value.trim(),
    latitude: parseFloat(document.getElementById("locLat").value),
    longitude: parseFloat(document.getElementById("locLng").value),
  };
  if (!body.name) { toast("Name is required", false); return; }
  if (isNaN(body.latitude) || isNaN(body.longitude)) { toast("Valid coordinates required", false); return; }

  const { ok, data } = id
    ? await api("PUT", `/api/admin/locations/${id}`, body)
    : await api("POST", "/api/admin/locations", body);

  if (ok) {
    toast(id ? "Location updated" : "Location added");
    closeModal("modalLocation");
    await loadLocations();
    updateStats();
  } else {
    toast(data.error || "Error saving location", false);
  }
}

async function deleteLocation(id, name) {
  if (!confirm(`Delete location "${name}"?`)) return;
  const { ok, data } = await api("DELETE", `/api/admin/locations/${id}`);
  if (ok) { toast("Location deleted"); await loadLocations(); updateStats(); }
  else toast(data.error || "Cannot delete location", false);
}

// ══════════════════════════════════════════════
//  COURSES
// ══════════════════════════════════════════════
async function loadCourses() {
  const [cRes, uRes] = await Promise.all([
    api("GET", "/api/admin/courses"),
    api("GET", "/api/admin/users?role=lecturer"),
  ]);
  if (cRes.ok) { state.courses = cRes.data.courses; renderCourses(); }
  if (uRes.ok) state.lecturers = uRes.data.users;
}

function renderCourses() {
  const q = document.getElementById("courseSearch").value.toLowerCase();
  const list = state.courses.filter(c =>
    c.course_code.toLowerCase().includes(q) || c.course_name.toLowerCase().includes(q));
  const tbody = document.getElementById("courseBody");
  if (!list.length) { tbody.innerHTML = `<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:1.5rem">No courses found.</td></tr>`; return; }
  tbody.innerHTML = list.map(c => `
    <tr>
      <td><span class="badge badge-active" style="font-family:var(--font-mono)">${esc(c.course_code)}</span></td>
      <td>${esc(c.course_name)}</td>
      <td style="color:var(--blue)">${esc(c.lecturer_name || "—")}</td>
      <td>${c.enrolled}</td>
      <td><div class="act-btns">
        <button class="btn btn-outline btn-sm" onclick="openEditCourse(${c.id})">Edit</button>
        <button class="btn btn-danger btn-sm" onclick="deleteCourse(${c.id},'${esc(c.course_code)}')">Delete</button>
      </div></td>
    </tr>`).join("");
}

function openAddCourse() {
  document.getElementById("courseId").value = "";
  document.getElementById("courseCode").value = "";
  document.getElementById("courseName").value = "";
  populateLecturerSelect();
  document.getElementById("modalCourseTitle").textContent = "Add Course";
  openModal("modalCourse");
}

function openEditCourse(id) {
  const c = state.courses.find(x => x.id === id);
  if (!c) return;
  document.getElementById("courseId").value = c.id;
  document.getElementById("courseCode").value = c.course_code;
  document.getElementById("courseName").value = c.course_name;
  populateLecturerSelect(c.lecturer_id);
  document.getElementById("modalCourseTitle").textContent = "Edit Course";
  openModal("modalCourse");
}

function populateLecturerSelect(selectedId) {
  const sel = document.getElementById("courseLecturer");
  sel.innerHTML = state.lecturers.map(l =>
    `<option value="${l.id}" ${l.id === selectedId ? "selected" : ""}>${esc(l.name)}</option>`).join("");
}

async function saveCourse() {
  const id = document.getElementById("courseId").value;
  const body = {
    course_code: document.getElementById("courseCode").value.trim(),
    course_name: document.getElementById("courseName").value.trim(),
    lecturer_id: parseInt(document.getElementById("courseLecturer").value),
  };
  if (!body.course_code || !body.course_name) { toast("Code and name are required", false); return; }

  const { ok, data } = id
    ? await api("PUT", `/api/admin/courses/${id}`, body)
    : await api("POST", "/api/admin/courses", body);

  if (ok) {
    toast(id ? "Course updated" : "Course created");
    closeModal("modalCourse");
    await loadCourses();
    updateStats();
  } else {
    toast(data.error || "Error saving course", false);
  }
}

async function deleteCourse(id, code) {
  if (!confirm(`Delete course ${code}? This will remove all its sessions and enrollments.`)) return;
  const { ok, data } = await api("DELETE", `/api/admin/courses/${id}`);
  if (ok) { toast("Course deleted"); await Promise.all([loadCourses(), loadEnrollments()]); updateStats(); }
  else toast(data.error || "Cannot delete course", false);
}

// ══════════════════════════════════════════════
//  ENROLLMENTS
// ══════════════════════════════════════════════
async function loadEnrollments() {
  const courseId = document.getElementById("enrollCourseFilter")?.value || "";
  const url = courseId ? `/api/admin/enrollments?course_id=${courseId}` : "/api/admin/enrollments";
  const [eRes, sRes] = await Promise.all([
    api("GET", url),
    api("GET", "/api/admin/users?role=student"),
  ]);
  if (eRes.ok) { state.enrollments = eRes.data.enrollments; }
  if (sRes.ok) { state.students = sRes.data.users; }

  // Populate course filter
  const sel = document.getElementById("enrollCourseFilter");
  if (sel && state.courses.length) {
    const cur = sel.value;
    sel.innerHTML = `<option value="">All courses</option>` +
      state.courses.map(c => `<option value="${c.id}" ${c.id == cur ? "selected" : ""}>${esc(c.course_code)} — ${esc(c.course_name)}</option>`).join("");
    sel.value = cur;
  }

  renderEnrollments();
}

function renderEnrollments() {
  const q = document.getElementById("enrollSearch").value.toLowerCase();
  const list = state.enrollments.filter(e => e.student_name.toLowerCase().includes(q));
  const tbody = document.getElementById("enrollBody");
  if (!list.length) { tbody.innerHTML = `<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:1.5rem">No enrollments found.</td></tr>`; return; }
  tbody.innerHTML = list.map(e => `
    <tr>
      <td>${esc(e.student_name)}</td>
      <td style="color:var(--muted);font-size:.82rem">${esc(e.student_email)}</td>
      <td><span class="badge badge-active" style="font-family:var(--font-mono)">${esc(e.course_code)}</span></td>
      <td style="font-size:.82rem;color:var(--muted)">${fmtDate(e.enrolled_at)}</td>
      <td><button class="btn btn-danger btn-sm" onclick="unenroll(${e.id},'${esc(e.student_name)}','${esc(e.course_code)}')">Remove</button></td>
    </tr>`).join("");
}

function openAddEnrollment() {
  const sSel = document.getElementById("enrollStudent");
  const cSel = document.getElementById("enrollCourse");
  sSel.innerHTML = state.students.map(s => `<option value="${s.id}">${esc(s.name)} — ${esc(s.email)}</option>`).join("");
  cSel.innerHTML = state.courses.map(c => `<option value="${c.id}">${esc(c.course_code)} — ${esc(c.course_name)}</option>`).join("");
  openModal("modalEnroll");
}

async function saveEnrollment() {
  const body = {
    student_id: parseInt(document.getElementById("enrollStudent").value),
    course_id: parseInt(document.getElementById("enrollCourse").value),
  };
  const { ok, data } = await api("POST", "/api/admin/enrollments", body);
  if (ok) {
    toast("Student enrolled");
    closeModal("modalEnroll");
    await loadEnrollments();
    updateStats();
  } else {
    toast(data.error || "Error enrolling student", false);
  }
}

async function unenroll(id, name, code) {
  if (!confirm(`Remove ${name} from ${code}?`)) return;
  const { ok, data } = await api("DELETE", `/api/admin/enrollments/${id}`);
  if (ok) { toast("Enrollment removed"); await loadEnrollments(); updateStats(); }
  else toast(data.error || "Cannot remove enrollment", false);
}

// ══════════════════════════════════════════════
//  USERS & ROLES
// ══════════════════════════════════════════════
async function loadUsers() {
  const role = document.getElementById("userRoleFilter")?.value || "";
  const url = role ? `/api/admin/users?role=${role}` : "/api/admin/users";
  const { ok, data } = await api("GET", url);
  if (ok) { state.users = data.users; renderUsers(); }
}

function renderUsers() {
  const q = document.getElementById("userSearch")?.value.toLowerCase() || "";
  const list = state.users.filter(u =>
    u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q));
  const tbody = document.getElementById("userBody");
  if (!tbody) return;
  if (!list.length) { tbody.innerHTML = `<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:1.5rem">No users found.</td></tr>`; return; }

  tbody.innerHTML = list.map(u => {
    const badgeCls = u.is_superuser ? "badge-superuser" : u.role === "admin" ? "badge-admin" : u.role === "lecturer" ? "badge-lecturer" : "badge-student";
    const roleLabel = u.is_superuser ? "Superuser" : u.role;
    let actions = "";
    if (IS_SUPERUSER) {
      if (u.role !== "admin") {
        actions = `<button class="btn btn-outline btn-sm" onclick="promoteUser(${u.id},'${esc(u.name)}')">Promote to Admin</button>`;
      } else if (!u.is_superuser) {
        actions = `<button class="btn btn-danger btn-sm" onclick="openDemote(${u.id},'${esc(u.name)}')">Demote</button>`;
      } else {
        actions = `<span style="color:var(--muted);font-size:.78rem">You</span>`;
      }
    }
    return `<tr>
      <td><strong>${esc(u.name)}</strong></td>
      <td style="color:var(--muted);font-size:.82rem">${esc(u.email)}</td>
      <td><span class="badge ${badgeCls}">${roleLabel}</span></td>
      <td style="font-size:.82rem;color:var(--muted)">${fmtDate(u.created_at)}</td>
      <td>${actions}</td>
    </tr>`;
  }).join("");
}

async function promoteUser(id, name) {
  if (!confirm(`Promote ${name} to admin?`)) return;
  const { ok, data } = await api("POST", `/api/admin/users/${id}/promote`);
  if (ok) { toast(data.message || "User promoted"); await loadUsers(); }
  else toast(data.error || "Cannot promote user", false);
}

function openDemote(id, name) {
  document.getElementById("demoteId").value = id;
  document.getElementById("demoteName").textContent = name;
  openModal("modalDemote");
}

async function confirmDemote() {
  const id = document.getElementById("demoteId").value;
  const role = document.getElementById("demoteRole").value;
  const { ok, data } = await api("POST", `/api/admin/users/${id}/demote`, { role });
  if (ok) { toast(data.message || "User demoted"); closeModal("modalDemote"); await loadUsers(); }
  else toast(data.error || "Cannot demote user", false);
}

// ══════════════════════════════════════════════
//  LOGS
// ══════════════════════════════════════════════
async function loadLogs(page = 1) {
  state.logsPage = page;
  const action = document.getElementById("logActionFilter").value;
  let url = `/api/admin/logs?page=${page}&per_page=25`;
  if (action) url += `&action=${action}`;
  const { ok, data } = await api("GET", url);
  if (!ok) return;
  state.logsTotalPages = data.pages || 1;
  renderLogs(data.logs || []);
  renderLogPagination();
}

function renderLogs(logs) {
  const tbody = document.getElementById("logBody");
  if (!logs.length) { tbody.innerHTML = `<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:1.5rem">No log entries found.</td></tr>`; return; }
  tbody.innerHTML = logs.map(l => {
    let details = "";
    try { details = JSON.stringify(JSON.parse(l.details), null, 0); } catch { details = l.details || ""; }
    return `<tr>
      <td style="font-size:.8rem;color:var(--muted);white-space:nowrap">${fmtDateTime(l.created_at)}</td>
      <td style="font-size:.85rem">${esc(l.admin_name || "—")}</td>
      <td><span class="log-action">${esc(l.action)}</span></td>
      <td style="font-size:.82rem;color:var(--muted)">${esc(l.target_type || "—")} ${l.target_id ? "#" + l.target_id : ""}</td>
      <td><div class="log-details" title="${esc(details)}">${esc(details)}</div></td>
    </tr>`;
  }).join("");
}

function renderLogPagination() {
  const pg = document.getElementById("logPagination");
  const total = state.logsTotalPages;
  const cur = state.logsPage;
  if (total <= 1) { pg.innerHTML = ""; return; }
  let html = `<button class="pg-btn" onclick="loadLogs(${cur - 1})" ${cur === 1 ? "disabled" : ""}>‹ Prev</button>`;
  const start = Math.max(1, cur - 2), end = Math.min(total, cur + 2);
  for (let i = start; i <= end; i++) {
    html += `<button class="pg-btn ${i === cur ? "active" : ""}" onclick="loadLogs(${i})">${i}</button>`;
  }
  html += `<button class="pg-btn" onclick="loadLogs(${cur + 1})" ${cur === total ? "disabled" : ""}>Next ›</button>`;
  html += `<span style="font-size:.8rem;color:var(--muted);margin-left:.5rem">Page ${cur} of ${total}</span>`;
  pg.innerHTML = html;
}

// ══════════════════════════════════════════════
//  UTILS
// ══════════════════════════════════════════════
function esc(str) {
  if (str == null) return "";
  return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
</script>
{% endblock %}
"""

import pathlib

p = pathlib.Path(r"templates/admin_dashboard.html")
current = p.read_text(encoding="utf-8")
# Strip trailing "</body></html>" so we can append cleanly — base.html handles that via blocks
# The file ends with {% endblock %} for content block. We need to append the scripts block.
full = current.rstrip() + "\n" + JS
p.write_text(full, encoding="utf-8")
print(f"Part 2 appended — final size: {p.stat().st_size} bytes")
