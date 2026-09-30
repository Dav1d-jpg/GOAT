import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')

print("=== Quellen-Verteilung ===")
rows = conn.execute("SELECT source, COUNT(*) FROM news_sentiment GROUP BY source ORDER BY COUNT(*) DESC").fetchall()
for r in rows:
    print(f"  {r[0]:30s} {r[1]}")

print("\n=== RoBERTa-Artikel Zeitraum ===")
row = conn.execute("""
    SELECT MIN(published), MAX(published), COUNT(*)
    FROM news_sentiment WHERE source='HuggingFace-RoBERTa' AND published > 0
""").fetchone()
if row[0]:
    print(f"  Von: {datetime.datetime.fromtimestamp(row[0]/1000).strftime('%d.%m.%Y')}")
    print(f"  Bis: {datetime.datetime.fromtimestamp(row[1]/1000).strftime('%d.%m.%Y')}")
    print(f"  Anzahl mit Datum: {row[2]}")

print("\n=== URL-Duplikate im Dataset (Stichprobe) ===")
dup = conn.execute("""
    SELECT url, COUNT(*) as n FROM news_sentiment
    GROUP BY url HAVING n > 1 LIMIT 5
""").fetchall()
print(f"  URLs mit Duplikaten: {len(dup)}")

conn.close()
