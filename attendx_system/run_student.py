"""
run_student.py — LOCAL ONLY. Not used on Vercel.

Starts the same Flask app on port 5000 with a student session cookie.
Production uses one host: /login and /dashboard/student.

Do not run this together with `python app.py` — both bind port 5000.
"""
from app import app

if __name__ == "__main__":
    print("[Student] http://localhost:5000/dashboard/student")
    print("Local only - Vercel does not use this port.")
    app.config["SESSION_COOKIE_NAME"] = "attendx_session_student"
    app.run(debug=True, host="0.0.0.0", port=5000)
