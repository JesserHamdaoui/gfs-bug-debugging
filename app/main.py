import time
from datetime import date
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

app = Flask(__name__)
app.secret_key = "dev-secret-key"

DATABASE_URL = "postgresql://postgres:postgres@localhost:55000/postgres"

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn

@app.route("/")
def index():
    return redirect(url_for("signin"))

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"].strip()
        password = request.form["password"]
        if not email or not password:
            flash("Email and password are required.", "error")
        else:
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO users (email, password) VALUES (%s, %s)",
                            (email, password)
                        )
                session["user_email"] = email
                return redirect(url_for("billing"))
            except psycopg2.errors.UniqueViolation:
                flash("An account with that email already exists.", "error")
    return render_template("signup.html")

@app.route("/signin", methods=["GET", "POST"])
def signin():
    if request.method == "POST":
        email = request.form["email"].strip()
        password = request.form["password"]
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id FROM users WHERE email = %s AND password = %s",
                    (email, password)
                )
                user = cur.fetchone()
        if user:
            session["user_email"] = email
            return redirect(url_for("billing"))
        flash("Invalid email or password.", "error")
    return render_template("signin.html")

@app.route("/billing")
def billing():
    if "user_email" not in session:
        return redirect(url_for("signin"))
    return render_template("billing.html", user=session["user_email"])

@app.route("/subscribe", methods=["POST"])
def subscribe():
    if "user_email" not in session:
        return jsonify({"error": "unauthenticated"}), 401

    plan = request.json.get("plan", "pro")
    price = 12.00 if plan == "pro" else 49.00
    period = date.today().replace(day=1).isoformat()

    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (session["user_email"],))
            user = cur.fetchone()

            # BUG: check and insert are not atomic — a second concurrent request
            # passes this check before the first one commits, creating duplicates
            cur.execute(
                "SELECT id FROM invoices WHERE user_id = %s AND period = %s",
                (user["id"], period)
            )
            if cur.fetchone():
                return jsonify({"error": "Invoice already exists for this period"}), 409

            time.sleep(0.5)

            cur.execute(
                "INSERT INTO invoices (user_id, period, price) VALUES (%s, %s, %s)",
                (user["id"], period, price)
            )

    return jsonify({"ok": True, "period": period, "price": price})

@app.route("/invoices")
def invoices():
    if "user_email" not in session:
        return jsonify([])
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT i.period, i.price, i.created_at
                   FROM invoices i JOIN users u ON u.id = i.user_id
                   WHERE u.email = %s ORDER BY i.created_at DESC""",
                (session["user_email"],)
            )
            rows = cur.fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/logout")
def logout():
    session.pop("user_email", None)
    return redirect(url_for("signin"))

if __name__ == "__main__":
    app.run(debug=True, port=8080, threaded=True)
