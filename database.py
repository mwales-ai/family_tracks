import sqlite3
import os

DB_PATH = os.path.join("data", "familytracks.db")


def getDb():
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initDb():
    """Create all tables if they don't exist."""
    os.makedirs("data", exist_ok=True)
    conn = getDb()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            displayName TEXT NOT NULL,
            passwordHash TEXT NOT NULL,
            isAdmin     INTEGER NOT NULL DEFAULT 0,
            avatarPath  TEXT,
            timezone    TEXT DEFAULT 'UTC',
            units       TEXT DEFAULT 'metric',
            aesKey      TEXT,
            userId      TEXT UNIQUE,
            createdAt   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS locations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            userId      INTEGER NOT NULL,
            latitude    REAL NOT NULL,
            longitude   REAL NOT NULL,
            altitude    REAL,
            speed       REAL,
            bearing     REAL,
            accuracy    REAL,
            battery     REAL,
            timestamp   TIMESTAMP NOT NULL,
            receivedAt  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (userId) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_locations_user_time
            ON locations(userId, timestamp);

        CREATE TABLE IF NOT EXISTS workouts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            userId      INTEGER NOT NULL,
            name        TEXT,
            startTime   TIMESTAMP NOT NULL,
            endTime     TIMESTAMP,
            workoutType TEXT,
            notes       TEXT,
            FOREIGN KEY (userId) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_workouts_user_time
            ON workouts(userId, startTime);

        CREATE TABLE IF NOT EXISTS workoutData (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            workoutId   INTEGER NOT NULL,
            latitude    REAL NOT NULL,
            longitude   REAL NOT NULL,
            altitude    REAL,
            speed       REAL,
            heartRate   INTEGER,
            temperature REAL,
            humidity    REAL,
            timestamp   TIMESTAMP NOT NULL,
            FOREIGN KEY (workoutId) REFERENCES workouts(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_workoutdata_workout
            ON workoutData(workoutId, timestamp);

        CREATE TABLE IF NOT EXISTS geofences (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            userId      INTEGER NOT NULL,
            name        TEXT NOT NULL,
            latitude    REAL NOT NULL,
            longitude   REAL NOT NULL,
            radiusMeters REAL NOT NULL DEFAULT 100,
            FOREIGN KEY (userId) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS geofenceEvents (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            userId      INTEGER NOT NULL,
            geofenceId  INTEGER NOT NULL,
            eventType   TEXT NOT NULL,
            timestamp   TIMESTAMP NOT NULL,
            FOREIGN KEY (userId) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (geofenceId) REFERENCES geofences(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_geofence_events_time
            ON geofenceEvents(timestamp DESC);

        CREATE INDEX IF NOT EXISTS idx_geofence_events_user
            ON geofenceEvents(userId, timestamp DESC);

        CREATE TABLE IF NOT EXISTS trackingEvents (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            userId      INTEGER NOT NULL,
            eventType   TEXT NOT NULL,
            timestamp   TIMESTAMP NOT NULL,
            FOREIGN KEY (userId) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_tracking_events_time
            ON trackingEvents(timestamp DESC);
    """)

    conn.commit()

    # Migrations for existing databases
    existingCols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "timezone" not in existingCols:
        conn.execute("ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'UTC'")
        conn.commit()
    if "units" not in existingCols:
        conn.execute("ALTER TABLE users ADD COLUMN units TEXT DEFAULT 'metric'")
        conn.commit()

    conn.close()
