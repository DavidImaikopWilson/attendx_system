"""
run_admin.py — Start the AttendX admin-facing server on port 5002.
Usage:  python run_admin.py
"""
from app import app

if __name__ == "__main__":
    print("[Admin] http://localhost:5002/dashboard/admin")
    app.config["SESSION_COOKIE_NAME"] = "attendx_session_admin"
    app.run(debug=True, host="0.0.0.0", port=5002)