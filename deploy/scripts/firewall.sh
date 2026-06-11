#!/usr/bin/env bash
# Lock down the VPS firewall. Critically: Redis (6379) is only reachable from
# the vast.ai agent box, never the open internet.
# Run as root on the VPS:  bash deploy/scripts/firewall.sh
set -euo pipefail

# Public IP of the vast.ai box that runs the agents.
AGENT_IP="${AGENT_IP:-151.237.25.234}"

command -v ufw >/dev/null 2>&1 || { echo "→ Installing ufw…"; apt-get update -y && apt-get install -y ufw; }

ufw default deny incoming
ufw default allow outgoing

ufw allow 22/tcp        comment 'SSH'
ufw allow 80/tcp        comment 'HTTP'
ufw allow 443/tcp       comment 'HTTPS'

# Redis: only the agent box.
ufw allow from "$AGENT_IP" to any port 6379 proto tcp comment 'Redis from vast.ai agents'

ufw --force enable
ufw status verbose
echo "✓ Firewall configured. Redis 6379 restricted to ${AGENT_IP}."
