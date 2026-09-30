import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')

sells = conn.execute("SELECT COUNT(*), SUM(CASE WHEN pl_eur > 0 THEN 1 ELSE 0 END), SUM(pl_eur) FROM paper_trades WHERE action='SELL' AND pl_eur IS NOT NULL").fetchone()
total, wins, pl = sells
print(f"Telegram-Quelle: Trades={total}, Wins={wins}, WR={wins/total*100:.0f}%, P&L={pl:.2f} EUR")

bal = conn.execute("SELECT portfolio_value, timestamp FROM paper_balance ORDER BY timestamp DESC LIMIT 1").fetchone()
ts = datetime.datetime.fromtimestamp(bal[1]/1000).strftime('%H:%M:%S')
print(f"Portfolio-Wert: {bal[0]:.2f} EUR (Stand {ts})")

# Schauen wann die Telegram-Nachricht gesendet wurde (letztes stündliches Update)
last_hour = conn.execute("SELECT timestamp FROM paper_balance ORDER BY timestamp DESC LIMIT 1").fetchone()
print(f"Jetzt: {datetime.datetime.now().strftime('%H:%M:%S')}")
conn.close()
