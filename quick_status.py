import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')
now = datetime.datetime.now()

bal = conn.execute('SELECT cash_eur, invested_eur, total_value FROM paper_balance ORDER BY timestamp DESC LIMIT 1').fetchone()
print(f'Portfolio: {bal[2]:.2f} EUR | P/L: {bal[2]-1000:+.2f} EUR | Cash: {bal[0]:.2f}')

print()
pos = conn.execute('SELECT symbol, amount, avg_buy_price FROM paper_portfolio').fetchall()
for p in pos:
    live = conn.execute('SELECT price FROM live_prices WHERE symbol=? ORDER BY timestamp DESC LIMIT 1', (p[0],)).fetchone()
    if live:
        pl_pct = (live[0] - p[2]) / p[2] * 100
        pl_eur = (live[0] - p[2]) * p[1]
        print(f'  {p[0]:<14} {pl_pct:+.2f}%  {pl_eur:+.2f} EUR  (kauf={p[2]:.5f} jetzt={live[0]:.5f})')

print()
sells = conn.execute("SELECT timestamp, symbol, pl_eur, reason FROM paper_trades WHERE action='SELL' ORDER BY timestamp DESC LIMIT 5").fetchall()
if sells:
    print('Letzte Verkaufe:')
    for s in sells:
        dt = datetime.datetime.fromtimestamp(s[0]/1000).strftime('%H:%M')
        print(f'  {dt} {s[1]:<14} {s[2]:+.2f} EUR | {s[3]}')

conn.close()
