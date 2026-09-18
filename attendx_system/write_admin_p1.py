import pathlib

HTML = r"""{% extends "base.html" %}
{% block title %}Admin Control Centre{% endblock %}

{% block extra_head %}
<style>
/* ── Admin-specific tokens ── */
:root {
  --admin:      #a78bfa;
  --admin-dim:  #4c1d95;
  --admin-glow: rgba(167,139,250,.15);
  --su:         #fb923c;
  --su-dim:     rgba(251,146,60,.15);
}

/* ── Sidebar layout ── */
.adm-shell {
  display: flex;
  min-height: calc(100vh - 60px);
}
.adm-sidebar {
  width: 220px;
  flex-shrink: 0;
  background: var(--navy-mid);
  border-right: 1px solid var(--border);
  padding: 1.5rem 0;
  position: sticky;
  top: 60px;
  height: calc(100vh - 60px);
  overflow-y: auto;
}
.adm-sidebar-label {
  font-size: .68rem;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: var(--muted);
  padding: .25rem 1.25rem .6rem;
}
.adm-nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: .65rem 1.25rem;
  font-size: .88rem;
  color: var(--muted);
  cursor: pointer;
  border-left: 3px solid transparent;
  transition: all .18s;
  user-select: none;
}
.adm-nav-item:hover { color: var(--text); background: rgba(255,255,255,.03); }
.adm-nav-item.active {
  color: var(--admin);
  border-left-color: var(--admin);
  background: var(--admin-glow);
  font-weight: 500;
}
.adm-nav-item .icon { font-size: 1rem; width: 20px; text-align: center; }
.adm-nav-item.su-only { display: none; }
.adm-nav-item.su-only.visible { display: flex; }

/* ── Main content ── */
.adm-main {
  flex: 1;
  padding: 2rem 2.5rem;
  min-width: 0;
}
.adm-tab { display: none; animation: fadeIn .3s ease; }
.adm-tab.active { display: block; }

/* ── Section header ── */
.sec-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border);
}
.sec-title {
  font-family: var(--font-head);
  font-size: 1.7rem;
  color: var(--white);
}
.sec-sub { font-size: .82rem; color: var(--muted); margin-top: 2px; }

/* ── Toast ── */
#toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  background: var(--navy-light);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: .85rem 1.25rem;
  font-size: .88rem;
  z-index: 9999;
  max-width: 340px;
  display: none;
  box-shadow: 0 8px 32px rgba(0,0,0,.4);
  animation: fadeIn .25s ease;
}
#toast.ok  { border-color: var(--green); color: var(--green); }
#toast.err { border-color: var(--red);   color: var(--red);   }

/* ── Modal ── */
.modal-bg {
  display: none;
  position: fixed; inset: 0;
  background: rgba(0,0,0,.65);
  z-index: 500;
  align-items: center;
  justify-content: center;
}
.modal-bg.open { display: flex; }
.modal {
  background: var(--navy-mid);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 2rem;
  width: 100%;
  max-width: 480px;
  animation: fadeIn .25s ease;
}
.modal-title {
  font-family: var(--font-head);
  font-size: 1.3rem;
  color: var(--white);
  margin-bottom: 1.25rem;
}
.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: .75rem;
  margin-top: 1.5rem;
}

/* ── Stats strip ── */
.stats-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1rem;
  margin-bottom: 2rem;
}
.stat-card {
  background: var(--navy-mid);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.1rem 1.25rem;
  display: flex;
  align-items: center;
  gap: 1rem;
  transition: border-color .2s;
}
.stat-card:hover { border-color: #3a5070; }
.stat-icon { font-size: 1.6rem; }
.stat-info {}
.stat-val { font-family: var(--font-head); font-size: 1.6rem; color: var(--white); line-height: 1; }
.stat-lbl { font-size: .74rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-top: 2px; }

/* ── Table actions col ── */
.act-btns { display: flex; gap: .4rem; flex-wrap: wrap; }

/* ── Role badge variants ── */
.badge-admin { background: var(--admin-glow); color: var(--admin); }
.badge-superuser { background: var(--su-dim); color: var(--su); }
.badge-lecturer { background: rgba(96,165,250,.1); color: var(--blue); }
.badge-student { background: rgba(34,211,160,.1); color: var(--green); }

/* ── Log details ── */
.log-details {
  font-family: var(--font-mono);
  font-size: .78rem;
  color: var(--muted);
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Pagination ── */
.pagination { display: flex; gap: .4rem; align-items: center; margin-top: 1rem; }
.pg-btn {
  background: var(--navy-mid);
  border: 1px solid var(--border);
  color: var(--muted);
  padding: 5px 12px;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: .82rem;
  transition: all .15s;
}
.pg-btn:hover { border-color: var(--admin); color: var(--admin); }
.pg-btn.active { border-color: var(--admin); color: var(--admin); background: var(--admin-glow); }
.pg-btn:disabled { opacity: .4; cursor: not-allowed; }

/* ── Search bar ── */
.search-bar {
  display: flex;
  gap: .75rem;
  margin-bottom: 1.25rem;
  align-items: center;
}
.search-bar .form-control { max-width: 260px; }

/* ── Log action pill ── */
.log-action {
  font-family: var(--font-mono);
  font-size: .72rem;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(167,139,250,.1);
  color: var(--admin);
}

@media (max-width: 900px) {
  .adm-sidebar { display: none; }
  .stats-strip { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 600px) {
  .adm-main { padding: 1rem; }
  .stats-strip { grid-template-columns: 1fr; }
}
</style>
{% endblock %}

{% block content %}
<div class="adm-shell">

  <!-- ── Sidebar ── -->
  <aside class="adm-sidebar">
    <div class="adm-sidebar-label">Navigation</div>
    <div class="adm-nav-item active" data-tab="locations">
      <span class="icon">📍</span> Locations
    </div>
    <div class="adm-nav-item" data-tab="courses">
      <span class="icon">📚</span> Courses
    </div>
    <div class="adm-nav-item" data-tab="enrollments">
      <span class="icon">🎓</span> Enrollments
    </div>
    <div class="adm-nav-item su-only" id="nav-users" data-tab="users">
      <span class="icon">👥</span> Users &amp; Roles
    </div>
    <div class="adm-nav-item" data-tab="logs">
      <span class="icon">📋</span> Activity Logs
    </div>
  </aside>

  <!-- ── Main ── -->
  <main class="adm-main">

    <!-- Overview stats -->
    <div class="stats-strip" id="statsStrip">
      <div class="stat-card"><span class="stat-icon">📍</span><div class="stat-info"><div class="stat-val" id="sLocations">—</div><div class="stat-lbl">Locations</div></div></div>
      <div class="stat-card"><span class="stat-icon">📚</span><div class="stat-info"><div class="stat-val" id="sCourses">—</div><div class="stat-lbl">Courses</div></div></div>
      <div class="stat-card"><span class="stat-icon">🎓</span><div class="stat-info"><div class="stat-val" id="sEnrollments">—</div><div class="stat-lbl">Enrollments</div></div></div>
      <div class="stat-card"><span class="stat-icon">👤</span><div class="stat-info"><div class="stat-val" id="sUsers">—</div><div class="stat-lbl">Users</div></div></div>
    </div>

    <!-- ════ TAB: LOCATIONS ════ -->
    <div class="adm-tab active" id="tab-locations">
      <div class="sec-head">
        <div><div class="sec-title">Locations</div><div class="sec-sub">Physical venues used for course scheduling</div></div>
        <button class="btn btn-primary btn-sm" id="btnAddLocation">+ Add Location</button>
      </div>
      <div class="search-bar">
        <input class="form-control" id="locSearch" placeholder="Filter by name…" oninput="renderLocations()">
      </div>
      <div class="card table-wrap">
        <table id="locTable">
          <thead><tr><th>Name</th><th>Latitude</th><th>Longitude</th><th>Schedules</th><th>Actions</th></tr></thead>
          <tbody id="locBody"><tr><td colspan="5" style="color:var(--muted);text-align:center;padding:2rem"><span class="spinner"></span></td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- ════ TAB: COURSES ════ -->
    <div class="adm-tab" id="tab-courses">
      <div class="sec-head">
        <div><div class="sec-title">Courses</div><div class="sec-sub">Manage courses and lecturer assignments</div></div>
        <button class="btn btn-primary btn-sm" id="btnAddCourse">+ Add Course</button>
      </div>
      <div class="search-bar">
        <input class="form-control" id="courseSearch" placeholder="Filter by code or name…" oninput="renderCourses()">
      </div>
      <div class="card table-wrap">
        <table id="courseTable">
          <thead><tr><th>Code</th><th>Name</th><th>Lecturer</th><th>Enrolled</th><th>Actions</th></tr></thead>
          <tbody id="courseBody"><tr><td colspan="5" style="color:var(--muted);text-align:center;padding:2rem"><span class="spinner"></span></td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- ════ TAB: ENROLLMENTS ════ -->
    <div class="adm-tab" id="tab-enrollments">
      <div class="sec-head">
        <div><div class="sec-title">Enrollments</div><div class="sec-sub">Assign and remove students from courses</div></div>
        <button class="btn btn-primary btn-sm" id="btnAddEnrollment">+ Enroll Student</button>
      </div>
      <div class="search-bar">
        <select class="form-control" id="enrollCourseFilter" onchange="loadEnrollments()" style="max-width:220px">
          <option value="">All courses</option>
        </select>
        <input class="form-control" id="enrollSearch" placeholder="Filter by student name…" oninput="renderEnrollments()">
      </div>
      <div class="card table-wrap">
        <table>
          <thead><tr><th>Student</th><th>Email</th><th>Course</th><th>Enrolled</th><th>Actions</th></tr></thead>
          <tbody id="enrollBody"><tr><td colspan="5" style="color:var(--muted);text-align:center;padding:2rem"><span class="spinner"></span></td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- ════ TAB: USERS (superuser only) ════ -->
    <div class="adm-tab" id="tab-users">
      <div class="sec-head">
        <div><div class="sec-title">Users &amp; Roles</div><div class="sec-sub">Promote lecturers/students to admin or demote admins</div></div>
      </div>
      <div class="search-bar">
        <select class="form-control" id="userRoleFilter" onchange="loadUsers()" style="max-width:180px">
          <option value="">All roles</option>
          <option value="admin">Admin</option>
          <option value="lecturer">Lecturer</option>
          <option value="student">Student</option>
        </select>
        <input class="form-control" id="userSearch" placeholder="Filter by name or email…" oninput="renderUsers()">
      </div>
      <div class="card table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Joined</th><th>Actions</th></tr></thead>
          <tbody id="userBody"><tr><td colspan="5" style="color:var(--muted);text-align:center;padding:2rem"><span class="spinner"></span></td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- ════ TAB: LOGS ════ -->
    <div class="adm-tab" id="tab-logs">
      <div class="sec-head">
        <div><div class="sec-title">Activity Logs</div><div class="sec-sub">Audit trail of all admin actions</div></div>
        <button class="btn btn-outline btn-sm" onclick="loadLogs(1)">↻ Refresh</button>
      </div>
      <div class="search-bar">
        <select class="form-control" id="logActionFilter" onchange="loadLogs(1)" style="max-width:200px">
          <option value="">All actions</option>
          <option value="ADD_VENUE">Add Venue</option>
          <option value="EDIT_VENUE">Edit Venue</option>
          <option value="DELETE_VENUE">Delete Venue</option>
          <option value="ADD_COURSE">Add Course</option>
          <option value="EDIT_COURSE">Edit Course</option>
          <option value="DELETE_COURSE">Delete Course</option>
          <option value="ENROLL_STUDENT">Enroll</option>
          <option value="UNENROLL_STUDENT">Unenroll</option>
          <option value="PROMOTE_USER">Promote</option>
          <option value="DEMOTE_USER">Demote</option>
        </select>
      </div>
      <div class="card table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Admin</th><th>Action</th><th>Target</th><th>Details</th></tr></thead>
          <tbody id="logBody"><tr><td colspan="5" style="color:var(--muted);text-align:center;padding:2rem"><span class="spinner"></span></td></tr></tbody>
        </table>
      </div>
      <div class="pagination" id="logPagination"></div>
    </div>

  </main>
</div>

<!-- ════ MODALS ════ -->

<!-- Add/Edit Location -->
<div class="modal-bg" id="modalLocation">
  <div class="modal">
    <div class="modal-title" id="modalLocationTitle">Add Location</div>
    <input type="hidden" id="locId">
    <div class="form-group">
      <label class="form-label">Name</label>
      <input class="form-control" id="locName" placeholder="e.g. Main Auditorium">
    </div>
    <div class="grid-2">
      <div class="form-group">
        <label class="form-label">Latitude</label>
        <input class="form-control" id="locLat" placeholder="e.g. 6.5244" type="number" step="any">
      </div>
      <div class="form-group">
        <label class="form-label">Longitude</label>
        <input class="form-control" id="locLng" placeholder="e.g. 3.3792" type="number" step="any">
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn btn-outline btn-sm" onclick="closeModal('modalLocation')">Cancel</button>
      <button class="btn btn-primary btn-sm" id="btnSaveLocation" onclick="saveLocation()">Save</button>
    </div>
  </div>
</div>

<!-- Add/Edit Course -->
<div class="modal-bg" id="modalCourse">
  <div class="modal">
    <div class="modal-title" id="modalCourseTitle">Add Course</div>
    <input type="hidden" id="courseId">
    <div class="grid-2">
      <div class="form-group">
        <label class="form-label">Course Code</label>
        <input class="form-control" id="courseCode" placeholder="e.g. CSC401">
      </div>
      <div class="form-group">
        <label class="form-label">Lecturer</label>
        <select class="form-control" id="courseLecturer"></select>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Course Name</label>
      <input class="form-control" id="courseName" placeholder="e.g. Advanced Algorithms">
    </div>
    <div class="modal-foot">
      <button class="btn btn-outline btn-sm" onclick="closeModal('modalCourse')">Cancel</button>
      <button class="btn btn-primary btn-sm" onclick="saveCourse()">Save</button>
    </div>
  </div>
</div>

<!-- Enroll Student -->
<div class="modal-bg" id="modalEnroll">
  <div class="modal">
    <div class="modal-title">Enroll Student</div>
    <div class="form-group">
      <label class="form-label">Student</label>
      <select class="form-control" id="enrollStudent"></select>
    </div>
    <div class="form-group">
      <label class="form-label">Course</label>
      <select class="form-control" id="enrollCourse"></select>
    </div>
    <div class="modal-foot">
      <button class="btn btn-outline btn-sm" onclick="closeModal('modalEnroll')">Cancel</button>
      <button class="btn btn-primary btn-sm" onclick="saveEnrollment()">Enroll</button>
    </div>
  </div>
</div>

<!-- Demote User -->
<div class="modal-bg" id="modalDemote">
  <div class="modal">
    <div class="modal-title">Demote User</div>
    <p style="color:var(--muted);font-size:.9rem;margin-bottom:1.25rem">
      Select the role to demote <strong id="demoteName" style="color:var(--text)"></strong> to:
    </p>
    <input type="hidden" id="demoteId">
    <div class="form-group">
      <label class="form-label">New Role</label>
      <select class="form-control" id="demoteRole">
        <option value="lecturer">Lecturer</option>
        <option value="student">Student</option>
      </select>
    </div>
    <div class="modal-foot">
      <button class="btn btn-outline btn-sm" onclick="closeModal('modalDemote')">Cancel</button>
      <button class="btn btn-danger btn-sm" onclick="confirmDemote()">Demote</button>
    </div>
  </div>
</div>

<!-- Toast -->
<div id="toast"></div>

{% endblock %}
"""

p = pathlib.Path(r"templates/admin_dashboard.html")
p.write_text(HTML, encoding="utf-8")
print(f"Part 1 written — {len(HTML)} chars, {p.stat().st_size} bytes")
