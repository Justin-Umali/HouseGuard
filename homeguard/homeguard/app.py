"""HomeGuard web app + detection engine entry point.

Run:  python app.py
"""
import hmac
import os
import secrets
import time
from functools import wraps

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

from homeguard import config, db, notifier, settings
from homeguard.engine import LEVEL_NAMES, Engine

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("HG_HTTPS") == "1")

engine = Engine()
_login_attempts: dict[str, list[float]] = {}


# ---------- security helpers ----------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("auth"):
            if request.path.startswith("/api/") or request.path == "/video_feed":
                abort(401)
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Bad CSRF token")


@app.after_request
def no_indexing(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["LEVEL_NAMES"] = LEVEL_NAMES


@app.template_filter("dt")
def fmt_dt(ts):
    return time.strftime("%b %d, %H:%M:%S", time.localtime(ts)) if ts else "-"


# ---------- auth ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "?"
        recent = [t for t in _login_attempts.get(ip, []) if time.time() - t < 300]
        if len(recent) >= 5:
            flash("Too many attempts. Wait 5 minutes.", "error")
            return render_template("login.html"), 429
        pw = request.form.get("password", "")
        if config.ADMIN_PASSWORD and hmac.compare_digest(pw, config.ADMIN_PASSWORD):
            session.clear()
            session["auth"] = True
            return redirect(url_for("dashboard"))
        recent.append(time.time())
        _login_attempts[ip] = recent
        flash("Wrong password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- pages ----------
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", status=engine.status(), events=db.recent(8))


@app.route("/events")
@login_required
def events():
    min_level = request.args.get("level", 1, type=int)
    return render_template("events.html", events=db.recent(100, min_level),
                           counts=db.counts(), min_level=min_level)


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    if request.method == "POST":
        f = request.form

        def num(name, lo, hi, cast=float):
            try:
                return max(lo, min(hi, cast(f.get(name, ""))))
            except ValueError:
                return settings.load()[name]

        settings.save({
            "armed": f.get("armed") == "on",
            "require_pir": f.get("require_pir") == "on",
            "recipients": f.get("recipients", "").strip(),
            "ssd_confidence": num("ssd_confidence", 0.2, 0.95),
            "hog_threshold": num("hog_threshold", 0.1, 3.0),
            "confirm_window": num("confirm_window", 2, 60, int),
            "danger_seconds": num("danger_seconds", 1, 60, int),
            "cooldown": num("cooldown", 10, 3600, int),
            "detect_interval": num("detect_interval", 0.2, 5.0),
        })
        flash("Settings saved.", "ok")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", s=settings.load(),
                           smtp_ok=notifier.smtp_configured(), status=engine.status())


# ---------- media + API ----------
@app.route("/video_feed")
@login_required
def video_feed():
    def gen():
        while True:
            jpg = engine.annotated_jpeg()
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.15)
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/snapshots/<path:name>")
@login_required
def snapshot(name):
    return send_from_directory(config.SNAP_DIR, name)


@app.route("/api/status")
@login_required
def api_status():
    return jsonify(engine.status())


@app.route("/api/simulate_motion", methods=["POST"])
@login_required
def api_simulate_motion():
    engine.pir.simulate(10)
    return jsonify(ok=True, message="Simulated PIR motion for 10 seconds.")


@app.route("/api/test_email", methods=["POST"])
@login_required
def api_test_email():
    s = settings.load()
    ok, info = notifier.send_email("HomeGuard test email",
                                   "If you can read this, HomeGuard email alerts work.",
                                   s["recipients"])
    return jsonify(ok=ok, message=info)


if __name__ == "__main__":
    if not config.ADMIN_PASSWORD or config.ADMIN_PASSWORD in ("change-me", "password"):
        raise SystemExit("Set a strong HG_PASSWORD in .env before starting (see .env.example).")
    engine.start()
    print(f"[homeguard] detector: {engine.detector.mode} | PIR hardware: {engine.pir.hardware}")
    print(f"[homeguard] open http://<pi-ip>:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, threaded=True, debug=False)
