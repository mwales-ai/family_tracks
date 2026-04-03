"""Test script to send a fake encrypted location via UDP.

Usage:
    python3 test_send_location.py [host] [port]

This connects to the database to look up the first user's key,
then sends an encrypted location packet to the UDP listener.
"""

import socket
import json
import base64
import os
import sys
from datetime import datetime

from Crypto.Cipher import AES

from database import getDb


def sendTestLocation(host, port):
    db = getDb()
    row = db.execute("SELECT userId, aesKey FROM users LIMIT 1").fetchone()
    db.close()

    if not row:
        print("No users in database. Run the app first to create the admin user.")
        return

    userUuid = row["userId"]
    aesKey = row["aesKey"]
    keyBytes = base64.b64decode(aesKey)

    print("Sending as user: " + userUuid)
    print("Using key: " + aesKey[:16] + "...")

    # Build location payload
    payload = json.dumps({
        "uid": userUuid,
        "lat": 39.7392,
        "lon": -104.9903,
        "alt": 1609.0,
        "spd": 1.2,
        "brg": 45.0,
        "acc": 5.0,
        "bat": 87.0,
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    })

    # Encrypt with AES-256-GCM (12-byte nonce)
    nonce = os.urandom(12)
    cipher = AES.new(keyBytes, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(payload.encode("utf-8"))

    # Wire format: [36-byte UUID][12-byte nonce][16-byte tag][ciphertext]
    packet = userUuid.encode("ascii") + nonce + tag + ciphertext

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(packet, (host, port))
    sock.close()

    print("Sent " + str(len(packet)) + " bytes to " + host + ":" + str(port))
    print("Location: Denver, CO (39.7392, -104.9903)")


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5555
    sendTestLocation(host, port)
