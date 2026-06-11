# CourtSideEdge — Production Deployment

Two-host split:

| Host | Role | Runs |
|------|------|------|
| **VPS** `76.13.100.125` → `courtside-edge.com` | Web tier | nginx (TLS), React dashboard, Express API/SSE/WS, Redis, SQLite ledger, and the 6 ledger-coupled agents (0, 13, 14, 15, 16, 20) |
| **vast.ai** `151.237.25.234` | Agent tier | The 18 stateless/compute agents, dialing back to the VPS Redis + API |

The ledger agents stay on the VPS because they read/write the SQLite file
directly — SQLite can't be shared across hosts. (With PostgreSQL enabled —
`COMPOSE_PROFILES=postgres` + `DATABASE_URL` in `env/web.env`, mirrored in
`env/agents.env` — that constraint disappears: any agent on any host can
read/write the ledger over the network.) Everything else talks over
Redis (authenticated, firewalled) and HTTPS.

```
Browser ──HTTPS──▶ nginx (VPS) ──┬─▶ web_client  (static SPA, :8080)
                                 └─▶ web_server  (:3000, API key injected by nginx)
                                          │
                                        Redis (:6379, password + firewall)
                                          ▲
                                          │ over the internet
                              vast.ai agents (18 services)
```

## Files

- `docker-compose.web.yml` — VPS web tier
- `docker-compose.agents.yml` — vast.ai agent tier
- `env/web.env.example`, `env/agents.env.example` — env templates
- `nginx/courtside-edge.com.conf.template` — host reverse proxy (TLS + API-key injection)
- `scripts/` — provisioning and deployment

## One-time setup

### 0. Generate matched secrets (on your laptop, in the repo)
```bash
bash deploy/scripts/gen-secrets.sh        # writes env/web.env + env/agents.env (gitignored)
```
This creates a shared `API_KEY` and `REDIS_PASSWORD` so the two hosts agree.
Set `HERMES_API_KEY=… bash deploy/scripts/gen-secrets.sh` to use a hosted Hermes endpoint.

### 1. VPS (web tier)
```bash
# get the repo onto the box, then from the repo root:
bash deploy/scripts/provision.sh          # install Docker
bash deploy/scripts/firewall.sh           # SSH/HTTP/HTTPS open; Redis only from the agent box
bash deploy/scripts/deploy-web.sh         # build + start the web tier (needs env/web.env)
bash deploy/scripts/install-nginx.sh      # install reverse proxy (HTTP bootstrap if no certs yet)
# point DNS A records for courtside-edge.com + www at 76.13.100.125, then:
bash deploy/scripts/setup-tls.sh          # Let's Encrypt certs + enable HTTPS
```

### 2. vast.ai (agent tier)
```bash
# copy env/agents.env to this box (it holds the matching secrets), then from the repo root:
bash deploy/scripts/provision.sh
bash deploy/scripts/deploy-agents.sh      # checks Redis reachability, then builds + starts 18 agents
```

## Updating a running deployment

```bash
git pull
bash deploy/scripts/deploy-web.sh         # on the VPS
bash deploy/scripts/deploy-agents.sh      # on the vast.ai box
```
Both are idempotent (`up -d --build`); only changed images rebuild.

## Security notes

- **API key never reaches the browser.** The SPA calls same-origin `/api`; nginx
  injects `Authorization: Bearer <API_KEY>` on the proxy hop. Rotate by editing
  `env/web.env` + re-running `install-nginx.sh` and `deploy-web.sh`.
- **Redis is password-protected and firewalled** to the agent box IP only. If the
  vast.ai box IP changes, update `AGENT_IP` and re-run `firewall.sh`.
- Real `env/web.env` / `env/agents.env` are gitignored — never commit them.

## Continuous deployment (GitHub Actions)

`.github/workflows/deploy.yml` deploys automatically after CI passes on `main`
(and on demand via the **Run workflow** button). It SSHes into each host, fast-
forwards the repo to `origin/main`, and runs the deploy script — so the env
files holding secrets stay on the hosts and never pass through CI.

One-time setup (repo **Settings → Secrets and variables → Actions**):

1. Generate a deploy keypair and authorize the public key on both hosts:
   ```bash
   ssh-keygen -t ed25519 -f deploy_key -N ''
   ssh-copy-id -i deploy_key.pub root@76.13.100.125
   ssh-copy-id -i deploy_key.pub -p 26918 root@151.237.25.234
   ```
2. Add **secrets** `VPS_SSH_KEY` and `VAST_SSH_KEY` (paste the private key).
3. Add the **variable** `DEPLOY_ENABLED=true` (master switch — jobs are skipped
   until this is set, so you never get red builds before it's configured).
4. Optional **variables** to override defaults:
   `VPS_HOST`, `VPS_USER`, `VPS_PORT`, `VPS_REPO_PATH`,
   `VAST_HOST`, `VAST_USER`, `VAST_PORT`, `VAST_REPO_PATH`.

Prereq: both hosts already have the repo cloned and their `deploy/env/*.env`
files in place (do the one-time manual deploy first).

## Operations

```bash
# logs
docker compose --env-file deploy/env/web.env -f deploy/docker-compose.web.yml logs -f web_server
docker compose --env-file deploy/env/agents.env -f deploy/docker-compose.agents.yml logs -f

# status
docker compose --env-file deploy/env/web.env -f deploy/docker-compose.web.yml ps

# agent health (via the SSH tunnel: ssh -p 26918 root@151.237.25.234 -L 8080:localhost:8080)
curl localhost:8000/health   # projection engine, etc.
```
