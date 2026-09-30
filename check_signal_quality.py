import sqlite3, pickle
import numpy as np

conn = sqlite3.connect('data/market_data.db')

# Aktuelle Signal-Verteilung
rows = conn.execute("""
    SELECT s.symbol, s.prob_up FROM signals s
    INNER JOIN (SELECT symbol, MAX(timestamp) AS max_ts FROM signals GROUP BY symbol) l
    ON s.symbol = l.symbol AND s.timestamp = l.max_ts
""").fetchall()

probs = [r[1] for r in rows]
print("=== Aktuelle ML-Signal-Verteilung ===")
print(f"Signale gesamt:    {len(probs)}")
print(f"Über 60% (kaufen): {sum(1 for p in probs if p >= 0.60)} ({sum(1 for p in probs if p >= 0.60)/len(probs)*100:.0f}%)")
print(f"Über 58%:          {sum(1 for p in probs if p >= 0.58)}")
print(f"Über 55%:          {sum(1 for p in probs if p >= 0.55)}")
print(f"Durchschnitt:      {np.mean(probs):.3f}")
print(f"Maximum:           {max(probs):.3f}")
print(f"Minimum:           {min(probs):.3f}")

# Trainingsdaten-Qualität
print("\n=== Trainingsdaten ===")
total = conn.execute("SELECT COUNT(*) FROM features WHERE timeframe='1h'").fetchone()[0]
print(f"OHLCV-Zeilen (1h): {total}")

# Sentiment-Abdeckung im Trainingszeitraum
sent = conn.execute("SELECT COUNT(*), MIN(published), MAX(published) FROM news_sentiment WHERE score IS NOT NULL AND published > 0").fetchone()
print(f"Sentiment-Artikel: {sent[0]}")

import datetime
if sent[1]:
    print(f"Zeitraum: {datetime.datetime.fromtimestamp(sent[1]/1000).strftime('%d.%m.%Y')} - {datetime.datetime.fromtimestamp(sent[2]/1000).strftime('%d.%m.%Y')}")

fg = conn.execute("SELECT COUNT(*) FROM fear_greed").fetchone()[0]
print(f"Fear&Greed-Eintraege: {fg}")

funding = conn.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM funding_rates").fetchone()
print(f"Funding Rates: {funding[1]} Eintraege ({funding[0]} Symbole)")

# Modell-Info
with open('models/main.pkl', 'rb') as f:
    bundle = pickle.load(f)
print(f"\nModell trainiert auf: {bundle.get('n_samples', '?')} Samples")
print(f"OOS Win-Rate laut Training: {bundle.get('oos_accuracy', '?')}")

conn.close()
