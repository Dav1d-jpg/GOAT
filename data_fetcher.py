"""Phase 1: Marktdaten von Kraken holen und in SQLite speichern."""
import os
import time
import logging

import ccxt
from dotenv import load_dotenv

from db import get_connection, init_db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

TOP_N = 20  # wie viele Paare nach Volumen beobachtet werden

TIMEFRAMES: dict[str, int] = {
    "5m": 288,   # ~24h rueckblick
    "1h": 500,   # ~20 Tage rueckblick
}


def build_exchange() -> ccxt.kraken:
    """Kraken-Instanz erstellen; API-Keys werden aus .env geladen."""
    return ccxt.kraken({
        "apiKey": os.getenv("KRAKEN_API_KEY", ""),
        "secret": os.getenv("KRAKEN_API_SECRET", ""),
        "enableRateLimit": True,
    })


def get_top_eur_symbols(exchange: ccxt.kraken, top_n: int = TOP_N) -> list[str]:
    """Top N EUR-Paare nach 24h-Handelsvolumen zurueckgeben."""
    log.info("Lade alle Ticker von Kraken …")
    tickers = exchange.fetch_tickers()
    eur = {
        sym: t
        for sym, t in tickers.items()
        if sym.endswith("/EUR") and t.get("quoteVolume")
    }
    ranked = sorted(eur.items(), key=lambda x: x[1]["quoteVolume"] or 0, reverse=True)
    symbols = [sym for sym, _ in ranked[:top_n]]
    log.info("Top %d EUR-Paare: %s", top_n, symbols)
    return symbols


def fetch_and_store_ohlcv(
    exchange: ccxt.kraken,
    symbols: list[str],
    timeframe: str = "1h",
    limit: int = 500,
) -> None:
    """OHLCV-Kerzen fuer ein Timeframe holen und in SQLite speichern."""
    with get_connection() as conn:
        for symbol in symbols:
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                if not candles:
                    log.warning("Keine Kerzen fuer %s (%s)", symbol, timeframe)
                    continue
                # OR REPLACE: die juengste Kerze ist beim ersten Insert noch
                # unfertig und MUSS beim naechsten Fetch ueberschrieben werden
                conn.executemany(
                    """INSERT OR REPLACE INTO ohlcv
                       (symbol, timeframe, timestamp, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (symbol, timeframe, c[0], c[1], c[2], c[3], c[4], c[5])
                        for c in candles
                    ],
                )
                conn.commit()
                log.info("Gespeichert: %d Kerzen (%s) fuer %s", len(candles), timeframe, symbol)
            except Exception as exc:
                log.warning("OHLCV-Fehler fuer %s (%s): %s", symbol, timeframe, exc)
            time.sleep(exchange.rateLimit / 1000)


def fetch_and_store_tickers(exchange: ccxt.kraken, symbols: list[str]) -> None:
    """Aktuellen Ticker-Snapshot speichern."""
    ts = int(time.time() * 1000)
    with get_connection() as conn:
        for symbol in symbols:
            try:
                t = exchange.fetch_ticker(symbol)
                conn.execute(
                    """INSERT OR IGNORE INTO tickers
                       (symbol, timestamp, bid, ask, last, volume_24h, change_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (symbol, ts, t["bid"], t["ask"], t["last"], t["quoteVolume"], t["percentage"]),
                )
                conn.commit()
            except Exception as exc:
                log.warning("Ticker-Fehler fuer %s: %s", symbol, exc)
            time.sleep(exchange.rateLimit / 1000)


def fetch_live_prices(exchange: ccxt.kraken, symbols: list[str]) -> dict[str, float]:
    """Alle Symbole in EINEM API-Call abfragen und Live-Preise speichern.

    Viel effizienter als einzelne fetch_ticker-Calls – ideal fuer den 1-Min-Loop.
    Gibt Dict {symbol: preis} zurueck fuer sofortige Weiterverwendung.
    """
    ts = int(time.time() * 1000)
    prices: dict[str, float] = {}
    try:
        all_tickers = exchange.fetch_tickers(symbols)
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS live_prices (
                    symbol    TEXT    NOT NULL,
                    timestamp INTEGER NOT NULL,
                    price     REAL    NOT NULL,
                    bid       REAL,
                    ask       REAL,
                    PRIMARY KEY (symbol, timestamp)
                )
            """)
            rows = []
            for symbol in symbols:
                t = all_tickers.get(symbol)
                if t and t.get("last"):
                    price = float(t["last"])
                    prices[symbol] = price
                    rows.append((symbol, ts, price, t.get("bid"), t.get("ask")))
            conn.executemany(
                "INSERT OR IGNORE INTO live_prices (symbol, timestamp, price, bid, ask) VALUES (?,?,?,?,?)",
                rows,
            )
            conn.commit()
        log.debug("Live-Preise aktualisiert: %d Symbole", len(prices))
    except Exception as exc:
        log.warning("Live-Preis-Fehler: %s", exc)
    return prices


def show_account_balance(exchange: ccxt.kraken) -> None:
    """Kontostand ausgeben – benoetigt API-Key."""
    if not os.getenv("KRAKEN_API_KEY"):
        log.info("Kein API-Key gesetzt – Kontostand wird uebersprungen.")
        return
    try:
        balance = exchange.fetch_balance()
        non_zero = {k: v for k, v in balance["total"].items() if v and v > 0}
        log.info("Kontostand: %s", non_zero)
    except Exception as exc:
        log.warning("Kontostand konnte nicht geladen werden: %s", exc)


if __name__ == "__main__":
    init_db()
    exchange = build_exchange()
    symbols = get_top_eur_symbols(exchange)
    for tf, limit in TIMEFRAMES.items():
        fetch_and_store_ohlcv(exchange, symbols, timeframe=tf, limit=limit)
    fetch_and_store_tickers(exchange, symbols)
    show_account_balance(exchange)
    log.info("Fertig. Daten liegen in data/market_data.db")
