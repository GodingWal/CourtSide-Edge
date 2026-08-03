# Deployment and rollback

## Quality gate

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy packages services apps
uv run pytest
```

## Controlled deployment

The SSH deployment user may invoke only the root-owned deployment wrapper:

```bash
sudo /usr/local/sbin/deploy-courtside
```

The wrapper creates a pre-deploy backup, fast-forwards `main`, syncs locked dependencies, applies
migrations with the migration-only database credential, restarts services and runs health checks.

## Rollback triggers

- Authenticated forecast board does not return HTTP 200.
- Public health endpoint is not HTTP 200.
- Any required timer is inactive or a service enters failed state.
- Migration fails or forecast count unexpectedly falls to zero.
- PostgreSQL connectivity or source freshness becomes unhealthy.

Code may be fast-forwarded to a corrective commit. Database rollback uses the verified
pre-deploy backup restored into a new database; migrations are not blindly reversed in place.
