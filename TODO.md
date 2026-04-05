# Incomplete Tasks

## MVP Phase 1: Reliable Location Tracking
* [X] Server receives and stores encrypted UDP location packets
* [X] Web dashboard shows family member locations on map
* [X] Android app sends encrypted location via UDP
* [X] QR code onboarding flow (admin generates, phone scans)
* [X] Android app shows map via WebView with auto-login
* [ ] Verify end-to-end flow works reliably on real device
    * [ ] Test location updates appearing on map in real-time
    * [ ] Test app survives being backgrounded / phone locked
    * [ ] Test app recovery after phone reboot (auto-start service option)
* [X] Hide the navbar on dashboard when loaded in mobile WebView
* [X] Android: background location permission flow (explain why, handle denial)
* [X] Android: battery optimization whitelist prompt (prevent OS from killing service)

## MVP Phase 2: Geofence Reporting
* [X] Server: geofence event detection
    * [X] Compare incoming location against all geofences
    * [X] Store geofence events table (userId, geofenceId, eventType enter/exit, timestamp)
    * [X] API endpoint to get recent geofence events (/api/geofence-events)
    * [X] API endpoint to get all geofences for mobile sync (/api/geofences/all)
* [X] Server: geofence events page
    * [X] Timeline view showing entry/exit events for all family members
    * [X] "Child A left Home at 8:00am", "Child B arrived at School at 8:30am"
* [X] Android: periodic sync with server
    * [X] Once per hour, fetch geofence list from server via API
    * [X] Store geofences locally in SharedPreferences as JSON
* [X] Android: geofence events tab
    * [X] Show recent geofence events from server API
    * [X] Pull-to-refresh

## Server Security & Polish
* [X] HTTPS / TLS support (nginx reverse proxy + Let's Encrypt)
    * [X] setup.sh script handles cert provisioning automatically
* [X] Installer script (setup.sh — asks for domain, port, admin password, sets up Docker)
* [X] Rate limiting on login endpoint to prevent brute force
* [X] Input validation / sanitization on all form inputs
* [X] Session timeout configuration (7-day persistent sessions)
* [X] Password minimum length (4 chars on add user)

## Map & UI Improvements
* [X] Auto-refresh map markers without full page reload (already polls every 30s via fetch)
* [X] User avatars (upload photo, display on map markers instead of initials)
* [X] Speed-colored track history (blue=slow, green=medium, red=fast)
* [X] Export location history (GPX download from dashboard)
* [X] Backup / restore database from admin panel
* [X] Dark mode theme (auto via prefers-color-scheme)

## Future: Data & Privacy
* [ ] Auto-purge old location data (configurable retention period)
* [ ] Per-user data export (GDPR-style "download my data")
* [ ] Audit log for admin actions
* [ ] Option for users to pause sharing without uninstalling

## Future: Android Enhancements
* [ ] Show own location history in-app
* [ ] Geofence configuration from app (add current location as geofence only)
* [ ] Handle server unreachable (queue packets, retry)
* [X] Smart location reporting
    * [X] If sitting inside a geofence for 30+ mins, switch to coarse GPS / slower reporting
    * [X] When leaving geofence, go back to normal reporting
* [ ] Workout mode (start/stop from app, high frequency reporting) (LOW PRIORITY)
* [ ] App store listing / signed release build

## Future: Infrastructure (LOW PRIORITY)
* [ ] Upgrade to self-hosted map tiles (MapLibre + tileserver-gl Docker container)
* [ ] PostgreSQL option for multi-user scaling
* [ ] Health check endpoint for monitoring
* [ ] Systemd service file alternative to Docker

## Future: iOS App (LOW PRIORITY)
* [ ] Create Xcode project (Swift)
* [ ] QR code scanning
* [ ] AES-256-GCM encryption + UDP sending
* [ ] Core Location background updates
* [ ] Settings (interval, fine/coarse, data sharing toggles)

## Future: Protocol
* [ ] Server acknowledgment packet (optional UDP reply so phone knows it was received)
    * [ ] Proper TCP/HTTPS connection to verify / resend missing packets once per hour
* [ ] Packet sequence numbers for detecting gaps
* [ ] Key rotation mechanism (LOW PRIORITY, may never do)

# Completed Tasks

* [X] Flask project scaffolding + Docker setup
* [X] Database schema (users, locations, workouts, geofences)
* [X] Auth system + login page
* [X] Admin panel + user management + QR code generation
* [X] Map dashboard with Leaflet.js + OSM tiles
* [X] Location history API + track rendering
* [X] UDP listener with AES-256-GCM decryption
* [X] Workout pages (list + detail with map and stats)
* [X] Geofence management in settings (interactive map picker + radius slider)
* [X] Android app: QR scanning, location service, encrypted UDP sending
* [X] Android app: bottom tab navigation (Map, Status, Settings)
* [X] Android app: WebView map with auto-authentication
* [X] API token auth endpoint for mobile app
