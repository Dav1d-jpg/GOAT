"""SQLite database setup and connection helper."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "market_data.db"


def get_connection() -> sqlite3.Connection:
    """Return a connection to the local SQLite database."""
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    """Create tables if they don't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                symbol    TEXT    NOT NULL,
                timeframe TEXT    NOT NULL,
                timestamp INTEGER NOT NULL,
                open      REAL    NOT NULL,
                high      REAL    NOT NULL,
                low       REAL    NOT NULL,
                close     REAL    NOT NULL,
                volume    REAL    NOT NULL,
                PRIMARY KEY (symbol, timeframe, timestamp)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickers (
                symbol     TEXT    NOT NULL,
                timestamp  INTEGER NOT NULL,
                bid        REAL,
                ask        REAL,
                last       REAL,
                volume_24h REAL,
                change_pct REAL,
                PRIMARY KEY (symbol, timestamp)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_sentiment (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                source     TEXT    NOT NULL,
                title      TEXT    NOT NULL,
                url        TEXT    UNIQUE,
                published  INTEGER,
                fetched_at INTEGER NOT NULL,
                sentiment  TEXT,
                score      REAL,
                summary    TEXT
            )
        """)
        conn.commit()
