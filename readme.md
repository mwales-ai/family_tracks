# Family Location Tracking Services with Privacy

We are tired of stupid free services that sell customer's private information to data
brokers.  This might just give you targeted ads, but insurance companies have been
known to buy data from brokers and then raise your rates.

# Cloud Services Under Your Control

Family Location Tracking is a docker container you can put on your own virtual private
server.  These will cost you some money, but can host many services on a single server
and gives you the piece of mind that your data is safe and UNDER YOUR CONTROL.

# Mobile Apps with minimal intrusion

Configure exactly what notification level you want and what data you want to have sent
out.  No nagging, no ads.

# Server Deployment

## Prerequisites

- A VPS or server with Docker and Docker Compose installed
- A domain name with an A record pointing to your server's IP
- Ports 80 (HTTP), 443 (HTTPS), and 5555 (UDP) open in your firewall

## Quick Setup (with HTTPS + Let's Encrypt)

```bash
git clone https://github.com/mwales-ai/family_tracks.git
cd family_tracks
./setup.sh
```

The setup script will:
1. Ask for your domain name, UDP port, and admin password
2. Generate a secret key
3. Configure nginx as an HTTPS reverse proxy
4. Obtain a free TLS certificate from Let's Encrypt
5. Start everything with Docker Compose

After setup, open `https://yourdomain.com` and log in with `admin` and your password.

## Local Development (no HTTPS)

```bash
# Run directly
python3 -m pip install -r requirements.txt
python3 app.py

# Or with Docker
docker compose -f docker-compose.dev.yml up --build
```

Default login: `admin` / `admin`

## Manual Docker Setup

If you want to configure things yourself:

```bash
# Create .env file
cat > .env << EOF
DOMAIN=tracks.example.com
UDP_PORT=5555
ADMIN_PASSWORD=your-secure-password
SECRET_KEY=$(openssl rand -hex 32)
EOF

# Edit nginx.conf — replace YOURDOMAIN with your actual domain
# Then start
docker compose up -d
```

## Certificate Renewal

Certbot runs as a container and auto-renews certificates. To manually renew:

```bash
docker compose run --rm certbot renew   # or docker-compose on older installs
docker compose restart nginx
```

## Firewall Rules

```bash
# UFW example
sudo ufw allow 80/tcp    # HTTP (cert renewal)
sudo ufw allow 443/tcp   # HTTPS
sudo ufw allow 5555/udp  # Location data
```

## Architecture

```
Internet
  |
  |--- TCP 443 ---> nginx (HTTPS) ---> Flask app (port 5000)
  |--- TCP 80  ---> nginx (HTTP, redirects to HTTPS + cert challenges)
  |--- UDP 5555 --> UDP listener (AES-256-GCM encrypted location packets)
```

# Adding Users

1. Log into the web admin panel
2. Go to **Admin** -> **Add User**
3. Click **QR Code** next to the new user
4. On the phone, open Family Tracks app -> **Scan QR Code**
5. The app is now configured and ready to track

# Mobile App Features

* Location (Fine or Coarse GPS)
* Reporting Frequency (10 seconds to 30 minutes)
* Geofence Locations (home, work, school, friends)
* Speed reporting
* Battery life reporting
* Avatar photo upload
* Workout Mode
  * Optional high reporting frequency
  * Environmental data (temperature, humidity, wind)
  * Biometrics collected by phone

# Privacy

* All location data is AES-256-GCM encrypted end-to-end
* Data is stored only on YOUR server
* Map tiles come from OpenStreetMap (they see tile requests, not your location data)
* No accounts, no tracking, no ads, no data brokers
* Users can delete their own location history at any time
* Database backup/restore from admin panel

# Related Repos

- **Android app:** [github.com/mwales-ai/family_tracks_android](https://github.com/mwales-ai/family_tracks_android)
- **iOS app:** not yet started
