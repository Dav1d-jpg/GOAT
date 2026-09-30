import sqlite3
conn = sqlite3.connect('data/market_data.db')
total = conn.execute("SELECT COUNT(*) FROM news_sentiment").fetchone()[0]
roberta = conn.execute("SELECT COUNT(*) FROM news_sentiment WHERE source='HuggingFace-RoBERTa'").fetchone()[0]
print(f"Gesamt Artikel: {total}")
print(f"RoBERTa-scored: {roberta}")
conn.close()
