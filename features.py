"""Feature Engineering: technische Indikatoren aus OHLCV-Daten berechnen."""
import logging
import sqlite3

import numpy as np
import pandas as pd

from db import DB_PATH, get_connection

log = logging.getLogger(__name__)


# ── Indikatoren ───────────────────────────────────────────────────────────────

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD-Linie, Signal-Linie und Histogramm."""
    ema_fast   = close.ewm(span=fast, adjust=False).mean()
    ema_slow   = close.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({
        "macd":        macd_line,
        "macd_signal": signal_line,
        "macd_hist":   macd_line - signal_line,
    })


def bollinger_bands(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: oberes Band, Mitte (SMA), unteres Band und %B."""
    sma   = close.rolling(period).mean()
    sigma = close.rolling(period).std()
    upper = sma + std * sigma
    lower = sma - std * sigma
    pct_b = (close - lower) / (upper - lower)
    return pd.DataFrame({
        "bb_upper":  upper,
        "bb_middle": sma,
        "bb_lower":  lower,
        "bb_pct_b":  pct_b,
    })


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return close.ewm(span=period, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range – misst Volatilitaet."""
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    """Volumen relativ zum gleitenden Durchschnitt."""
    return volume / volume.rolling(period).mean()


# ── Feature-Berechnung ────────────────────────────────────────────────────────

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Alle Features fuer einen OHLCV-DataFrame berechnen.

    Erwartet Spalten: timestamp, open, high, low, close, volume
    Gibt DataFrame mit allen Features zurueck (NaN-Zeilen am Anfang werden entfernt).
    """
    feat = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()

    feat["rsi_14"]    = rsi(df["close"], 14)
    feat["rsi_7"]     = rsi(df["close"], 7)

    macd_df           = macd(df["close"])
    feat              = pd.concat([feat, macd_df], axis=1)

    bb_df             = bollinger_bands(df["close"])
    feat              = pd.concat([feat, bb_df], axis=1)

    feat["ema_9"]     = ema(df["close"], 9)
    feat["ema_21"]    = ema(df["close"], 21)
    feat["ema_50"]    = ema(df["close"], 50)

    feat["atr_14"]    = atr(df["high"], df["low"], df["close"], 14)
    feat["vol_ratio"] = volume_sma(df["volume"], 20)

    # Preis-Momentum (Rendite ueber n Perioden)
    feat["ret_1"]     = df["close"].pct_change(1)
    feat["ret_3"]     = df["close"].pct_change(3)
    feat["ret_6"]     = df["close"].pct_change(6)

    # Hoehere-Zeitebenen-Trend: 1-Tag- und 3-Tage-Momentum (24h / 72h).
    # Gibt dem Modell den uebergeordneten Trend-Kontext, den es bei reinem
    # 1h-Blick nicht hat ("trade mit dem hoeheren Timeframe").
    feat["mom_24"]    = df["close"].pct_change(24)
    feat["mom_72"]    = df["close"].pct_change(72)

    # Zeit-Saisonalitaet (zyklisch kodiert): Krypto handelt 24/7, hat aber
    # Wochenend- und Tageszeit-Effekte. sin/cos haelt 23:00 und 00:00 nah beieinander.
    dt   = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    hour = dt.dt.hour.to_numpy()
    dow  = dt.dt.dayofweek.to_numpy()
    feat["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    feat["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    feat["dow_sin"]  = np.sin(2 * np.pi * dow / 7)
    feat["dow_cos"]  = np.cos(2 * np.pi * dow / 7)

    # Kerzen-Muster: Koerper- und Docht-Groesse normalisiert
    feat["body_size"] = (df["close"] - df["open"]).abs() / df["open"]
    feat["wick_up"]   = (df["high"] - df[["close", "open"]].max(axis=1)) / df["open"]
    feat["wick_down"] = (df[["close", "open"]].min(axis=1) - df["low"]) / df["open"]

    # ── Strategie-Features (0/1 Signale) ──────────────────────────────────────
    # RSI Extremzonen
    feat["rsi_oversold"]   = (feat["rsi_14"] < 30).astype(int)
    feat["rsi_overbought"] = (feat["rsi_14"] > 70).astype(int)

    # MACD Kreuzung (bullish wenn Histogramm von negativ auf positiv wechselt)
    feat["macd_bullish_cross"] = (
        (feat["macd_hist"] > 0) & (feat["macd_hist"].shift(1) <= 0)
    ).astype(int)
    feat["macd_bearish_cross"] = (
        (feat["macd_hist"] < 0) & (feat["macd_hist"].shift(1) >= 0)
    ).astype(int)

    # Golden Cross / Death Cross (EMA50 vs EMA21 als schnellere Variante)
    ema_200 = ema(df["close"], 200)
    feat["golden_cross"] = (feat["ema_50"] > ema_200).astype(int)
    feat["death_cross"]  = (feat["ema_50"] < ema_200).astype(int)

    # Bollinger Band Squeeze (enge Bänder = Ausbruch kommt)
    bb_width = (feat["bb_upper"] - feat["bb_lower"]) / feat["bb_middle"]
    feat["bb_squeeze"]      = (bb_width < bb_width.rolling(20).quantile(0.2)).astype(int)
    feat["bb_breakout_up"]  = (df["close"] > feat["bb_upper"]).astype(int)
    feat["bb_breakout_down"]= (df["close"] < feat["bb_lower"]).astype(int)

    # Volumen-Ausbruch (Whale-Aktivität oder News-Reaktion)
    feat["vol_breakout"] = (
        (feat["vol_ratio"] > 2.0) & (df["close"].pct_change(1).abs() > 0.005)
    ).astype(int)

    # Preis über/unter wichtigen EMAs (Trend-Kontext)
    feat["above_ema50"]  = (df["close"] > feat["ema_50"]).astype(int)
    feat["above_ema21"]  = (df["close"] > feat["ema_21"]).astype(int)

    return feat.dropna()


# ── Laden + Speichern ─────────────────────────────────────────────────────────

def load_ohlcv(symbol: str, timeframe: str = "1h") -> pd.DataFrame:
    """OHLCV-Daten fuer ein Symbol aus der DB laden."""
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM ohlcv WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp
            """,
            conn,
            params=(symbol, timeframe),
        )
    return df


def get_symbols() -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM ohlcv WHERE timeframe='1h'"
        ).fetchall()
    return [r[0] for r in rows]


def compute_and_store_all(timeframe: str = "1h") -> None:
    """Features fuer alle Symbole berechnen und in DB speichern."""
    symbols = get_symbols()
    log.info("Berechne Features fuer %d Symbole (%s)…", len(symbols), timeframe)

    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS features (
                symbol    TEXT    NOT NULL,
                timeframe TEXT    NOT NULL,
                timestamp INTEGER NOT NULL,
                open      REAL, high REAL, low REAL, close REAL, volume REAL,
                rsi_14 REAL, rsi_7 REAL,
                macd REAL, macd_signal REAL, macd_hist REAL,
                bb_upper REAL, bb_middle REAL, bb_lower REAL, bb_pct_b REAL,
                ema_9 REAL, ema_21 REAL, ema_50 REAL,
                atr_14 REAL, vol_ratio REAL,
                ret_1 REAL, ret_3 REAL, ret_6 REAL,
                mom_24 REAL, mom_72 REAL,
                hour_sin REAL, hour_cos REAL, dow_sin REAL, dow_cos REAL,
                body_size REAL, wick_up REAL, wick_down REAL,
                rsi_oversold INTEGER, rsi_overbought INTEGER,
                macd_bullish_cross INTEGER, macd_bearish_cross INTEGER,
                golden_cross INTEGER, death_cross INTEGER,
                bb_squeeze INTEGER, bb_breakout_up INTEGER, bb_breakout_down INTEGER,
                vol_breakout INTEGER, above_ema50 INTEGER, above_ema21 INTEGER,
                PRIMARY KEY (symbol, timeframe, timestamp)
            )
        """)
        # Migration: neue Spalten in bestehende Tabellen nachziehen (SQLite kennt
        # kein "ADD COLUMN IF NOT EXISTS", daher gegen vorhandene Spalten pruefen)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(features)")}
        for col in ("mom_24", "mom_72", "hour_sin", "hour_cos", "dow_sin", "dow_cos"):
            if col not in existing:
                conn.execute(f"ALTER TABLE features ADD COLUMN {col} REAL")
        conn.commit()

    for symbol in symbols:
        try:
            df   = load_ohlcv(symbol, timeframe)
            if len(df) < 60:
                log.warning("Zu wenig Daten fuer %s (%d Zeilen)", symbol, len(df))
                continue
            feat = compute_features(df)
            feat["symbol"]    = symbol
            feat["timeframe"] = timeframe

            with get_connection() as conn:
                cols = [
                    "symbol", "timeframe", "timestamp",
                    "open", "high", "low", "close", "volume",
                    "rsi_14", "rsi_7",
                    "macd", "macd_signal", "macd_hist",
                    "bb_upper", "bb_middle", "bb_lower", "bb_pct_b",
                    "ema_9", "ema_21", "ema_50",
                    "atr_14", "vol_ratio",
                    "ret_1", "ret_3", "ret_6",
                    "mom_24", "mom_72",
                    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
                    "body_size", "wick_up", "wick_down",
                    "rsi_oversold", "rsi_overbought",
                    "macd_bullish_cross", "macd_bearish_cross",
                    "golden_cross", "death_cross",
                    "bb_squeeze", "bb_breakout_up", "bb_breakout_down",
                    "vol_breakout", "above_ema50", "above_ema21",
                ]
                placeholders = ",".join(["?"] * len(cols))
                conn.executemany(
                    f"INSERT OR REPLACE INTO features ({','.join(cols)}) VALUES ({placeholders})",
                    feat[cols].values.tolist(),
                )
                conn.commit()
            log.info("Features gespeichert: %s (%d Zeilen)", symbol, len(feat))
        except Exception as exc:
            log.error("Fehler bei %s: %s", symbol, exc)

    log.info("Feature Engineering abgeschlossen.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    compute_and_store_all("1h")
    compute_and_store_all("5m")
