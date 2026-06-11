import os

from shared.base_agent import db_connect
from shared.db import AUTO_PK, IS_POSTGRES

DB_PATH = os.path.join(os.path.dirname(__file__), '../../data/hoopstats_wnba.db')

def get_connection():
    if not IS_POSTGRES:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return db_connect(DB_PATH)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Create tables for Box Scores and Rolling Baselines.
    # Dates are stored as ISO TEXT so both SQLite and Postgres return strings.
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS player_box_scores (
        id {AUTO_PK},
        player_id TEXT,
        player_name TEXT,
        game_id TEXT,
        date TEXT,
        team TEXT,
        opponent TEXT,
        minutes REAL,
        points INTEGER,
        assists INTEGER,
        rebounds INTEGER,
        steals INTEGER,
        blocks INTEGER,
        turnovers INTEGER,
        field_goals_made INTEGER,
        field_goals_attempted INTEGER,
        threes_made INTEGER,
        threes_attempted INTEGER,
        free_throws_made INTEGER,
        free_throws_attempted INTEGER,
        usage_rate REAL,
        offensive_rating REAL,
        defensive_rating REAL
    )
    ''')
    
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS team_box_scores (
        id {AUTO_PK},
        team TEXT,
        game_id TEXT,
        date TEXT,
        opponent TEXT,
        pace REAL,
        offensive_efficiency REAL,
        defensive_efficiency REAL
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS rolling_baselines (
        player_id TEXT PRIMARY KEY,
        player_name TEXT,
        last_updated TEXT,
        l5_minutes REAL,
        l5_usage_rate REAL,
        l10_usage_rate REAL,
        season_offensive_rating REAL,
        season_defensive_rating REAL
    )
    ''')
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
