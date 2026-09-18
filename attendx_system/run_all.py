"""
run_all.py — Launch all three AttendX servers simultaneously.

Usage:  python run_all.py

Ports:
  5000  →  Student  dashboard   http://localhost:5000/dashboard/student
  5001  →  Lecturer dashboard   http://localhost:5001/dashboard/lecturer
  5002  →  Admin    dashboard   http://localhost:5002/dashboard/admin
"""
import subprocess
import sys
import time
import webbrowser
import os

PYTHON = sys.executable
BASE   = os.path.dirname(os.path.abspath(__file__))

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

def main():
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("   AttendX — All-Server Launcher")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    for srv in SERVERS:
        proc = subprocess.Popen(
            [PYTHON, srv["script"]],
            cwd=BASE,
        )
        processes.append(proc)
        print(f"  ▶  {srv['label']}  →  {srv['url']}")

    print("\n  All three servers are starting up...")
    print("  Press Ctrl+C to stop all.\n")

    # Give servers a moment to bind their ports before opening browsers
    time.sleep(2.5)

    for srv in SERVERS:
        try:
            webbrowser.open(srv["url"])
        except Exception:
            pass

    try:
        # Keep main process alive; forward Ctrl+C to children
        for proc in processes:
            proc.wait()
    except KeyboardInterrupt:
        print("\n\n  Stopping all servers…")
        for proc in processes:
            proc.terminate()
        print("  Done. Goodbye.\n")


if __name__ == "__main__":
    main()
