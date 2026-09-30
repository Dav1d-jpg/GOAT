"""Setzt das Paper-Trading komplett zurück – Marktdaten bleiben erhalten.

Leert ALLE Trading-Tabellen (auch paper_shorts + stoploss_cooldown, die erst
nach der ersten Version dazukamen) für einen echten sauberen 1000-EUR-Start.
"""
import sqlite3
from db import DB_PATH

# Alle Tabellen, die zum Paper-Trading-Zustand gehören
TABLES = [
    "paper_trades",
    "paper_portfolio",
    "paper_shorts",
    "paper_balance",
    "trailing_stops",
    "stoploss_cooldown",
]

with sqlite3.connect(DB_PATH) as conn:
    for tbl in TABLES:
        # Nur löschen, wenn die Tabelle existiert (robust gegen frische DBs)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone()
        if exists:
            conn.execute(f"DELETE FROM {tbl}")
    conn.commit()

print("Paper-Trading zurückgesetzt:")
print("  - Trades:          geloescht")
print("  - Portfolio:       geloescht")
print("  - Shorts:          geloescht")
print("  - Balance-History: geloescht")
print("  - Trailing Stops:  geloescht")
print("  - Cooldowns:       geloescht")
print("  - Startkapital:    1000 EUR (wird beim naechsten Zyklus gesetzt)")
