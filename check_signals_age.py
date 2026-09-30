import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')
row = conn.execute("SELECT MAX(timestamp), COUNT(*) FROM signals").fetchone()
if row[0]:
    ts = datetime.datetime.fromtimestamp(row[0]/1000)
    print(f"Letztes Signal: {ts.strftime('%d.%m.%Y %H:%M')}")
    print(f"Signale gesamt: {row[1]}")
    print(f"Jetzt: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}")
conn.close()
