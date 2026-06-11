#!/usr/bin/env bash
# Obtain Let's Encrypt certs for courtside-edge.com and enable the HTTPS proxy.
# Prereqs: DNS A records for courtside-edge.com and www.courtside-edge.com point
# at this VPS, ports 80/443 open, and install-nginx.sh has been run once.
# Run as root from the repo root on the VPS:  bash deploy/scripts/setup-tls.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

DOMAIN="courtside-edge.com"
EMAIL="${CERTBOT_EMAIL:-gwal325@gmail.com}"

command -v certbot >/dev/null 2>&1 || { echo "→ Installing certbot…"; apt-get update -y && apt-get install -y certbot; }

mkdir -p /var/www/certbot

echo "→ Requesting certificate via webroot…"
certbot certonly --webroot -w /var/www/certbot \
  -d "$DOMAIN" -d "www.$DOMAIN" \
  --email "$EMAIL" --agree-tos --non-interactive --keep-until-expiring

# Now render and enable the full TLS config (install-nginx.sh detects the certs).
bash deploy/scripts/install-nginx.sh

# Certbot's systemd timer handles renewal; reload nginx on renew.
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'EOF'
#!/usr/bin/env bash
systemctl reload nginx
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

echo "✓ TLS enabled for https://${DOMAIN}"
