import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')

print("=== Neueste 1h-Features je Symbol ===")
rows = conn.execute("""
    SELECT symbol, MAX(timestamp) as ts FROM features
    WHERE timeframe='1h' GROUP BY symbol ORDER BY ts DESC LIMIT 10
""").fetchall()
for r in rows:
    ts = datetime.datetime.fromtimestamp(r[1]/1000)
    diff = datetime.datetime.now() - ts
    h = diff.total_seconds() / 3600
    print(f"  {r[0]:12s} {ts.strftime('%d.%m %H:%M')} ({h:.1f}h alt)")

print("\n=== Neueste Signale je Symbol ===")
rows2 = conn.execute("""
    SELECT symbol, MAX(timestamp) as ts FROM signals
    GROUP BY symbol ORDER BY ts DESC LIMIT 5
""").fetchall()
for r in rows2:
    ts = datetime.datetime.fromtimestamp(r[1]/1000)
    diff = datetime.datetime.now() - ts
    h = diff.total_seconds() / 3600
    print(f"  {r[0]:12s} {ts.strftime('%d.%m %H:%M')} ({h:.1f}h alt)")

conn.close()
