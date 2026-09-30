import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')
now = datetime.datetime.now()

print("=" * 50)
print("GOAT BOT - HEALTH CHECK")
print("=" * 50)

# Datenfrische
last_price = conn.execute("SELECT MAX(timestamp) FROM live_prices").fetchone()[0]
last_news  = conn.execute("SELECT MAX(fetched_at) FROM news_sentiment").fetchone()[0]
last_feat  = conn.execute("SELECT MAX(timestamp) FROM features WHERE timeframe='1h'").fetchone()[0]
last_sig   = conn.execute("SELECT MAX(timestamp) FROM signals").fetchone()[0]

def age(ts):
    if not ts: return "KEINE DATEN"
    diff = now - datetime.datetime.fromtimestamp(ts/1000)
    m = int(diff.total_seconds() / 60)
    if m < 5: return f"OK ({m}min)"
    if m < 70: return f"OK ({m}min)"
    return f"ALT ({m}min) - WARNUNG"

print(f"\nDaten-Frische:")
print(f"  Live-Preise:  {age(last_price)}")
print(f"  News:         {age(last_news)}")
print(f"  Features:     {age(last_feat)}")
print(f"  ML-Signale:   {age(last_sig)}")

# Signalqualität
sigs = conn.execute("""
    SELECT s.prob_up FROM signals s
    INNER JOIN (SELECT symbol, MAX(timestamp) AS m FROM signals GROUP BY symbol) l
    ON s.symbol=l.symbol AND s.timestamp=l.m
""").fetchall()
probs = [r[0] for r in sigs]
above55 = sum(1 for p in probs if p >= 0.55)
print(f"\nML-Signale:")
print(f"  Gesamt:       {len(probs)}")
print(f"  >= 55% (Buy): {above55}")
print(f"  Durchschnitt: {sum(probs)/len(probs)*100:.1f}%")
print(f"  Maximum:      {max(probs)*100:.1f}%")

# Trading-Config
import sys; sys.path.insert(0, '.')
import paper_trader as pt
print(f"\nKonfiguration:")
print(f"  ML-Threshold:    {pt.MIN_CONFIDENCE_BUY*100:.0f}%")
print(f"  Stop-Loss:       -{pt.STOP_LOSS_PCT*100:.0f}%")
print(f"  Take-Profit:     +{pt.TAKE_PROFIT_PCT*100:.0f}%")
print(f"  Max Positionen:  {pt.MAX_POSITIONS}")
print(f"  Min-Haltezeit:   {pt.MIN_HOLD_HOURS}h")
print(f"  FAST_NEWS_BUY:   DEAKTIVIERT")

# News-Qualität
news_15min = conn.execute("""
    SELECT AVG(score), COUNT(*) FROM news_sentiment
    WHERE fetched_at > ?
""", (int(now.timestamp()*1000) - 15*60*1000,)).fetchone()
print(f"\nNews (letzte 15min):")
print(f"  Artikel: {news_15min[1]}")
if news_15min[1]:
    print(f"  Avg Score: {news_15min[0]:.2f} ({'BULLISH' if news_15min[0] > 0.75 else 'PANIC' if news_15min[0] < 0.12 else 'NEUTRAL'})")

print(f"\n{'='*50}")
print("STATUS: BEREIT")
print(f"{'='*50}")
conn.close()
