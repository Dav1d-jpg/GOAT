"""Historische OHLCV-Daten von HuggingFace importieren.

Dataset: zongowo111/v2-crypto-ohlcv-data
  - Binance-Daten seit 2019, 9 Mio Zeilen, 1h + 15m Timeframes
  - Preise in USDT (nicht EUR) – fuer ML-Features irrelevant da alle
    Features relativ sind (Returns, RSI, MACD, BB-% etc.)

Strategie:
  - Nur 1h-Daten (Haupt-Trainings-Timeframe des Modells)
  - INSERT OR IGNORE: vorhandene Kraken-Daten werden NIE ueberschrieben
  - Cutoff: nur Daten vor 2026-04-01 (sicher vor live Kraken-Daten)
  - Nach dem Import: python features.py + python model.py manuell ausfuehren

Danach verfuegt das Modell ueber 6+ Jahre statt 1 Monat Trainingsdaten.
"""
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from huggingface_hub import hf_hub_download

from db import DB_PATH, get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Binance-Symbol → Bot-Symbol Mapping
SYMBOL_MAP: dict[str, str] = {
    "ADAUSDT":  "ADA/EUR",
    "ALGOUSDT": "ALGO/EUR",
    "BCHUSDT":  "BCH/EUR",
    "BNBUSDT":  "BNB/EUR",
    "BTCUSDT":  "BTC/EUR",
    "DOGEUSDT": "DOGE/EUR",
    "ETHUSDT":  "ETH/EUR",
    "LINKUSDT": "LINK/EUR",
    "LTCUSDT":  "LTC/EUR",
    "NEARUSDT": "NEAR/EUR",
    "SOLUSDT":  "SOL/EUR",
    "XRPUSDT":  "XRP/EUR",
}

REPO_ID     = "zongowo111/v2-crypto-ohlcv-data"
TIMEFRAME   = "1h"

# Nur historische Daten importieren – kein Konflikt mit Live-Kraken-Daten
CUTOFF_TS_MS = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _get_existing_count(symbol: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol=? AND timeframe='1h'",
            (symbol,)
        ).fetchone()[0]


def import_symbol(binance_sym: str, bot_sym: str) -> int:
    """Ein Symbol von HuggingFace laden und in die DB importieren."""
    coin = binance_sym.replace("USDT", "")
    file_path = f"klines/{binance_sym}/{coin}_{TIMEFRAME}.parquet"

    log.info("Lade %s (%s) ...", bot_sym, file_path)

    try:
        local_path = hf_hub_download(
            repo_id=REPO_ID,
            filename=file_path,
            repo_type="dataset",
        )
    except Exception as exc:
        log.error("  Download fehlgeschlagen fuer %s: %s", binance_sym, exc)
        return 0

    try:
        df = pd.read_parquet(local_path)
    except Exception as exc:
        log.error("  Parquet-Lesen fehlgeschlagen: %s", exc)
        return 0

    # Timestamp-Spalte normalisieren
    if "open_time" in df.columns:
        ts_col = "open_time"
    elif "timestamp" in df.columns:
        ts_col = "timestamp"
    else:
        log.error("  Keine Timestamp-Spalte gefunden. Spalten: %s", list(df.columns))
        return 0

    # Timestamp in Millisekunden bringen (datetime64[ns] = ns seit Epoch UTC)
    if str(df[ts_col].dtype).startswith("datetime"):
        df["ts_ms"] = df[ts_col].astype("int64") // 1_000_000
    else:
        sample = int(df[ts_col].iloc[0])
        if sample > 1_000_000_000_000:
            df["ts_ms"] = df[ts_col].astype("int64")
        else:
            df["ts_ms"] = df[ts_col].astype("int64") * 1000

    # Nur historische Daten (vor Cutoff)
    df = df[df["ts_ms"] < CUTOFF_TS_MS].copy()
    if df.empty:
        log.warning("  Keine Daten vor Cutoff fuer %s", bot_sym)
        return 0

    # Pflicht-Spalten pruefen
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        log.error("  Fehlende Spalten: %s", missing)
        return 0

    # Vektorisiert statt iterrows() – viel schneller bei 50k+ Zeilen
    n = len(df)
    rows = list(zip(
        [bot_sym] * n,
        ["1h"]    * n,
        df["ts_ms"].tolist(),
        df["open"].tolist(),
        df["high"].tolist(),
        df["low"].tolist(),
        df["close"].tolist(),
        df["volume"].tolist(),
    ))

    # Batch-Insert mit INSERT OR IGNORE (schuetzt vorhandene Kraken-Daten)
    BATCH = 5000
    inserted = 0
    with get_connection() as conn:
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            conn.executemany(
                """INSERT OR IGNORE INTO ohlcv
                   (symbol, timeframe, timestamp, open, high, low, close, volume)
                   VALUES (?,?,?,?,?,?,?,?)""",
                batch,
            )
            conn.commit()
            inserted += len(batch)

    log.info("  %s: %d Zeilen importiert (bis %s)",
             bot_sym, len(rows),
             datetime.fromtimestamp(df["ts_ms"].max() / 1000).strftime("%Y-%m-%d"))
    return len(rows)


def verify_import() -> None:
    """Prueft die importierten Daten auf Konsistenz."""
    log.info("=" * 50)
    log.info("VERIFIKATION")
    log.info("=" * 50)

    with sqlite3.connect(DB_PATH) as conn:
        for bot_sym in sorted(SYMBOL_MAP.values()):
            row = conn.execute(
                """SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
                   FROM ohlcv WHERE symbol=? AND timeframe='1h'""",
                (bot_sym,)
            ).fetchone()
            count, ts_min, ts_max = row
            if count == 0:
                log.warning("  %s: KEINE DATEN", bot_sym)
                continue
            dt_min = datetime.fromtimestamp(ts_min / 1000).strftime("%Y-%m-%d")
            dt_max = datetime.fromtimestamp(ts_max / 1000).strftime("%Y-%m-%d")

            # Luecken pruefen (grobe Kontrolle: erwartete Zeilen vs tatsaechliche)
            expected_hours = (ts_max - ts_min) / 3_600_000
            gap_pct = (1 - count / max(expected_hours, 1)) * 100

            status = "OK" if gap_pct < 5 else f"WARNUNG {gap_pct:.0f}% Luecken"
            log.info("  %-14s %6d Zeilen  %s bis %s  [%s]",
                     bot_sym, count, dt_min, dt_max, status)


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("OHLCV History Import gestartet")
    log.info("Cutoff: %s (nur aeltere Daten werden importiert)",
             datetime.fromtimestamp(CUTOFF_TS_MS / 1000).strftime("%Y-%m-%d"))
    log.info("Symbole: %d", len(SYMBOL_MAP))
    log.info("=" * 60)

    # Vorher-Zustand
    with sqlite3.connect(DB_PATH) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE timeframe='1h'"
        ).fetchone()[0]
    log.info("Vorhandene 1h-Zeilen VOR Import: %d", before)

    total = 0
    failed = []
    for binance_sym, bot_sym in SYMBOL_MAP.items():
        try:
            n = import_symbol(binance_sym, bot_sym)
            total += n
            time.sleep(0.5)  # Rate limiting
        except Exception as exc:
            log.error("Fehler bei %s: %s", bot_sym, exc)
            failed.append(bot_sym)

    # Nachher-Zustand
    with sqlite3.connect(DB_PATH) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE timeframe='1h'"
        ).fetchone()[0]

    log.info("=" * 60)
    log.info("Import abgeschlossen")
    log.info("  Neue Zeilen eingefuegt: %d", after - before)
    log.info("  Gesamt 1h-Zeilen jetzt: %d", after)
    if failed:
        log.warning("  Fehlgeschlagen: %s", failed)
    log.info("=" * 60)

    verify_import()

    log.info("=" * 60)
    log.info("Naechste Schritte:")
    log.info("  1. python features.py       <- Features neu berechnen")
    log.info("  2. python model.py          <- Modell neu trainieren")
    log.info("=" * 60)
