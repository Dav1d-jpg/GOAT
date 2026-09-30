import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')

total = conn.execute("SELECT COUNT(*) FROM news_sentiment").fetchone()[0]
with_score = conn.execute("SELECT COUNT(*) FROM news_sentiment WHERE score IS NOT NULL").fetchone()[0]
without_score = conn.execute("SELECT COUNT(*) FROM news_sentiment WHERE score IS NULL").fetchone()[0]

oldest = conn.execute("SELECT MIN(published) FROM news_sentiment WHERE published > 0").fetchone()[0]
newest = conn.execute("SELECT MAX(published) FROM news_sentiment").fetchone()[0]

print(f"Artikel gesamt:       {total}")
print(f"Mit Sentiment-Score:  {with_score}")
print(f"Ohne Score:           {without_score}")
if oldest:
    print(f"Zeitraum: {datetime.datetime.fromtimestamp(oldest/1000).strftime('%d.%m.%Y')} bis {datetime.datetime.fromtimestamp(newest/1000).strftime('%d.%m.%Y')}")

# Quellen
print("\nTop Quellen:")
rows = conn.execute("SELECT source, COUNT(*) as n FROM news_sentiment GROUP BY source ORDER BY n DESC LIMIT 10").fetchall()
for r in rows:
    print(f"  {r[0]:25s} {r[1]:6d}")
conn.close()
