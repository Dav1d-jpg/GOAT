import sqlite3
from datetime import datetime
from db import DB_PATH

with sqlite3.connect(DB_PATH) as conn:
    print("=== Letzte ML-Signale ===")
    rows = conn.execute(
        "SELECT symbol, signal, prob_up, timestamp FROM signals ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()
    for r in rows:
        t = datetime.fromtimestamp(r[3]/1000).strftime("%H:%M")
        print(f"  {t}  {r[0]:<12} {r[1]}  P(up)={r[2]*100:.0f}%")

    print("\n=== Portfolio ===")
    row = conn.execute(
        "SELECT cash_eur, invested_eur, total_value, timestamp FROM paper_balance ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    t = datetime.fromtimestamp(row[3]/1000).strftime("%H:%M:%S")
    print(f"  Wert: {row[2]:.2f}€  Cash: {row[0]:.2f}€  Stand: {t}")

    print("\n=== Offene Positionen ===")
    rows = conn.execute("SELECT symbol, amount, avg_buy_price FROM paper_portfolio").fetchall()
    if rows:
        for r in rows: print(f"  {r[0]:<12} {r[1]:.4f} @ {r[2]:.4f}€")
    else:
        print("  Keine")

    print("\n=== Letzter Live-Preis ===")
    row2 = conn.execute("SELECT MAX(timestamp) FROM live_prices").fetchone()
    if row2[0]:
        t2 = datetime.fromtimestamp(row2[0]/1000).strftime("%H:%M:%S")
        print(f"  {t2}")
    else:
        print("  Keine Live-Preise")
