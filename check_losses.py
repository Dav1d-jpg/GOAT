import sqlite3, datetime
conn = sqlite3.connect('data/market_data.db')

print("=== Alle Trades seit Reset ===")
rows = conn.execute("""
    SELECT timestamp, symbol, action, price, pl_eur, reason, prob_up
    FROM paper_trades ORDER BY timestamp
""").fetchall()
for r in rows:
    ts = datetime.datetime.fromtimestamp(r[0]/1000).strftime('%d.%m %H:%M')
    pl = f"{r[4]:+.2f}" if r[4] is not None else "offen"
    prob = f"{r[6]:.2f}" if r[6] else "0.00"
    print(f"{ts} | {r[2]:4s} | {r[1]:12s} | {r[3]:.4f} | P&L:{pl:>7} | prob={prob} | {r[5]}")

print()
sells = [(r[4], r[5]) for r in rows if r[2]=='SELL' and r[4] is not None]
if sells:
    total_pl = sum(s[0] for s in sells)
    wins = sum(1 for s in sells if s[0] > 0)
    print(f"Gesamt P&L: {total_pl:+.2f} EUR | Win-Rate: {wins}/{len(sells)} = {wins/len(sells)*100:.0f}%")
    print()
    print("Verluste nach Grund:")
    reasons = {}
    for pl, reason in sells:
        r = reason.split('(')[0]
        reasons[r] = reasons.get(r, [])
        reasons[r].append(pl)
    for r, pls in sorted(reasons.items(), key=lambda x: sum(x[1])):
        print(f"  {r:30s} {sum(pls):+.2f} EUR ({len(pls)} Trades, WR={sum(1 for p in pls if p>0)/len(pls)*100:.0f}%)")

conn.close()
