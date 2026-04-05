import os
import uuid
import base64
import io
import json
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode

from database import initDb, getDb
from udp_listener import startUdpThread

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
app.permanent_session_lifetime = timedelta(days=7)

# Rate limiting: track failed login attempts per IP
theLoginAttempts = defaultdict(list)
LOGIN_RATE_LIMIT = 5       # max attempts
LOGIN_RATE_WINDOW = 300    # per 5 minutes


def isRateLimited(ip):
    """Check if an IP has exceeded the login rate limit."""
    now = time.time()
    # Prune old entries
    theLoginAttempts[ip] = [t for t in theLoginAttempts[ip] if now - t < LOGIN_RATE_WINDOW]
    return len(theLoginAttempts[ip]) >= LOGIN_RATE_LIMIT


def recordFailedLogin(ip):
    """Record a failed login attempt."""
    theLoginAttempts[ip].append(time.time())

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
        ip = request.remote_addr

        if isRateLimited(ip):
            flash("Too many login attempts. Try again in a few minutes.", "error")
            return render_template("login.html")

        username = request.form.get("username", "").strip()[:64]
        password = request.form.get("password", "")[:256]

        db = getDb()
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        db.close()

        if row and check_password_hash(row["passwordHash"], password):
            login_user(User(row), remember=True)
            return redirect(url_for("dashboard"))

        recordFailedLogin(ip)
        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/api/auth/token", methods=["POST"])
def apiTokenLogin():
    """Login via user_id + AES key (used by mobile app WebView).

    The mobile app already has these from the QR code, so this
    avoids making the user type a password in the app.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    userId = data.get("user_id", "")
    key = data.get("key", "")

    db = getDb()
    row = db.execute("SELECT * FROM users WHERE userId = ?", (userId,)).fetchone()
    db.close()

    if row and row["aesKey"] == key:
        login_user(User(row))
        return jsonify({"status": "ok", "displayName": row["displayName"]})

    return jsonify({"error": "invalid credentials"}), 401


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


@app.route("/events")
@login_required
def events():
    return render_template("events.html")


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


@app.route("/api/geofence-events")
@login_required
def apiGeofenceEvents():
    """Get recent geofence events for all users."""
    limit = request.args.get("limit", 50, type=int)
    db = getDb()
    rows = db.execute("""
        SELECT ge.id, ge.eventType, ge.timestamp,
               u.displayName,
               g.name as geofenceName
        FROM geofenceEvents ge
        JOIN users u ON u.id = ge.userId
        JOIN geofences g ON g.id = ge.geofenceId
        ORDER BY ge.timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()
    db.close()

    result = []
    for r in rows:
        verb = "arrived at" if r["eventType"] == "enter" else "left"
        result.append({
            "id": r["id"],
            "displayName": r["displayName"],
            "geofenceName": r["geofenceName"],
            "eventType": r["eventType"],
            "message": r["displayName"] + " " + verb + " " + r["geofenceName"],
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


@app.route("/api/locations/export")
@login_required
def apiExportGpx():
    """Export location history as a GPX file."""
    userId = request.args.get("userId", type=int)
    startTime = request.args.get("start")
    endTime = request.args.get("end")

    if not userId:
        return jsonify({"error": "userId required"}), 400

    db = getDb()

    user = db.execute("SELECT displayName FROM users WHERE id = ?", (userId,)).fetchone()
    if not user:
        db.close()
        return jsonify({"error": "user not found"}), 404

    if startTime and endTime:
        rows = db.execute("""
            SELECT latitude, longitude, altitude, speed, timestamp
            FROM locations
            WHERE userId = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """, (userId, startTime, endTime)).fetchall()
    else:
        rows = db.execute("""
            SELECT latitude, longitude, altitude, speed, timestamp
            FROM locations
            WHERE userId = ? AND timestamp >= datetime('now', '-1 day')
            ORDER BY timestamp ASC
        """, (userId,)).fetchall()

    db.close()

    gpx = '<?xml version="1.0" encoding="UTF-8"?>\n'
    gpx += '<gpx version="1.1" creator="FamilyTracks">\n'
    gpx += '  <trk>\n'
    gpx += '    <name>' + user["displayName"] + '</name>\n'
    gpx += '    <trkseg>\n'

    for r in rows:
        gpx += '      <trkpt lat="' + str(r["latitude"]) + '" lon="' + str(r["longitude"]) + '">\n'
        if r["altitude"] is not None:
            gpx += '        <ele>' + str(r["altitude"]) + '</ele>\n'
        if r["timestamp"]:
            gpx += '        <time>' + str(r["timestamp"]) + '</time>\n'
        if r["speed"] is not None:
            gpx += '        <speed>' + str(r["speed"]) + '</speed>\n'
        gpx += '      </trkpt>\n'

    gpx += '    </trkseg>\n'
    gpx += '  </trk>\n'
    gpx += '</gpx>\n'

    buf = io.BytesIO(gpx.encode("utf-8"))
    buf.seek(0)
    filename = user["displayName"].replace(" ", "_") + "_track.gpx"
    return send_file(buf, mimetype="application/gpx+xml",
                     as_attachment=True, download_name=filename)


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

    username = request.form.get("username", "").strip()[:64]
    displayName = request.form.get("displayName", "").strip()[:64]
    password = request.form.get("password", "")[:256]
    isAdmin = 1 if request.form.get("isAdmin") else 0

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("admin"))

    if len(password) < 4:
        flash("Password must be at least 4 characters.", "error")
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


@app.route("/admin/backup")
@login_required
def adminBackup():
    """Download a copy of the database file."""
    if not current_user.isAdmin:
        return redirect(url_for("dashboard"))

    from database import DB_PATH
    if not os.path.exists(DB_PATH):
        flash("Database not found.", "error")
        return redirect(url_for("admin"))

    return send_file(DB_PATH, as_attachment=True, download_name="familytracks_backup.db")


@app.route("/admin/restore", methods=["POST"])
@login_required
def adminRestore():
    """Restore the database from an uploaded file."""
    if not current_user.isAdmin:
        return redirect(url_for("dashboard"))

    if "dbfile" not in request.files:
        flash("No file selected.", "error")
        return redirect(url_for("admin"))

    f = request.files["dbfile"]
    if not f.filename:
        flash("No file selected.", "error")
        return redirect(url_for("admin"))

    from database import DB_PATH
    import shutil

    # Save backup of current DB
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, DB_PATH + ".bak")

    f.save(DB_PATH)
    flash("Database restored. Previous database saved as .bak file.", "success")
    return redirect(url_for("admin"))


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


@app.route("/settings/avatar", methods=["POST"])
@login_required
def uploadAvatar():
    if "avatar" not in request.files:
        flash("No file selected.", "error")
        return redirect(url_for("settings"))

    f = request.files["avatar"]
    if not f.filename:
        flash("No file selected.", "error")
        return redirect(url_for("settings"))

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
        flash("Invalid image format. Use JPG, PNG, GIF, or WebP.", "error")
        return redirect(url_for("settings"))

    avatarDir = os.path.join("data", "avatars")
    os.makedirs(avatarDir, exist_ok=True)

    filename = str(current_user.id) + "." + ext
    filepath = os.path.join(avatarDir, filename)
    f.save(filepath)

    db = getDb()
    db.execute("UPDATE users SET avatarPath = ? WHERE id = ?", (filename, current_user.id))
    db.commit()
    db.close()

    flash("Avatar updated.", "success")
    return redirect(url_for("settings"))


@app.route("/avatar/<filename>")
def serveAvatar(filename):
    """Serve avatar images from data/avatars/."""
    avatarDir = os.path.join("data", "avatars")
    return send_file(os.path.join(avatarDir, filename))


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
    name = request.form.get("name", "").strip()[:64]
    lat = request.form.get("latitude", type=float)
    lon = request.form.get("longitude", type=float)
    radius = request.form.get("radius", 100, type=float)

    if not name or lat is None or lon is None:
        flash("Name, latitude, and longitude are required.", "error")
        return redirect(url_for("settings"))

    if lat < -90 or lat > 90 or lon < -180 or lon > 180:
        flash("Invalid coordinates.", "error")
        return redirect(url_for("settings"))

    if radius < 10 or radius > 50000:
        radius = max(10, min(50000, radius))

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


@app.route("/api/geofences/all")
@login_required
def apiAllGeofences():
    """Get all geofences for all users (used by mobile app sync)."""
    db = getDb()
    rows = db.execute("""
        SELECT g.*, u.displayName as ownerName
        FROM geofences g
        JOIN users u ON u.id = g.userId
        ORDER BY g.name
    """).fetchall()
    db.close()

    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "name": r["name"],
            "ownerName": r["ownerName"],
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
