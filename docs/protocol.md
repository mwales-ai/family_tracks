# Family Tracks — Protocol ICD

Interface Control Document for the protocols used between the Family Tracks
mobile app and server. Intended as a starting point for independent security
review.

Revision: 2026-04-22 (matches server commit at time of writing).

## 1. System Overview

Family Tracks has two network-facing components:

1. **Server** — Python/Flask app backed by SQLite. Listens on:
   - TCP (HTTP/HTTPS) for the web dashboard, admin panel, and REST API.
   - UDP for encrypted location packets from mobile clients.
2. **Mobile app** (Android today, iOS planned) — scans a QR code once to
   acquire its credentials, then sends periodic encrypted location packets
   over UDP and talks to the REST API over TCP.

Typical deployment: Docker container behind an nginx reverse proxy with
Let's Encrypt TLS. UDP is exposed directly; HTTPS is terminated at nginx.

## 2. Threat Model (Design Assumptions)

In scope:
- Passive network eavesdropper who can observe UDP and HTTP(S) traffic on
  the wire.
- Active attacker who can inject, replay, drop, or reorder UDP packets.
- Lost or stolen mobile device (once reported, the admin can revoke credentials).
- Shared-infrastructure attacker who can observe traffic metadata
  (source IP, packet size, timing) but cannot break AES-256-GCM.

Out of scope (explicitly — document here when this changes):
- Attacker with root on the server or physical access to the SQLite DB.
- Attacker who has compromised the mobile OS keystore.
- Side-channel / traffic-analysis attacks on UDP packet timing and size.
- Forward secrecy of historical UDP traffic: the per-user AES key is
  long-lived and re-used until the admin rotates it. An attacker who later
  steals the key can decrypt all captured ciphertext.

## 3. Credential Provisioning (QR Code)

Credentials are provisioned once, out-of-band, via a QR code shown in the
admin panel.

**QR payload** — JSON, scanned by the mobile app:

```json
{
  "host":     "server.example.com",
  "port":     5555,
  "web_port": 443,
  "scheme":   "https",
  "key":      "<base64-encoded 32-byte AES-256 key>",
  "user_id":  "<36-char UUID v4>"
}
```

Fields:
- `host` — fully-qualified domain or IP of the server.
- `port` — UDP port for encrypted location packets.
- `web_port` + `scheme` — HTTPS endpoint used for the REST API and WebView map.
- `key` — 32 random bytes, base64-encoded, generated per-user at creation.
- `user_id` — random UUID, also generated at user creation. Identifies the
  user in the UDP wire header (see §4).

Both `key` and `user_id` are stored in cleartext in the SQLite `users` table
(`aesKey`, `userId` columns). An attacker with file-system access to the DB
can impersonate any user.

The QR code is served only to authenticated admins at
`GET /admin/qrcode/<userId>` and is not cached by the server.

## 4. UDP Location Protocol

### 4.1 Wire format

Each UDP datagram has this structure (all fields contiguous, no framing):

```
offset  size  field
------  ----  -----------------------------------------
0       36    user_id as ASCII (UUID string, hyphenated)
36      12    AES-GCM nonce (random per packet)
48      16    AES-GCM authentication tag
64      N     AES-256-GCM ciphertext (JSON payload)
```

Minimum valid packet size: 64 bytes (empty ciphertext — rejected at the
JSON-parsing layer, but passes initial length check). Practical minimum
is ~130 bytes for the smallest required JSON body.

The `user_id` prefix is **cleartext on the wire**. It is used by the server
to look up the correct decryption key. This leaks the identity of the
sender to any observer. See §8 for mitigation ideas.

### 4.2 Cryptography

- Algorithm: AES-256-GCM, 256-bit key.
- Nonce: 96 bits (12 bytes), expected to be random per packet. The mobile
  client is responsible for generating fresh randomness. The server does
  **not** track nonces or reject reuse.
- Authentication tag: 128 bits (16 bytes), verified by `decrypt_and_verify`.
  Packets failing authentication are silently dropped.
- Associated data (AAD): **none**. In particular, the cleartext `user_id`
  prefix is NOT bound to the ciphertext by AAD, so an attacker could
  substitute a different victim's `user_id` in front of a ciphertext;
  however, the substituted packet would fail GCM authentication under the
  victim's key (different key) and be dropped.

### 4.3 Decrypted JSON payload

The plaintext inside the GCM envelope is UTF-8 JSON. Fields:

Required:
- `uid` — the user's UUID (should match the cleartext header; currently
  **not cross-checked** — see §8).
- `lat`, `lon` — decimal degrees.
- `ts` — ISO-8601 timestamp string produced by the client.

Optional:
- `alt` — altitude, meters.
- `spd` — speed, m/s.
- `brg` — bearing, degrees.
- `acc` — accuracy, meters.
- `bat` — battery, percent 0–100.
- `wid` — workout ID (integer, active workout session only).
- `hr` — heart rate, bpm (workout).
- `temp` — ambient temperature °C (workout).
- `hum` — humidity % (workout).

### 4.4 Server processing

Per packet, the listener (`udp_listener.py:handlePacket`):

1. Checks minimum length ≥ 64 bytes.
2. Extracts the 36-byte `user_id` prefix.
3. Looks up the AES key in an in-memory cache (falls back to DB).
4. Decrypts the remainder with AES-256-GCM.
5. Parses JSON.
6. Writes a row to `locations`, optionally `workoutData`.
7. Runs geofence evaluation (§6).

No acknowledgement is sent. Packet loss, reordering, and replay are all
silently tolerated (see §8).

### 4.5 Known protocol limitations (acknowledged, not fixed)

- **No replay protection**. A captured ciphertext can be re-sent and will
  be stored again. `ts` is taken at face value.
- **No nonce-reuse detection**. If the client RNG fails and reuses a nonce
  under the same key, GCM's security guarantees collapse. Server does not
  detect this.
- **No delivery confirmation**. The client never learns whether a packet
  landed. A future TCP "gap reconciliation" endpoint is planned.
- **No forward secrecy**. Key is long-lived; past traffic is decryptable
  by anyone who later compromises the key.
- **No binding between cleartext `user_id` header and encrypted `uid`**.
  See §8 for the remediation sketch.

## 5. REST API (HTTPS)

All routes except the login pages require an authenticated session.
Session auth uses Flask-Login with signed session cookies
(`SESSION_COOKIE_SECURE=True` when deployed behind HTTPS, 7-day lifetime).

### 5.1 Authentication

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/login` | none | Username + password form login. Rate-limited. |
| POST | `/api/auth/token` | none | Mobile app WebView login — body `{user_id, key}` matching the QR-code credentials. Starts a session cookie. |
| GET  | `/logout` | session | Ends session. |

The `/api/auth/token` endpoint compares `key` against the stored `aesKey`
with a plain string compare (not constant-time). A timing-attack-capable
attacker on the network could in principle use this to learn a prefix of
the key. Low practical risk given HTTPS terminates at nginx, but worth
noting for review.

### 5.2 Location and event APIs

| Method | Path | Returns |
|--------|------|---------|
| GET | `/api/locations/latest` | Most recent location per user. |
| GET | `/api/locations/history?userId&start&end` | Location history for one user within a timestamp range. |
| GET | `/api/locations/export?userId&start&end` | Same, as GPX download. |
| GET | `/api/geofence-events?limit&since` | Recent geofence enter/exit events (all users). `since` is an ISO-8601 cutoff. |
| GET | `/api/geofences` | Current user's geofences. |
| GET | `/api/geofences/all` | Every user's geofences (mobile-sync). |
| GET | `/api/user/avatar` | Current user's avatar image, 404 if none. |

### 5.3 Workout APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/workouts/start` | Begin a workout session. |
| POST | `/api/workouts/<id>/stop` | End a workout. |
| GET | `/api/workouts/<id>/data` | All data points for a workout. |

### 5.4 Settings

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/settings/timezone` | Update current user's timezone. |
| POST | `/settings/units` | `metric` or `imperial`. |
| POST | `/settings/avatar` | Upload avatar image. |
| POST | `/settings/deletehistory` | Delete the current user's location history. |
| POST | `/settings/geofence/add` | Add a geofence. |
| POST | `/settings/geofence/delete/<id>` | Remove a geofence. |

### 5.5 Admin-only

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin` | Admin dashboard (user list). |
| POST | `/admin/adduser` | Create a user. |
| POST | `/admin/deleteuser/<id>` | Remove a user. |
| GET | `/admin/qrcode/<id>` | Generate the provisioning QR. |
| GET | `/admin/backup` | Download a SQLite dump. |
| POST | `/admin/restore` | Restore a SQLite dump. |

Admin routes check `current_user.isAdmin` and redirect non-admins to the
dashboard — they do **not** return 403. A non-admin probing these routes
learns only that they exist.

## 6. Geofence Event Generation

Server-side, on every UDP packet, after the location is stored
(`udp_listener.py:checkGeofences`):

1. Fetch all geofence rows.
2. Deduplicate by `name` — if multiple users created a fence called
   "Home" at the same address, only the first is kept. This is done so a
   packet entering a shared location produces one event per user, not N.
3. For each surviving fence, compute Haversine distance from the packet
   position; fence is "inside" if distance ≤ `radiusMeters`.
4. Compare with per-user last-known state (kept in an in-memory dict keyed
   by DB user ID — **lost on server restart**, resulting in a spurious
   "enter" event for whichever fence the user happens to be in).
5. Insert `enter`/`exit` rows into `geofenceEvents` with the client-supplied
   `ts`.

## 7. Mobile App Security Notes

- Mobile stores `user_id` and `key` in Android SharedPreferences. No Keystore-
  backed encryption is used today. An attacker with physical access + root
  on the phone can read the key.
- The WebView login uses `/api/auth/token` with the same credentials,
  meaning the AES key is effectively a bearer token for the web UI as well
  as the UDP channel. Compromise of one is compromise of both.
- Location service is a foreground service with a persistent notification;
  battery-optimization whitelisting is requested at first run.

## 8. Known Issues / Suggested Hardening

This section lists protocol-level items an independent reviewer is likely
to flag. None are fixed yet. Listed so a review can confirm we have
considered them and track what's deferred vs. what's a real gap.

1. **Cleartext user ID in UDP header** — enables correlation of packets
   to a specific family member on the wire. Mitigation: encrypt an
   ephemeral 4-byte user tag under a server-known secret, or switch the
   key-lookup to a constant-time trial-decrypt across a small number of
   candidate keys.
2. **No AAD binding** between cleartext header and ciphertext. Trivially
   fixable by passing the 36-byte header as AAD to GCM on both ends.
3. **No replay protection**. Options: client-side monotonic counter in
   AAD, or server-side deduplication by (user, ts).
4. **AES key == web-API bearer token**. A leaked key authenticates the web
   session. Splitting into separate UDP key vs. WebView token is cheap.
5. **Non-constant-time key comparison** in `/api/auth/token`.
6. **No rate limit on UDP**. A well-known `user_id` plus a flood of
   garbage ciphertext forces the server to run GCM verify per packet.
7. **No key rotation mechanism**. Admin re-key is planned
   (regenerate `user_id` and `aesKey`, show fresh QR) but not shipped as
   of this revision.
8. **In-memory geofence state is not persisted**, causing spurious "enter"
   events on server restart.
9. **Admin routes leak existence** via redirect vs. 403. Low-severity.
10. **HTTPS is optional** — the server runs cleartext HTTP if deployed
    without the nginx reverse proxy. The `setup.sh` script is the happy path
    and configures HTTPS by default.

## 9. References

- Source: `udp_listener.py` (UDP protocol), `app.py` (REST API),
  `database.py` (schema).
- Test harness: `test_send_location.py` — mirrors the client-side
  encryption path and is the easiest place to reproduce a wire packet.
- Wire diagram (copy of §4.1 for quick reference):
  `[36 user UUID ascii][12 nonce][16 GCM tag][ciphertext]`
