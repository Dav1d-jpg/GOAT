import sqlite3
from datetime import datetime
from db import DB_PATH

with sqlite3.connect(DB_PATH) as conn:
    ohlcv_5m = conn.execute("SELECT COUNT(*) FROM ohlcv WHERE timeframe='5m'").fetchone()[0]
    ohlcv_1h = conn.execute("SELECT COUNT(*) FROM ohlcv WHERE timeframe='1h'").fetchone()[0]
    tickers  = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
    news     = conn.execute("SELECT COUNT(*) FROM news_sentiment").fetchone()[0]
    latest   = conn.execute("SELECT MAX(timestamp) FROM tickers").fetchone()[0]

if latest:
    print("Letztes Update:", datetime.fromtimestamp(latest/1000).strftime("%d.%m.%Y %H:%M:%S"))
print(f"OHLCV 5min:  {ohlcv_5m:,} Kerzen")
print(f"OHLCV 1h:    {ohlcv_1h:,} Kerzen")
print(f"Tickers:     {tickers:,} Snapshots")
print(f"News:        {news:,} Artikel analysiert")
