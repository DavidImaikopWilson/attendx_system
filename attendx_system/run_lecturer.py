"""
run_lecturer.py — LOCAL ONLY. Not used on Vercel.

Starts the same Flask app on port 5001 with a lecturer session cookie.
Production uses one host: /login and /dashboard/lecturer.
"""
from app import app

if __name__ == "__main__":
    print("[Lecturer] http://localhost:5001/dashboard/lecturer")
    print("Local only - Vercel does not use this port.")
    app.config["SESSION_COOKIE_NAME"] = "attendx_session_lecturer"
    app.run(debug=True, host="0.0.0.0", port=5001)
