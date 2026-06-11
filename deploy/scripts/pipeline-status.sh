#!/usr/bin/env bash
# One-shot live pipeline status: games, lines, props, edges, heartbeats.
# Run from repo root on the vast.ai box: bash deploy/scripts/pipeline-status.sh
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PY=".venv-agents/bin/python"; [ -x "$PY" ] || PY=python3
set -a; . deploy/env/agents.env; set +a

"$PY" - <<'PYEOF'
import json
import os
import sys
sys.path.insert(0, '.')
import redis

from shared.espn_client import get_scoreboard

r = redis.Redis(host=os.environ['REDIS_HOST'].strip(), port=int(os.environ['REDIS_PORT']),
                password=os.environ.get('REDIS_PASSWORD', '').strip() or None, socket_timeout=10)

games = get_scoreboard()
print(f"── Today's slate (ET): {len(games)} games")
for g in games:
    ou = g.get('odds', {}).get('over_under') if g.get('odds') else None
    print(f"   {g['away']} @ {g['home']}  [{g['state']}]" + (f"  O/U {ou}" if ou else ""))

props = r.hgetall('props:lines')
print(f"\n── Live player props cached: {len(props)}")
for k, v in list(props.items())[:8]:
    p = json.loads(v)
    print(f"   {p['player']} {p['stat']} {p['line']} ({p.get('odds')}) @ {p.get('book')}")

print(f"\n── Edges in stream_market_intelligence: {r.xlen('stream_market_intelligence')}")
for key in ('recent:velocity_alerts', 'recent:sharp_consensus', 'recent:rotations'):
    print(f"── {key}: {r.llen(key)} entries")

beats = sorted(k.decode().split(':')[-1] for k in r.keys('heartbeat:agent:*'))
print(f"\n── Heartbeats ({len(beats)}): {', '.join(beats)}")
PYEOF
