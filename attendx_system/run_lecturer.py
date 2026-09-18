"""
run_lecturer.py — Start the AttendX lecturer-facing server on port 5001.
Usage:  python run_lecturer.py
"""
from app import app

if __name__ == "__main__":
    print("[Lecturer] http://localhost:5001/dashboard/lecturer")
    app.config["SESSION_COOKIE_NAME"] = "attendx_session_lecturer"
    app.run(debug=True, host="0.0.0.0", port=5001)