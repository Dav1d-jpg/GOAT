"""Zusätzliche Markt-Features:
  1. Fear & Greed Index  – Marktpsychologie (alternative.me, kostenlos)
  2. Funding Rates       – Leverage-Sentiment (Binance, kostenlos)

Diese Features sind einzigartig – sie erfassen Dinge die RSI/MACD/BB nicht können.
"""
import logging
import sqlite3
import time
from datetime import datetime

import requests

from db import DB_PATH, get_connection

log = logging.getLogger(__name__)


# ── DB-Setup ──────────────────────────────────────────────────────────────────

def init_extra_tables() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fear_greed (
                timestamp INTEGER PRIMARY KEY,
                value     INTEGER NOT NULL,
                label     TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS funding_rates (
                symbol    TEXT    NOT NULL,
                timestamp INTEGER NOT NULL,
                rate      REAL    NOT NULL,
                PRIMARY KEY (symbol, timestamp)
            )
        """)
        conn.commit()


# ── Fear & Greed Index ────────────────────────────────────────────────────────
# 0-25  = Extreme Fear   → historisch guter Kaufzeitpunkt
# 25-45 = Fear
# 45-55 = Neutral
# 55-75 = Greed
# 75-100 = Extreme Greed → historisch guter Verkaufszeitpunkt

def fetch_fear_greed_history(days: int = 365) -> int:
    """Historischen Fear & Greed Index laden (bis zu 365 Tage)."""
    try:
        r = requests.get(
            f"https://api.alternative.me/fng/?limit={days}&format=json",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        rows = [
            (int(d["timestamp"]) * 1000, int(d["value"]), d["value_classification"])
            for d in data
        ]
        with get_connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO fear_greed (timestamp, value, label) VALUES (?,?,?)",
                rows,
            )
            conn.commit()
        log.info("Fear & Greed: %d Einträge importiert", len(rows))
        return len(rows)
    except Exception as exc:
        log.warning("Fear & Greed Fehler: %s", exc)
        return 0


def fetch_fear_greed_latest() -> dict | None:
    """Aktuellen Fear & Greed Wert holen und speichern."""
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1&format=json",
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()["data"][0]
        ts    = int(d["timestamp"]) * 1000
        value = int(d["value"])
        label = d["value_classification"]
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO fear_greed VALUES (?,?,?)", (ts, value, label)
            )
            conn.commit()
        log.info("Fear & Greed: %d (%s)", value, label)
        return {"timestamp": ts, "value": value, "label": label}
    except Exception as exc:
        log.warning("Fear & Greed aktuell Fehler: %s", exc)
        return None


def get_current_fear_greed() -> dict | None:
    """Letzten gespeicherten Fear & Greed Wert aus DB lesen."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT value, label, timestamp FROM fear_greed ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    if row:
        return {"value": row[0], "label": row[1], "timestamp": row[2]}
    return None


# ── Funding Rates ─────────────────────────────────────────────────────────────
# Positiv = Markt ist long gehebelt (long bezahlt short)
#   → Viele spekulieren auf steigende Preise → Korrektur-Risiko steigt
# Negativ = Markt ist short gehebelt
#   → Short-Squeeze möglich → Preis kann schnell steigen
# Neutral (~0) = ausgewogener Markt

# Binance-Symbol-Mapping (Futures, USDT-pairs)
FUNDING_SYMBOLS: dict[str, str] = {
    "BTCUSDT":  "BTC/EUR",
    "ETHUSDT":  "ETH/EUR",
    "SOLUSDT":  "SOL/EUR",
    "XRPUSDT":  "XRP/EUR",
    "ADAUSDT":  "ADA/EUR",
    "DOGEUSDT": "DOGE/EUR",
    "NEARUSDT": "NEAR/EUR",
    "LTCUSDT":  "LTC/EUR",
    "XLMUSDT":  "XLM/EUR",
    "ALGOUSDT": "ALGO/EUR",
    "INJUSDT":  "INJ/EUR",
    "HBARUSDT": "HBAR/EUR",
}


def fetch_funding_rates() -> int:
    """Aktuelle Funding Rates von Binance holen (kein API-Key nötig)."""
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        ts   = int(time.time() * 1000)
        rows = []
        for item in data:
            symbol = FUNDING_SYMBOLS.get(item.get("symbol", ""))
            if not symbol:
                continue
            rate = float(item.get("lastFundingRate", 0))
            rows.append((symbol, ts, rate))

        with get_connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO funding_rates VALUES (?,?,?)", rows
            )
            conn.commit()
        log.info("Funding Rates: %d Symbole gespeichert", len(rows))
        return len(rows)
    except Exception as exc:
        log.warning("Funding Rates Fehler: %s", exc)
        return 0


def fetch_funding_history(symbol_binance: str, our_symbol: str, limit: int = 500) -> int:
    """Historische Funding Rates für ein Symbol laden."""
    try:
        r = requests.get(
            f"https://fapi.binance.com/fapi/v1/fundingRate"
            f"?symbol={symbol_binance}&limit={limit}",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        rows = [
            (our_symbol, int(d["fundingTime"]), float(d["fundingRate"]))
            for d in data
        ]
        with get_connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO funding_rates VALUES (?,?,?)", rows
            )
            conn.commit()
        return len(rows)
    except Exception as exc:
        log.warning("Funding History Fehler (%s): %s", symbol_binance, exc)
        return 0


def get_latest_funding_rates() -> dict[str, float]:
    """Letzten Funding Rate je Symbol aus DB lesen."""
    with sqlite3.connect(DB_PATH) as conn:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='funding_rates'"
        ).fetchone()
        if not has:
            return {}
        rows = conn.execute(
            """SELECT f.symbol, f.rate FROM funding_rates f
               INNER JOIN (
                   SELECT symbol, MAX(timestamp) AS max_ts
                   FROM funding_rates GROUP BY symbol
               ) l ON f.symbol = l.symbol AND f.timestamp = l.max_ts"""
        ).fetchall()
    return {r[0]: r[1] for r in rows}


# ── Hauptfunktion ──────────────────────────────────────────────────────────────


def import_all_history() -> None:
    """Historische Daten einmalig importieren."""
    init_extra_tables()

    log.info("Importiere Fear & Greed History (365 Tage) …")
    fetch_fear_greed_history(days=365)

    log.info("Importiere Funding Rate History …")
    total = 0
    for binance_sym, our_sym in FUNDING_SYMBOLS.items():
        n = fetch_funding_history(binance_sym, our_sym, limit=500)
        log.info("  %s: %d Einträge", our_sym, n)
        total += n
        time.sleep(0.3)
    log.info("Funding History: %d Einträge total", total)


# ── Google Trends ─────────────────────────────────────────────────────────────

def fetch_google_trends() -> dict[str, float]:
    """Google Trends für Crypto-Keywords holen (via pytrends)."""
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
        pt.build_payload(
            ["bitcoin", "ethereum", "crypto crash", "buy crypto"],
            timeframe="now 7-d",
            geo="",
        )
        df = pt.interest_over_time()
        if df.empty:
            return {}
        latest = df.iloc[-1]
        result = {
            "trends_bitcoin":     float(latest.get("bitcoin", 50)),
            "trends_ethereum":    float(latest.get("ethereum", 50)),
            "trends_crypto_crash": float(latest.get("crypto crash", 0)),
            "trends_buy_crypto":  float(latest.get("buy crypto", 50)),
        }
        log.info("Google Trends: BTC=%d ETH=%d Crash=%d Buy=%d",
                 result["trends_bitcoin"], result["trends_ethereum"],
                 result["trends_crypto_crash"], result["trends_buy_crypto"])
        return result
    except Exception as exc:
        log.warning("Google Trends Fehler: %s", exc)
        return {}


def run_extra_features() -> None:
    """Fear & Greed + Funding Rates + Google Trends aktualisieren."""
    init_extra_tables()
    fetch_fear_greed_latest()
    fetch_funding_rates()
    fetch_google_trends()   # Trends werden von market_regime.py genutzt


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import_all_history()
    fg = get_current_fear_greed()
    if fg:
        print(f"\nAktueller Fear & Greed: {fg['value']} – {fg['label']}")
    rates = get_latest_funding_rates()
    print("\nAktuelle Funding Rates:")
    for sym, rate in sorted(rates.items(), key=lambda x: x[1], reverse=True):
        print(f"  {sym:<12} {rate*100:+.4f}%")
