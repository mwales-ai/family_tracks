import socket
import json
import base64
import threading

from Crypto.Cipher import AES

from database import getDb

UDP_LISTENER_DEBUG = False


def debugLog(msg):
    if UDP_LISTENER_DEBUG:
        print("[UDP] " + msg)


def decryptPacket(aesKeyB64, data):
    """Decrypt an AES-256-GCM encrypted packet.

    Packet format:
        [12 bytes nonce][16 bytes tag][ciphertext]
    """
    if len(data) < 28:
        debugLog("Packet too short: " + str(len(data)) + " bytes")
        return None

    key = base64.b64decode(aesKeyB64)
    nonce = data[0:12]
    tag = data[12:28]
    ciphertext = data[28:]

    try:
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
        return plaintext.decode("utf-8")
    except (ValueError, KeyError) as e:
        debugLog("Decryption failed: " + str(e))
        return None


def lookupUserByUuid(userUuid):
    """Find the user row by their UUID, return (id, aesKey) or None."""
    db = getDb()
    row = db.execute(
        "SELECT id, aesKey FROM users WHERE userId = ?", (userUuid,)
    ).fetchone()
    db.close()
    if row:
        return (row["id"], row["aesKey"])
    return None


def buildKeyCache():
    """Build a dict mapping userId UUID -> (db id, aesKey) for fast lookup."""
    db = getDb()
    rows = db.execute("SELECT id, userId, aesKey FROM users").fetchall()
    db.close()
    cache = {}
    for r in rows:
        cache[r["userId"]] = (r["id"], r["aesKey"])
    return cache


def storeLocation(dbUserId, loc):
    """Insert a location record into the database."""
    db = getDb()
    db.execute(
        "INSERT INTO locations (userId, latitude, longitude, altitude, speed, "
        "bearing, accuracy, battery, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            dbUserId,
            loc.get("lat"),
            loc.get("lon"),
            loc.get("alt"),
            loc.get("spd"),
            loc.get("brg"),
            loc.get("acc"),
            loc.get("bat"),
            loc.get("ts")
        )
    )
    db.commit()
    db.close()


def storeWorkoutData(dbUserId, loc):
    """If the packet has workout info, store it in workoutData."""
    workoutId = loc.get("wid")
    if workoutId is None:
        return

    db = getDb()
    db.execute(
        "INSERT INTO workoutData (workoutId, latitude, longitude, altitude, "
        "speed, heartRate, temperature, humidity, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            workoutId,
            loc.get("lat"),
            loc.get("lon"),
            loc.get("alt"),
            loc.get("spd"),
            loc.get("hr"),
            loc.get("temp"),
            loc.get("hum"),
            loc.get("ts")
        )
    )
    db.commit()
    db.close()


def handlePacket(data, addr, keyCache):
    """Process a single UDP packet.

    Packet format (before encryption):
        JSON with fields:
        {
            "uid": "<user uuid>",
            "lat": 39.123,
            "lon": -98.456,
            "alt": 300.0,       (optional)
            "spd": 1.5,         (optional, m/s)
            "brg": 180.0,       (optional, degrees)
            "acc": 10.0,        (optional, meters)
            "bat": 85.0,        (optional, percent)
            "ts":  "2026-04-01T12:00:00",
            "wid": 5,           (optional, workout id)
            "hr":  120,         (optional, heart rate)
            "temp": 22.5,       (optional, celsius)
            "hum": 45.0         (optional, percent)
        }

    The entire JSON is encrypted with AES-256-GCM.
    But we need the user UUID to look up the key. So the packet
    has a plaintext prefix:

    Wire format:
        [36 bytes user UUID ascii][12 bytes nonce][16 bytes tag][ciphertext]
    """
    if len(data) < 64:
        debugLog("Packet too short from " + str(addr))
        return

    userUuid = data[0:36].decode("ascii", errors="replace")
    encryptedPart = data[36:]

    debugLog("Packet from " + str(addr) + " user=" + userUuid)

    # Look up user key
    userInfo = keyCache.get(userUuid)
    if userInfo is None:
        # Cache miss - try DB directly (new user added since cache built)
        userInfo = lookupUserByUuid(userUuid)
        if userInfo is None:
            debugLog("Unknown user UUID: " + userUuid)
            return
        keyCache[userUuid] = userInfo

    dbUserId, aesKey = userInfo

    plaintext = decryptPacket(aesKey, encryptedPart)
    if plaintext is None:
        debugLog("Failed to decrypt packet from " + userUuid)
        return

    try:
        loc = json.loads(plaintext)
    except json.JSONDecodeError:
        debugLog("Invalid JSON in decrypted packet")
        return

    debugLog("Location: lat=" + str(loc.get("lat")) + " lon=" + str(loc.get("lon")))

    storeLocation(dbUserId, loc)
    storeWorkoutData(dbUserId, loc)


def runUdpListener(port):
    """Run the UDP listener in a loop."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    print("UDP listener started on port " + str(port))

    keyCache = buildKeyCache()

    while True:
        data, addr = sock.recvfrom(4096)
        try:
            handlePacket(data, addr, keyCache)
        except Exception as e:
            debugLog("Error handling packet: " + str(e))


def startUdpThread(port):
    """Start the UDP listener in a background thread."""
    thread = threading.Thread(target=runUdpListener, args=(port,), daemon=True)
    thread.start()
    return thread
