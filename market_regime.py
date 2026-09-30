"""Market Regime Detection: erkennt ob der Markt im Bull, Bear oder Seitwärts ist.

Signale:
  - EMA50 vs EMA200 (Golden/Death Cross)
  - Fear & Greed Index
  - BTC-Dominanz (CoinGecko)
  - Volatilität (ATR-basiert)

Output: 'BULL', 'BEAR', 'SIDEWAYS' + Konfidenz 0-1
"""
import logging
import sqlite3
import time

import requests

from db import DB_PATH, get_connection

log = logging.getLogger(__name__)


def init_regime_table() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_regime (
                timestamp      INTEGER PRIMARY KEY,
                regime         TEXT    NOT NULL,
                confidence     REAL    NOT NULL,
                btc_dominance  REAL,
                fear_greed     INTEGER,
                ema_trend      TEXT,
                volatility     TEXT
            )
        """)
        conn.commit()


def fetch_btc_dominance() -> float | None:
    """BTC-Dominanz von CoinGecko holen (kostenlos)."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=15,
            headers={"User-Agent": "GOAT-TradingBot/1.0"},
        )
        r.raise_for_status()
        dominance = r.json()["data"]["market_cap_percentage"].get("btc", 50.0)
        log.info("BTC-Dominanz: %.1f%%", dominance)
        return float(dominance)
    except Exception as exc:
        log.warning("BTC-Dominanz Fehler: %s", exc)
        return None


def get_btc_ema_trend() -> str:
    """EMA50 vs EMA200 für BTC/EUR aus der features-Tabelle."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """SELECT ema_50, golden_cross, death_cross FROM features
               WHERE symbol = 'BTC/EUR' AND timeframe = '1h'
               ORDER BY timestamp DESC LIMIT 1"""
        ).fetchone()
    if not row:
        return "UNKNOWN"
    if row[1] == 1:
        return "BULLISH"
    if row[2] == 1:
        return "BEARISH"
    return "NEUTRAL"


def get_current_volatility() -> str:
    """Aktuelle BTC-Volatilität (ATR-basiert)."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT atr_14, close FROM features
               WHERE symbol = 'BTC/EUR' AND timeframe = '1h'
               ORDER BY timestamp DESC LIMIT 20"""
        ).fetchall()
    if not rows:
        return "UNKNOWN"
    avg_atr_pct = sum(r[0] / r[1] for r in rows if r[1] > 0) / len(rows) * 100
    if avg_atr_pct > 3.0:   return "HIGH"
    if avg_atr_pct > 1.5:   return "MEDIUM"
    return "LOW"


def detect_regime() -> dict:
    """Aktuelles Marktregime bestimmen und in DB speichern."""
    init_regime_table()

    ema_trend    = get_btc_ema_trend()
    volatility   = get_current_volatility()
    btc_dominance = fetch_btc_dominance()

    # Fear & Greed aus DB
    with sqlite3.connect(DB_PATH) as conn:
        fg = conn.execute(
            "SELECT value FROM fear_greed ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    fear_greed = fg[0] if fg else 50

    # Regime-Scoring
    bull_score = 0
    bear_score = 0

    # EMA Trend (stärkstes Signal)
    if ema_trend == "BULLISH":  bull_score += 3
    elif ema_trend == "BEARISH": bear_score += 3

    # Fear & Greed
    if fear_greed < 25:    bull_score += 2   # Extreme Fear = contrarian bullish
    elif fear_greed < 40:  bull_score += 1
    elif fear_greed > 75:  bear_score += 2   # Extreme Greed = contrarian bearish
    elif fear_greed > 60:  bear_score += 1

    # BTC Dominanz
    if btc_dominance:
        if btc_dominance > 55:  bear_score += 1   # Flucht in BTC = Altcoins schlecht
        elif btc_dominance < 45: bull_score += 1   # Altseason = breiter Bullrun

    # Volatilität
    if volatility == "HIGH":    bear_score += 1   # Hohe Vola = Unsicherheit
    elif volatility == "LOW":   bull_score += 1   # Niedrige Vola = stabiler Trend

    total = bull_score + bear_score
    if total == 0:
        regime, confidence = "SIDEWAYS", 0.5
    elif bull_score > bear_score:
        regime     = "BULL"
        confidence = bull_score / total
    elif bear_score > bull_score:
        regime     = "BEAR"
        confidence = bear_score / total
    else:
        regime, confidence = "SIDEWAYS", 0.5

    ts = int(time.time() * 1000)
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO market_regime
               (timestamp, regime, confidence, btc_dominance, fear_greed, ema_trend, volatility)
               VALUES (?,?,?,?,?,?,?)""",
            (ts, regime, confidence, btc_dominance, fear_greed, ema_trend, volatility),
        )
        conn.commit()

    log.info("Marktregime: %s (Konfidenz %.0f%%) | EMA: %s | F&G: %d | Dominanz: %.1f%% | Vola: %s",
             regime, confidence * 100, ema_trend, fear_greed,
             btc_dominance or 0, volatility)
    return {"regime": regime, "confidence": confidence,
            "ema_trend": ema_trend, "fear_greed": fear_greed}


def get_current_regime() -> dict:
    """Letztes gespeichertes Regime aus DB lesen."""
    with sqlite3.connect(DB_PATH) as conn:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_regime'"
        ).fetchone()
        if not has:
            return {"regime": "UNKNOWN", "confidence": 0.5}
        row = conn.execute(
            "SELECT regime, confidence, fear_greed, ema_trend FROM market_regime ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    if row:
        return {"regime": row[0], "confidence": row[1],
                "fear_greed": row[2], "ema_trend": row[3]}
    return {"regime": "UNKNOWN", "confidence": 0.5}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = detect_regime()
    print(f"\nAktuelles Regime: {result['regime']} ({result['confidence']*100:.0f}% Konfidenz)")
