# Family Tracks - Server

Privacy-first, self-hosted family location tracker. Users own their data,
no third-party data brokers, no ads.

## Architecture

- **Backend:** Python + Flask, SQLite database (file: `data/familytracks.db`)
- **Frontend:** Plain HTML/CSS/JS + Leaflet.js with OpenStreetMap tiles
- **Protocol:** Phones send location as UDP packets encrypted with AES-256-GCM
- **Deployment:** Docker container exposing TCP 5000 (web) and UDP 5555 (location data)

## Key Files

- `app.py` - Flask routes: auth, map dashboard, admin panel, workouts, settings, REST API
- `database.py` - SQLite schema and helpers (users, locations, workouts, geofences)
- `udp_listener.py` - Receives and decrypts UDP location packets from mobile apps
- `test_send_location.py` - Test script to simulate a phone sending a location packet

## UDP Packet Wire Format

```
[36-byte user UUID ascii][12-byte nonce][16-byte GCM tag][AES-256-GCM ciphertext]
```

The encrypted payload is JSON with fields: uid, lat, lon, ts (required),
alt, spd, brg, acc, bat, wid, hr, temp, hum (optional).

## QR Code Format

Admin panel generates QR codes containing JSON:
```json
{"host": "server-ip", "port": 5555, "key": "<base64 AES-256 key>", "user_id": "<uuid>"}
```

## Running Locally

```bash
python3 app.py
```
Default admin login: admin / admin (configurable via ADMIN_PASSWORD env var)

## Related Repos

- **Android app:** github.com/mwales-ai/family_tracks_android
- **iOS app:** not yet started
