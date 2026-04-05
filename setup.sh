#!/bin/bash
#
# Family Tracks - Server Setup Script
#
# This script:
#   1. Asks for your domain name, UDP port, and admin password
#   2. Generates a secret key
#   3. Configures nginx with your domain
#   4. Obtains a Let's Encrypt TLS certificate
#   5. Starts everything with Docker Compose
#
# Prerequisites:
#   - A server with Docker and Docker Compose installed
#   - A domain name pointing to this server's IP (A record)
#   - Ports 80, 443 (TCP) and 5555 (UDP) open in your firewall
#

set -e

echo "============================================"
echo "  Family Tracks - Server Setup"
echo "============================================"
echo ""

# Check for docker
if ! command -v docker &>/dev/null; then
    echo "Error: Docker is not installed."
    echo "Install it with: curl -fsSL https://get.docker.com | sh"
    exit 1
fi

# Detect docker compose command (v2 plugin vs v1 standalone)
if docker compose version &>/dev/null; then
    DC="docker compose"
elif command -v docker-compose &>/dev/null; then
    DC="docker-compose"
else
    echo "Error: Docker Compose is not installed."
    exit 1
fi

echo "Using: $DC"

# Get domain
echo "Enter your domain name (e.g. tracks.example.com):"
read -r DOMAIN
if [ -z "$DOMAIN" ]; then
    echo "Error: Domain name is required."
    exit 1
fi

# Get UDP port
echo ""
echo "UDP port for location data [5555]:"
read -r UDP_PORT
UDP_PORT=${UDP_PORT:-5555}

# Get admin password
echo ""
echo "Admin password [admin]:"
read -r -s ADMIN_PASSWORD
ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin}
echo ""

# Generate secret key
SECRET_KEY=$(openssl rand -hex 32)

# Write .env file
cat > .env << EOF
DOMAIN=${DOMAIN}
UDP_PORT=${UDP_PORT}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
SECRET_KEY=${SECRET_KEY}
EOF

echo ""
echo "Configuration saved to .env"

# Update nginx.conf with the actual domain
sed -i "s/YOURDOMAIN/${DOMAIN}/g" nginx.conf
sed -i "s/server_name _;/server_name ${DOMAIN};/g" nginx.conf

echo "Updated nginx.conf with domain: ${DOMAIN}"

# Step 1: Start with HTTP only (for cert challenge)
# Temporarily comment out the HTTPS server block so nginx can start
# without certs existing yet
echo ""
echo "--- Step 1: Starting HTTP server for certificate challenge ---"

# Create a temporary nginx config with just HTTP
cat > nginx.conf.tmp << 'TMPEOF'
server {
    listen 80;
    server_name DOMAINHERE;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://familytracks:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
TMPEOF
sed -i "s/DOMAINHERE/${DOMAIN}/g" nginx.conf.tmp

# Use the temp config to start nginx
cp nginx.conf nginx.conf.final
cp nginx.conf.tmp nginx.conf
rm nginx.conf.tmp

# Create certbot directories
mkdir -p certbot/conf certbot/www

# Start the app and nginx (HTTP only)
$DC up -d familytracks nginx

echo "Waiting for nginx to start..."
sleep 5

# Step 2: Obtain certificate
echo ""
echo "--- Step 2: Obtaining Let's Encrypt certificate ---"
echo ""
echo "Enter your email for Let's Encrypt notifications (or leave blank):"
read -r EMAIL

EMAIL_ARG=""
if [ -n "$EMAIL" ]; then
    EMAIL_ARG="--email ${EMAIL}"
else
    EMAIL_ARG="--register-unsafely-without-email"
fi

docker run --rm \
    -v "$(pwd)/certbot/conf:/etc/letsencrypt" \
    -v "$(pwd)/certbot/www:/var/www/certbot" \
    certbot/certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    ${EMAIL_ARG} \
    --agree-tos \
    --no-eff-email \
    -d "${DOMAIN}"

if [ $? -ne 0 ]; then
    echo ""
    echo "Certificate request failed. Make sure:"
    echo "  1. Your domain ${DOMAIN} points to this server's IP"
    echo "  2. Port 80 is open in your firewall"
    echo ""
    echo "You can still use the server over HTTP on port 80."
    echo "Re-run this script to try again."
    exit 1
fi

# Step 3: Switch to HTTPS config and restart
echo ""
echo "--- Step 3: Enabling HTTPS ---"

cp nginx.conf.final nginx.conf
rm -f nginx.conf.final

$DC restart nginx

# Start certbot renewal container
$DC up -d certbot

echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "  Website: https://${DOMAIN}"
echo "  UDP Port: ${UDP_PORT}"
echo "  Admin Login: admin / (your password)"
echo ""
echo "  Certificate will auto-renew via certbot."
echo ""
echo "  Firewall: make sure these ports are open:"
echo "    TCP 80   (HTTP, for cert renewal)"
echo "    TCP 443  (HTTPS)"
echo "    UDP ${UDP_PORT}  (location data)"
echo ""
echo "  To view logs:  docker compose logs -f"
echo "  To stop:       docker compose down"
echo "  To restart:    docker compose up -d"
echo ""
