#!/usr/bin/env bash
# Install the host nginx reverse proxy for courtside-edge.com on the VPS.
# Substitutes the API key from deploy/env/web.env into the proxy config.
# Run as root from the repo root on the VPS:  bash deploy/scripts/install-nginx.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

DOMAIN="courtside-edge.com"
TEMPLATE="deploy/nginx/${DOMAIN}.conf.template"
ENV_FILE="deploy/env/web.env"
AVAILABLE="/etc/nginx/sites-available/${DOMAIN}"
ENABLED="/etc/nginx/sites-enabled/${DOMAIN}"

command -v nginx >/dev/null 2>&1 || { echo "→ Installing nginx…"; apt-get update -y && apt-get install -y nginx; }

API_KEY="$(grep -E '^API_KEY=' "$ENV_FILE" | cut -d= -f2-)"
[ -z "$API_KEY" ] && { echo "✗ API_KEY not found in $ENV_FILE" >&2; exit 1; }

mkdir -p /var/www/certbot

# If certs aren't issued yet, install an HTTP-only stub so nginx can start
# (setup-tls.sh swaps in the full config after obtaining certs).
if [ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
  echo "→ Certs not present yet — installing HTTP-only bootstrap config."
  cat > "$AVAILABLE" <<EOF
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 'CourtSideEdge: awaiting TLS. Run setup-tls.sh.\n'; add_header Content-Type text/plain; }
}
EOF
else
  echo "→ Rendering full TLS reverse-proxy config."
  sed "s|__API_KEY__|${API_KEY//&/\\&}|g" "$TEMPLATE" > "$AVAILABLE"
fi

ln -sf "$AVAILABLE" "$ENABLED"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx || systemctl restart nginx
echo "✓ nginx config installed for ${DOMAIN}."
