"""
run_student.py — Start the AttendX student-facing server on port 5000.
Usage:  python run_student.py
"""
from app import app

if __name__ == "__main__":
    print("[Student] http://localhost:5000/dashboard/student")
    app.config["SESSION_COOKIE_NAME"] = "attendx_session_student"
    app.run(debug=True, host="0.0.0.0", port=5000)
