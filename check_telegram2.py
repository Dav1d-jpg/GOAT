import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')

sells = conn.execute("SELECT COUNT(*), SUM(CASE WHEN pl_eur > 0 THEN 1 ELSE 0 END), SUM(pl_eur) FROM paper_trades WHERE action='SELL' AND pl_eur IS NOT NULL").fetchone()
print(f"DB-Realitaet: {sells[0]} Trades, WR={sells[1]/sells[0]*100:.0f}%, P&L={sells[2]:.2f} EUR")

bal = conn.execute("SELECT total_value, timestamp FROM paper_balance ORDER BY timestamp DESC LIMIT 1").fetchone()
ts = datetime.datetime.fromtimestamp(bal[1]/1000).strftime('%d.%m %H:%M')
print(f"Portfolio: {bal[0]:.2f} EUR (Stand {ts})")
print(f"Jetzt: {datetime.datetime.now().strftime('%d.%m %H:%M')}")

# Schauen was notify_portfolio berechnet
import sys; sys.path.insert(0, '.')
from telegram_notify import notify_portfolio
import inspect
print("\ntelegram_notify.py notify_portfolio Quellcode:")
print(inspect.getsource(notify_portfolio))
conn.close()
