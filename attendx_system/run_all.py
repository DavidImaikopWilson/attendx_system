"""
run_all.py — LOCAL ONLY. Not used on Vercel.

Launch all three AttendX servers simultaneously for local cookie isolation.

Do not also run `python app.py` — it shares port 5000 with the student server.

Usage:  python run_all.py

Ports:
  5000  →  Student  dashboard   http://localhost:5000/dashboard/student
  5001  →  Lecturer dashboard   http://localhost:5001/dashboard/lecturer
  5002  →  Admin    dashboard   http://localhost:5002/dashboard/admin

On Vercel there is one URL: /login, /dashboard/student, /dashboard/lecturer, /dashboard/admin.
"""
import socket
import subprocess
import sys
import time
import webbrowser
import os

PYTHON = sys.executable
BASE   = os.path.dirname(os.path.abspath(__file__))

# Windows consoles and redirected pipes default to cp1252, which cannot encode
# box-drawing/symbol characters — printing them raises UnicodeEncodeError and
# the launcher dies before it can report a port conflict. Output below is ASCII;
# this keeps stdout tolerant if anything non-ASCII slips in later.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

SERVERS = [
    {
        "label":  "Student  (port 5000)",
        "script": os.path.join(BASE, "run_student.py"),
        "url":    "http://localhost:5000",
        "port":   5000,
    },
    {
        "label":  "Lecturer (port 5001)",
        "script": os.path.join(BASE, "run_lecturer.py"),
        "url":    "http://localhost:5001",
        "port":   5001,
    },
    {
        "label":  "Admin    (port 5002)",
        "script": os.path.join(BASE, "run_admin.py"),
        "url":    "http://localhost:5002/dashboard/admin",
        "port":   5002,
    },
]

processes = []


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main():
    print("\n" + "=" * 52)
    print("   AttendX - All-Server Launcher (local)")
    print("=" * 52 + "\n")

    busy = [srv for srv in SERVERS if port_in_use(srv["port"])]
    if busy:
        print("  PORT CONFLICT - stop the other process first.")
        print("  Common cause: `python app.py` already using 5000.\n")
        for srv in busy:
            print(f"  [x] port {srv['port']} already in use ({srv['label']})")
        print("\n  Find the owning process (PowerShell):")
        print("  Get-NetTCPConnection -LocalPort 5000,5001,5002 -State Listen |")
        print("    Select-Object LocalPort, OwningProcess")
        sys.exit(1)

    for srv in SERVERS:
        proc = subprocess.Popen(
            [PYTHON, srv["script"]],
            cwd=BASE,
        )
        processes.append(proc)
        print(f"  [>] {srv['label']}  ->  {srv['url']}")

    print("\n  All three servers are starting up...")
    print("  Press Ctrl+C to stop all.\n")

    time.sleep(2.5)

    for srv in SERVERS:
        try:
            webbrowser.open(srv["url"])
        except Exception:
            pass

    try:
        for proc in processes:
            proc.wait()
    except KeyboardInterrupt:
        print("\n\n  Stopping all servers...")
        for proc in processes:
            proc.terminate()
        print("  Done. Goodbye.\n")


if __name__ == "__main__":
    main()
