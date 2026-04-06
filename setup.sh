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

# Require docker compose v2
if ! docker compose version &>/dev/null; then
    echo "Error: Docker Compose v2 is required."
    echo "Install it with: sudo apt install docker-compose-plugin"
    exit 1
fi

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
docker compose up -d familytracks nginx

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

# Check if cert was actually obtained
if [ ! -f "certbot/conf/live/${DOMAIN}/fullchain.pem" ]; then
    echo ""
    echo "Certificate not found. Make sure:"
    echo "  1. Your domain ${DOMAIN} points to this server's IP"
    echo "  2. Port 80 is open in your firewall"
    echo ""
    echo "The server is running on HTTP (port 80) without TLS."
    echo "Re-run this script to try again."
    rm -f nginx.conf.final
    exit 1
fi

# Step 3: Write the final HTTPS nginx config
echo ""
echo "--- Step 3: Enabling HTTPS ---"

cat > nginx.conf << 'NGINXEOF'
server {
    listen 80;
    server_name DOMAINHERE;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name DOMAINHERE;

    ssl_certificate     /etc/letsencrypt/live/DOMAINHERE/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAINHERE/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    client_max_body_size 10M;

    location / {
        proxy_pass http://familytracks:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINXEOF

sed -i "s/DOMAINHERE/${DOMAIN}/g" nginx.conf
rm -f nginx.conf.final

docker compose restart nginx

# Start certbot renewal container
docker compose up -d certbot

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
