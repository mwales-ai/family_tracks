import os
import uuid
import base64
import io
import json
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode

from database import initDb, getDb
from udp_listener import startUdpThread

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

loginManager = LoginManager()
loginManager.init_app(app)
loginManager.login_view = "login"


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.displayName = row["displayName"]
        self.isAdmin = bool(row["isAdmin"])
        self.avatarPath = row["avatarPath"]


@loginManager.user_loader
def loadUser(userId):
    db = getDb()
    row = db.execute("SELECT * FROM users WHERE id = ?", (userId,)).fetchone()
    db.close()
    if row is None:
        return None
    return User(row)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        db = getDb()
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        db.close()

        if row and check_password_hash(row["passwordHash"], password):
            login_user(User(row))
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard / Map
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


# ---------------------------------------------------------------------------
# API: locations
# ---------------------------------------------------------------------------

@app.route("/api/locations/latest")
@login_required
def apiLatestLocations():
    """Get the most recent location for each user."""
    db = getDb()
    rows = db.execute("""
        SELECT u.id, u.displayName, u.avatarPath, l.latitude, l.longitude,
               l.speed, l.battery, l.timestamp
        FROM users u
        JOIN locations l ON l.userId = u.id
        WHERE l.id = (
            SELECT l2.id FROM locations l2
            WHERE l2.userId = u.id
            ORDER BY l2.timestamp DESC
            LIMIT 1
        )
    """).fetchall()
    db.close()

    result = []
    for r in rows:
        result.append({
            "userId": r["id"],
            "displayName": r["displayName"],
            "avatarPath": r["avatarPath"],
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "speed": r["speed"],
            "battery": r["battery"],
            "timestamp": r["timestamp"]
        })

    return jsonify(result)


@app.route("/api/locations/history")
@login_required
def apiLocationHistory():
    """Get location history for a user within a date range."""
    userId = request.args.get("userId", type=int)
    startTime = request.args.get("start")
    endTime = request.args.get("end")

    if not userId:
        return jsonify({"error": "userId required"}), 400

    db = getDb()

    if startTime and endTime:
        rows = db.execute("""
            SELECT latitude, longitude, altitude, speed, bearing, accuracy,
                   battery, timestamp
            FROM locations
            WHERE userId = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """, (userId, startTime, endTime)).fetchall()
    else:
        # Default: last 24 hours
        rows = db.execute("""
            SELECT latitude, longitude, altitude, speed, bearing, accuracy,
                   battery, timestamp
            FROM locations
            WHERE userId = ? AND timestamp >= datetime('now', '-1 day')
            ORDER BY timestamp ASC
        """, (userId,)).fetchall()

    db.close()

    result = []
    for r in rows:
        result.append({
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "altitude": r["altitude"],
            "speed": r["speed"],
            "bearing": r["bearing"],
            "accuracy": r["accuracy"],
            "battery": r["battery"],
            "timestamp": r["timestamp"]
        })

    return jsonify(result)


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route("/admin")
@login_required
def admin():
    if not current_user.isAdmin:
        flash("Access denied.", "error")
        return redirect(url_for("dashboard"))

    db = getDb()
    users = db.execute("SELECT * FROM users ORDER BY username").fetchall()
    db.close()

    return render_template("admin.html", users=users)


@app.route("/admin/adduser", methods=["POST"])
@login_required
def adminAddUser():
    if not current_user.isAdmin:
        return redirect(url_for("dashboard"))

    username = request.form.get("username", "").strip()
    displayName = request.form.get("displayName", "").strip()
    password = request.form.get("password", "")
    isAdmin = 1 if request.form.get("isAdmin") else 0

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("admin"))

    if not displayName:
        displayName = username

    # Generate AES-256 key and unique user ID for UDP communication
    aesKey = base64.b64encode(os.urandom(32)).decode("ascii")
    userUuid = str(uuid.uuid4())

    db = getDb()
    try:
        db.execute(
            "INSERT INTO users (username, displayName, passwordHash, isAdmin, aesKey, userId) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, displayName, generate_password_hash(password), isAdmin, aesKey, userUuid)
        )
        db.commit()
        flash(f"User '{username}' created.", "success")
    except sqlite3.IntegrityError:
        flash(f"Username '{username}' already exists.", "error")
    finally:
        db.close()

    return redirect(url_for("admin"))


@app.route("/admin/deleteuser/<int:userId>", methods=["POST"])
@login_required
def adminDeleteUser(userId):
    if not current_user.isAdmin:
        return redirect(url_for("dashboard"))

    if userId == current_user.id:
        flash("You cannot delete yourself.", "error")
        return redirect(url_for("admin"))

    db = getDb()
    db.execute("DELETE FROM users WHERE id = ?", (userId,))
    db.commit()
    db.close()

    flash("User deleted.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/qrcode/<int:userId>")
@login_required
def adminQrCode(userId):
    """Generate a QR code containing the connection info for a user."""
    if not current_user.isAdmin:
        return redirect(url_for("dashboard"))

    db = getDb()
    row = db.execute("SELECT userId, aesKey FROM users WHERE id = ?", (userId,)).fetchone()
    db.close()

    if not row:
        flash("User not found.", "error")
        return redirect(url_for("admin"))

    host = request.host.split(":")[0]
    udpPort = int(os.environ.get("UDP_PORT", 5555))

    qrData = json.dumps({
        "host": host,
        "port": udpPort,
        "key": row["aesKey"],
        "user_id": row["userId"]
    })

    img = qrcode.make(qrData)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return send_file(buf, mimetype="image/png")


# ---------------------------------------------------------------------------
# Workouts
# ---------------------------------------------------------------------------

@app.route("/workouts")
@login_required
def workouts():
    db = getDb()
    rows = db.execute("""
        SELECT w.*, u.displayName
        FROM workouts w
        JOIN users u ON u.id = w.userId
        ORDER BY w.startTime DESC
        LIMIT 50
    """).fetchall()
    db.close()
    return render_template("workouts.html", workouts=rows)


@app.route("/workouts/<int:workoutId>")
@login_required
def workoutDetail(workoutId):
    db = getDb()
    workout = db.execute("""
        SELECT w.*, u.displayName
        FROM workouts w
        JOIN users u ON u.id = w.userId
        WHERE w.id = ?
    """, (workoutId,)).fetchone()

    if not workout:
        db.close()
        flash("Workout not found.", "error")
        return redirect(url_for("workouts"))

    db.close()
    return render_template("workout_detail.html", workout=workout)


@app.route("/api/workouts/<int:workoutId>/data")
@login_required
def apiWorkoutData(workoutId):
    """Get all data points for a workout."""
    db = getDb()
    rows = db.execute("""
        SELECT latitude, longitude, altitude, speed, heartRate,
               temperature, humidity, timestamp
        FROM workoutData
        WHERE workoutId = ?
        ORDER BY timestamp ASC
    """, (workoutId,)).fetchall()
    db.close()

    result = []
    for r in rows:
        result.append({
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "altitude": r["altitude"],
            "speed": r["speed"],
            "heartRate": r["heartRate"],
            "temperature": r["temperature"],
            "humidity": r["humidity"],
            "timestamp": r["timestamp"]
        })

    return jsonify(result)


@app.route("/api/workouts/start", methods=["POST"])
@login_required
def apiStartWorkout():
    """Start a new workout (called from mobile app or web)."""
    data = request.get_json()
    name = data.get("name", "Workout")
    workoutType = data.get("type", "general")

    db = getDb()
    cursor = db.execute(
        "INSERT INTO workouts (userId, name, startTime, workoutType) VALUES (?, ?, datetime('now'), ?)",
        (current_user.id, name, workoutType)
    )
    workoutId = cursor.lastrowid
    db.commit()
    db.close()

    return jsonify({"workoutId": workoutId})


@app.route("/api/workouts/<int:workoutId>/stop", methods=["POST"])
@login_required
def apiStopWorkout(workoutId):
    """End a workout."""
    db = getDb()
    db.execute(
        "UPDATE workouts SET endTime = datetime('now') WHERE id = ? AND userId = ?",
        (workoutId, current_user.id)
    )
    db.commit()
    db.close()

    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        newPassword = request.form.get("newPassword", "")
        confirmPassword = request.form.get("confirmPassword", "")

        if newPassword and newPassword == confirmPassword:
            db = getDb()
            db.execute(
                "UPDATE users SET passwordHash = ? WHERE id = ?",
                (generate_password_hash(newPassword), current_user.id)
            )
            db.commit()
            db.close()
            flash("Password updated.", "success")
        elif newPassword:
            flash("Passwords do not match.", "error")

    db = getDb()
    geofences = db.execute(
        "SELECT * FROM geofences WHERE userId = ? ORDER BY name", (current_user.id,)
    ).fetchall()
    db.close()

    return render_template("settings.html", geofences=geofences)


@app.route("/settings/deletehistory", methods=["POST"])
@login_required
def deleteHistory():
    db = getDb()
    db.execute("DELETE FROM locations WHERE userId = ?", (current_user.id,))
    db.commit()
    db.close()
    flash("Location history deleted.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/geofence/add", methods=["POST"])
@login_required
def addGeofence():
    name = request.form.get("name", "").strip()
    lat = request.form.get("latitude", type=float)
    lon = request.form.get("longitude", type=float)
    radius = request.form.get("radius", 100, type=float)

    if not name or lat is None or lon is None:
        flash("Name, latitude, and longitude are required.", "error")
        return redirect(url_for("settings"))

    db = getDb()
    db.execute(
        "INSERT INTO geofences (userId, name, latitude, longitude, radiusMeters) "
        "VALUES (?, ?, ?, ?, ?)",
        (current_user.id, name, lat, lon, radius)
    )
    db.commit()
    db.close()

    flash("Geofence '" + name + "' added.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/geofence/delete/<int:fenceId>", methods=["POST"])
@login_required
def deleteGeofence(fenceId):
    db = getDb()
    db.execute(
        "DELETE FROM geofences WHERE id = ? AND userId = ?",
        (fenceId, current_user.id)
    )
    db.commit()
    db.close()

    flash("Geofence deleted.", "success")
    return redirect(url_for("settings"))


@app.route("/api/geofences")
@login_required
def apiGeofences():
    """Get all geofences for the current user."""
    db = getDb()
    rows = db.execute(
        "SELECT * FROM geofences WHERE userId = ? ORDER BY name",
        (current_user.id,)
    ).fetchall()
    db.close()

    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "name": r["name"],
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "radiusMeters": r["radiusMeters"]
        })

    return jsonify(result)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def createDefaultAdmin():
    """Create the admin account if no users exist."""
    db = getDb()
    count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if count == 0:
        adminPassword = os.environ.get("ADMIN_PASSWORD", "admin")
        aesKey = base64.b64encode(os.urandom(32)).decode("ascii")
        userUuid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO users (username, displayName, passwordHash, isAdmin, aesKey, userId) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("admin", "Admin", generate_password_hash(adminPassword), 1, aesKey, userUuid)
        )
        db.commit()
        print(f"Default admin account created (username: admin)")
    db.close()


if __name__ == "__main__":
    initDb()
    createDefaultAdmin()

    udpPort = int(os.environ.get("UDP_PORT", 5555))
    startUdpThread(udpPort)

    app.run(host="0.0.0.0", port=5000, debug=True)
