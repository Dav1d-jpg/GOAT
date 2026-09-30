import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')

count = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
print("Trades seit Reset:", count)

last_price = conn.execute("SELECT MAX(timestamp) FROM live_prices").fetchone()[0]
if last_price:
    ts = datetime.datetime.fromtimestamp(last_price/1000)
    diff = datetime.datetime.now() - ts
    print(f"Letzter Live-Preis: {ts.strftime('%d.%m %H:%M:%S')}")
    print(f"Jetzt:              {datetime.datetime.now().strftime('%d.%m %H:%M:%S')}")
    print(f"Differenz:          {diff}")

conn.close()
