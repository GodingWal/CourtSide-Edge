"""Unit tests for the v3.1 ensemble layers against a synthetic SQLite history."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from ensemble import EnsembleMathCore  # noqa: E402


def _build_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE player_box_scores (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             player_id TEXT, player_name TEXT, game_id TEXT, date DATE,
             team TEXT, opponent TEXT, minutes REAL, points INTEGER,
             assists INTEGER, rebounds INTEGER, steals INTEGER, blocks INTEGER,
             turnovers INTEGER, field_goals_made INTEGER, field_goals_attempted INTEGER,
             threes_made INTEGER, threes_attempted INTEGER, free_throws_made INTEGER,
             free_throws_attempted INTEGER, usage_rate REAL,
             offensive_rating REAL, defensive_rating REAL)"""
    )
    rows = []
    # Star A: consistent 30-min, ~21 PTS, ~6 AST player on LVA (12 games),
    # with co-moving game-to-game variation so correlations are estimable.
    for i in range(12):
        hot = i % 3  # 0/1/2 — drives correlated PTS/AST/3PM swings
        rows.append(("p1", "Star A", f"g{i}", f"2026-05-{i+1:02d}", "LVA", "NYL",
                     30, 20 + hot, 5 + hot, 7 + (i % 2), i % 2, (i + 1) % 2,
                     2, 8, 16, 1 + hot, 5, 2, 2, 24.0))
    # Teammate B: high-usage star on the same team.
    for i in range(12):
        rows.append(("p2", "Teammate B", f"g{i}", f"2026-05-{i+1:02d}", "LVA", "NYL",
                     32, 25, 4, 9, 1, 2, 3, 10, 20, 1, 4, 4, 5, 30.0))
    # Opponent C on another team (should never affect LVA redistribution).
    for i in range(12):
        rows.append(("p3", "Opponent C", f"g{i}", f"2026-05-{i+1:02d}", "NYL", "LVA",
                     31, 18, 7, 5, 2, 0, 2, 7, 15, 2, 6, 2, 2, 26.0))
    conn.executemany(
        """INSERT INTO player_box_scores
           (player_id, player_name, game_id, date, team, opponent, minutes, points,
            assists, rebounds, steals, blocks, turnovers, field_goals_made,
            field_goals_attempted, threes_made, threes_attempted, free_throws_made,
            free_throws_attempted, usage_rate)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    conn.close()


class TestEnsemble(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        _build_db(cls.db_path)
        cls.core = EnsembleMathCore(db_path=cls.db_path)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.db_path)

    def test_projection_tracks_history(self):
        proj = self.core.run_projection("Star A", "PTS", {}, n_sims=2000)
        self.assertIsNotNone(proj)
        # ~21 PPG history; projection should land near it.
        self.assertAlmostEqual(proj["projected_value"], 21, delta=4)
        self.assertGreaterEqual(proj["projected_minutes"], 20)
        self.assertEqual(proj["games_sampled"], 12)

    def test_insufficient_history_returns_none(self):
        self.assertIsNone(self.core.run_projection("Nobody", "PTS", {}, n_sims=500))

    def test_no_lookahead_with_as_of_date(self):
        # Cutting history before the first stored game leaves nothing to project from.
        proj = self.core.run_projection("Star A", "PTS", {}, as_of_date="2026-05-01", n_sims=500)
        self.assertIsNone(proj)
        # A mid-season cut uses only earlier games.
        proj = self.core.run_projection("Star A", "PTS", {}, as_of_date="2026-05-06", n_sims=500)
        self.assertEqual(proj["games_sampled"], 5)

    def test_usage_redistribution_boosts_when_teammate_out(self):
        base = self.core.run_projection("Star A", "PTS", {}, n_sims=4000)
        boosted = self.core.run_projection(
            "Star A", "PTS", {"out_players": ["Teammate B"]}, n_sims=4000
        )
        self.assertGreater(boosted["usage_boost"], 1.0)
        self.assertIn("Teammate B", boosted["usage_boost_from"])
        self.assertGreater(boosted["projected_value"], base["projected_value"])

    def test_redistribution_ignores_other_teams_and_self(self):
        boost, credited = self.core.usage_boost("Star A", ["Opponent C", "Star A"])
        self.assertEqual(boost, 1.0)
        self.assertEqual(credited, [])

    def test_p_over_line_present_and_sane(self):
        proj = self.core.run_projection("Star A", "PTS", {"line": 20.5}, n_sims=4000)
        self.assertIn("p_over_line", proj)
        self.assertGreater(proj["p_over_line"], 0.0)
        self.assertLess(proj["p_over_line"], 1.0)
        # A line far above any plausible outcome must yield ~zero probability.
        high = self.core.run_projection("Star A", "PTS", {"line": 60.5}, n_sims=4000)
        self.assertLess(high["p_over_line"], 0.05)

    def test_counting_stats_are_integervalued_distribution(self):
        proj = self.core.run_projection("Star A", "AST", {}, n_sims=2000)
        self.assertIsNotNone(proj)
        self.assertAlmostEqual(proj["projected_value"], 6, delta=2.5)

    def test_correlation_matrix(self):
        corr = self.core.stat_correlations(min_games=5)
        # Pairs exist and values are valid correlations.
        self.assertTrue(corr)
        for v in corr.values():
            self.assertGreaterEqual(v, -1.0)
            self.assertLessEqual(v, 1.0)


if __name__ == "__main__":
    unittest.main()
