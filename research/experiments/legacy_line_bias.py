"""Deep dive: is there exploitable structure in the rescued legacy lines?

WHAT THIS CAN AND CANNOT ANSWER
-------------------------------
Every price in the legacy archive is NULL -- the source encoding was lossy and the American
odds could not be recovered. So this measures whether lines were *beaten*, never whether
beating them was profitable. A 54% over rate means nothing on its own: standard prop pricing
near -115 both ways needs roughly 53.5% just to break even.

METHOD NOTES THAT CHANGE THE ANSWER
-----------------------------------
1. **One row per player-game-market.** The raw table has up to five bookmakers quoting the
   same player, and those rows share a single outcome. Treating them as five observations
   would shrink the confidence intervals by ~sqrt(5) and manufacture significance out of
   nothing. We collapse to the median line across books.
2. **DNPs are dropped, not counted as unders.** A scratch writes zeros across the box score,
   which read naively is a winning under on every market that player was priced in.
3. **Multiple comparisons are counted and corrected.** Slice 30 ways at p<0.05 and you expect
   1.5 false positives; the interesting question is whether anything survives Bonferroni.

RESULT (run 2026-08-04, 615 scoreable player-game-markets, 2026-06-23 to 2026-07-16)
------------------------------------------------------------------------------------
Nothing replicates. Recorded here so it is not rediscovered and believed later.

* Overall 45.0% over (p=0.013), but it drifts 39.0% -> 45.8% across the two halves of the
  window, so even the "unders lean" is unstable.
* Four slices survived Bonferroni. Three were outcome-conditioned (minutes played, bench
  role) and therefore untradeable -- a player who logs 15 minutes against a line priced for
  30 misses every over by construction. That is the definition of the outcome, not a signal.
* The fourth, "books agree (spread=0) -> 37.9% over", was a confound: a single book quoting
  alone has a spread of zero by definition, and single-book rows were 100% of that slice.
  Controlling for line level, thin vs liquid at low lines is 41.5% vs 45.0% -- gone.
* A high-line effect (62.3% over) appeared during the controlled analysis. Split-half:
  73.3% on n=30 in the first half, 50.9% on n=53 in the second. Does not replicate.

The one stable, useful finding is negative: at five books quoting, the over rate is 49.6%
(n=387) -- a coin flip. The liquid market is efficiently priced, so simple rules will not
beat it, and any real edge lives in thin markets where vig is widest and limits lowest.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from wnba_store.db import connect

MIN_MAPPING_CONFIDENCE = 0.5
MIN_SAMPLE = 30


@dataclass
class Slice:
    label: str
    n: int
    overs: int

    @property
    def rate(self) -> float:
        return self.overs / self.n if self.n else 0.0

    @property
    def stderr(self) -> float:
        p = self.rate
        return math.sqrt(p * (1 - p) / self.n) if self.n else 0.0

    @property
    def z(self) -> float:
        """Standard normal deviate against a 50% null."""
        return (self.rate - 0.5) / self.stderr if self.stderr else 0.0

    @property
    def p_value(self) -> float:
        return math.erfc(abs(self.z) / math.sqrt(2))

    def ci95(self) -> tuple[float, float]:
        m = 1.96 * self.stderr
        return self.rate - m, self.rate + m


def fetch_settled() -> list[dict[str, object]]:
    """One row per (player, game, market): median line across books, plus the outcome."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH deduped AS (
              SELECT h.player_id, h.game_id, h.prop_type,
                     percentile_cont(0.5) WITHIN GROUP (ORDER BY h.line) AS line,
                     count(DISTINCT h.bookmaker) AS books,
                     max(h.line) - min(h.line) AS book_spread,
                     min(h.observed_at) AS first_seen,
                     max(h.scheduled_tipoff) AS tipoff
              FROM wnba.historical_prop_quotes h
              WHERE h.mapping_confidence >= %s
              GROUP BY h.player_id, h.game_id, h.prop_type
            )
            SELECT d.*, p.full_name, l.minutes, l.started,
                   l.points, l.assists, l.steals, l.blocks,
                   l.rebounds_offensive + l.rebounds_defensive AS rebounds,
                   l.three_pointers_made, l.free_throws_made,
                   l.field_goals_attempted
            FROM deduped d
            JOIN wnba.players p ON p.player_id = d.player_id
            JOIN wnba.player_game_lines l
              ON l.player_id = d.player_id AND l.game_id = d.game_id
            """,
            (MIN_MAPPING_CONFIDENCE,),
        )
        return [dict(r) for r in cur.fetchall()]


_STAT = {
    "points": lambda r: r["points"],
    "rebounds": lambda r: r["rebounds"],
    "assists": lambda r: r["assists"],
    "three_pointers": lambda r: r["three_pointers_made"],
}


def main() -> None:
    rows = fetch_settled()
    print(f"settled player-game-markets (deduped across books): {len(rows)}")

    scored: list[dict[str, object]] = []
    dnp = pushes = unsupported = 0
    for r in rows:
        getter = _STAT.get(str(r["prop_type"]))
        if getter is None:
            unsupported += 1
            continue
        minutes = float(str(r["minutes"]))
        if minutes == 0.0:
            dnp += 1
            continue
        actual = float(getter(r))
        line = float(str(r["line"]))
        if actual == line:
            pushes += 1
            continue
        scored.append({**r, "actual": actual, "line_f": line, "over": actual > line})

    print(f"  dropped: {dnp} DNP, {pushes} push, {unsupported} unmodelled market")
    print(f"  scoreable: {len(scored)}\n")
    if not scored:
        return

    def report(title: str, groups: dict[str, list[dict[str, object]]], tests: list[Slice]) -> None:
        print(f"--- {title} ---")
        for key in sorted(groups):
            items = groups[key]
            if len(items) < MIN_SAMPLE:
                continue
            s = Slice(key, len(items), sum(1 for x in items if x["over"]))
            tests.append(s)
            lo, hi = s.ci95()
            flag = "  <-- p<0.05" if s.p_value < 0.05 else ""
            print(
                f"  {key:22} n={s.n:5}  over={s.rate:6.1%}  "
                f"95% CI [{lo:5.1%}, {hi:5.1%}]  p={s.p_value:.3f}{flag}"
            )
        print()

    tests: list[Slice] = []

    overall = Slice("ALL", len(scored), sum(1 for x in scored if x["over"]))
    tests.append(overall)
    lo, hi = overall.ci95()
    print("--- overall ---")
    print(
        f"  {'every market':22} n={overall.n:5}  over={overall.rate:6.1%}  "
        f"95% CI [{lo:5.1%}, {hi:5.1%}]  p={overall.p_value:.3f}\n"
    )

    by_market: dict[str, list[dict[str, object]]] = defaultdict(list)
    for x in scored:
        by_market[str(x["prop_type"])].append(x)
    report("by market", by_market, tests)

    by_start: dict[str, list[dict[str, object]]] = defaultdict(list)
    for x in scored:
        by_start["starter" if x["started"] else "bench"].append(x)
    report("by role (observed after the fact -- descriptive only)", by_start, tests)

    by_spread: dict[str, list[dict[str, object]]] = defaultdict(list)
    for x in scored:
        spread = float(str(x["book_spread"] or 0))
        key = "books agree (0)" if spread == 0 else ("spread <=1" if spread <= 1 else "spread >1")
        by_spread[key].append(x)
    report("by cross-book disagreement", by_spread, tests)

    by_minutes: dict[str, list[dict[str, object]]] = defaultdict(list)
    for x in scored:
        m = float(str(x["minutes"]))
        key = "<20 min" if m < 20 else ("20-30 min" if m < 30 else ">=30 min")
        by_minutes[key].append(x)
    report("by minutes played (outcome-conditioned -- NOT tradeable)", by_minutes, tests)

    # Multiple comparisons. With this many slices, "p<0.05 somewhere" is the null hypothesis.
    k = len(tests)
    alpha = 0.05 / k
    survivors = [t for t in tests if t.p_value < alpha]
    print(f"=== {k} tests performed; Bonferroni alpha = {alpha:.4f} ===")
    if survivors:
        for s in survivors:
            print(f"  SURVIVES correction: {s.label} n={s.n} over={s.rate:.1%} p={s.p_value:.5f}")
    else:
        nominal = [t for t in tests if t.p_value < 0.05]
        print(f"  nothing survives correction ({len(nominal)} were nominally p<0.05,")
        print(f"   and {0.05 * k:.1f} false positives were expected by chance alone)")


if __name__ == "__main__":
    main()
