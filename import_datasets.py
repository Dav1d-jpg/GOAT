"""Historische Daten importieren:
  1. Kraken via ccxt  → 2 Jahre 1h-OHLCV (EUR-Paare, direkt nutzbar)
  2. HuggingFace      → 180k vorgelabelte Crypto-News (Sentiment)

Nach dem Import:
  python features.py
  python model.py
"""
import logging
import os
import time

from dotenv import load_dotenv

from db import get_connection, init_db

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TWO_YEARS_MS  = 2 * 365 * 24 * 3600 * 1000
CANDLES_LIMIT = 720   # Kraken-Maximum pro Request


# ── 1. Historische Kraken OHLCV-Daten ─────────────────────────────────────────

def import_historical_ohlcv() -> None:
    """2 Jahre 1h-OHLCV von Kraken holen und in die DB speichern."""
    import ccxt

    exchange = ccxt.kraken({
        "apiKey":          os.getenv("KRAKEN_API_KEY", ""),
        "secret":          os.getenv("KRAKEN_API_SECRET", ""),
        "enableRateLimit": True,
    })

    # Alle EUR-Paare aus unserer DB
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM ohlcv WHERE timeframe='1h'"
        ).fetchall()
    symbols = [r[0] for r in rows] if rows else [
        "BTC/EUR","ETH/EUR","SOL/EUR","XRP/EUR","ADA/EUR",
        "DOGE/EUR","LTC/EUR","NEAR/EUR","XLM/EUR","ALGO/EUR",
    ]

    since_ms = int(time.time() * 1000) - TWO_YEARS_MS
    log.info("Lade 2 Jahre 1h-OHLCV für %d Symbole von Kraken …", len(symbols))

    total = 0
    for symbol in symbols:
        log.info("  %s …", symbol)
        cursor = since_ms

        while True:
            try:
                candles = exchange.fetch_ohlcv(symbol, "1h", since=cursor, limit=CANDLES_LIMIT)
            except Exception as exc:
                log.warning("  Fehler bei %s: %s", symbol, exc)
                break

            if not candles:
                break

            with get_connection() as conn:
                conn.executemany(
                    """INSERT OR IGNORE INTO ohlcv
                       (symbol, timeframe, timestamp, open, high, low, close, volume)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    [(symbol, "1h", c[0], c[1], c[2], c[3], c[4], c[5]) for c in candles],
                )
                conn.commit()

            total  += len(candles)
            cursor  = candles[-1][0] + 1  # nächste Seite

            # Wenn letzte Seite kürzer als Limit → fertig
            if len(candles) < CANDLES_LIMIT:
                break

            time.sleep(exchange.rateLimit / 1000)

        log.info("  %s: fertig", symbol)
        time.sleep(1)

    log.info("OHLCV-Import abgeschlossen: %d Kerzen total", total)


# ── 2. News-Sentiment von HuggingFace ─────────────────────────────────────────

def import_news_sentiment() -> None:
    """180k vorgelabelte Crypto-News-Artikel von HuggingFace importieren."""
    try:
        from datasets import load_dataset
    except ImportError:
        log.error("datasets-Bibliothek fehlt. Installiere mit: pip install datasets")
        return

    log.info("Lade News-Sentiment-Dataset von HuggingFace …")

    # label: 0=neutral, 1=positive, 2=negative
    LABEL_MAP = {0: ("neutral", 0.5), 1: ("positive", 0.8), 2: ("negative", 0.2)}

    try:
        ds = load_dataset(
            "SahandNZ/cryptonews-articles-with-price-momentum-labels",
            split="train",
            streaming=True,
        )
    except Exception as exc:
        log.error("Dataset konnte nicht geladen werden: %s", exc)
        return

    now       = int(time.time() * 1000)
    imported  = 0
    batch: list = []
    BATCH_SIZE  = 2000

    def flush() -> None:
        with get_connection() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO news_sentiment
                   (source, title, url, published, fetched_at, sentiment, score, summary)
                   VALUES (?,?,?,?,?,?,?,?)""",
                batch,
            )
            conn.commit()

    for row in ds:
        try:
            text  = str(row.get("text") or "")[:400].strip()
            if not text:
                continue
            url   = str(row.get("url") or f"hf_news_{imported}")
            label = int(row.get("label") or 0)
            sentiment, score = LABEL_MAP.get(label, ("neutral", 0.5))

            raw_ts = row.get("datetime") or row.get("date")
            try:
                from datetime import datetime
                if isinstance(raw_ts, str):
                    published = int(datetime.fromisoformat(raw_ts).timestamp() * 1000)
                elif raw_ts is not None:
                    published = int(float(str(raw_ts)) * 1000)
                else:
                    published = now
            except Exception:
                published = now

            batch.append(("HuggingFace", text, url, published, now, sentiment, score, None))
            imported += 1

            if len(batch) >= BATCH_SIZE:
                flush()
                batch.clear()
                log.info("News: %d Artikel importiert …", imported)
        except Exception as exc:
            log.debug("Zeile übersprungen: %s", exc)

    if batch:
        flush()

    log.info("News-Import abgeschlossen: %d Artikel", imported)


# ── Hauptprogramm ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    log.info("=" * 60)
    log.info("Daten-Import gestartet")
    log.info("=" * 60)

    import_historical_ohlcv()
    import_news_sentiment()

    log.info("=" * 60)
    log.info("Fertig! Jetzt ausführen:")
    log.info("  python features.py")
    log.info("  python model.py")
    log.info("=" * 60)
