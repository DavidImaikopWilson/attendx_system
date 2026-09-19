"""
run_admin.py — LOCAL ONLY. Not used on Vercel.

Starts the same Flask app on port 5002 with an admin session cookie.
Production uses one host: /login and /dashboard/admin.
"""
from app import app

if __name__ == "__main__":
    print("[Admin] http://localhost:5002/dashboard/admin")
    print("Local only - Vercel does not use this port.")
    app.config["SESSION_COOKIE_NAME"] = "attendx_session_admin"
    app.run(debug=True, host="0.0.0.0", port=5002)
