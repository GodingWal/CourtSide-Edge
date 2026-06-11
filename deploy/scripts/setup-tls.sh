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

mkdir -p /var/www/certbot/.well-known/acme-challenge

# Ensure the HTTP bootstrap nginx config (which serves the ACME webroot from
# /var/www/certbot) is active BEFORE certbot runs. Without this, the challenge
# path returns 404 and authentication fails. install-nginx.sh installs the
# HTTP-only bootstrap when certs are not yet present.
echo "→ Ensuring ACME webroot is served by nginx…"
bash deploy/scripts/install-nginx.sh

# Self-test the challenge path over the public hostname before bothering the CA.
TOKEN="selftest-$(date +%s)"
echo "$TOKEN" > "/var/www/certbot/.well-known/acme-challenge/$TOKEN"
if curl -fsS "http://${DOMAIN}/.well-known/acme-challenge/${TOKEN}" 2>/dev/null | grep -q "$TOKEN"; then
  echo "✓ ACME challenge path reachable."
else
  echo "✗ http://${DOMAIN}/.well-known/acme-challenge/ is not serving from /var/www/certbot." >&2
  echo "  Check: DNS A records -> this VPS, port 80 open, 'nginx -t', and the" >&2
  echo "  /etc/nginx/sites-enabled/${DOMAIN} symlink. Aborting before contacting the CA." >&2
  rm -f "/var/www/certbot/.well-known/acme-challenge/$TOKEN"
  exit 1
fi
rm -f "/var/www/certbot/.well-known/acme-challenge/$TOKEN"

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
