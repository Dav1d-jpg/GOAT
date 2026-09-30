"""Assembler: alle Datenquellen zu einem ML-Datensatz zusammenführen.

Quellen:
  - features-Tabelle   (OHLCV-Indikatoren: RSI, MACD, BB, EMA, ATR, …)
  - news_sentiment      (Ollama-Sentiment-Scores als rollendes Zeitfenster)
  - whale_signals       (On-Chain-Daten, Volumen-Anomalien, Orderbook-Walls)
  - fear_greed          (Marktpsychologie-Index 0-100)
  - funding_rates       (Leverage-Sentiment von Binance Futures)

Target: steigt der Schlusskurs in LOOKAHEAD Kerzen? (1 = ja, 0 = nein)
"""
import logging
import sqlite3

import numpy as np
import pandas as pd

from db import DB_PATH
from extra_features import get_current_fear_greed, get_latest_funding_rates

log = logging.getLogger(__name__)

LOOKAHEAD  = 3    # Kerzen voraus für das Ziel
TIMEFRAME  = "1h"

# Target gebuehren-bewusst: ein Trade lohnt erst wenn der Kurs um mehr als die
# Round-Trip-Kosten steigt (2x 0.26% Kraken Taker + etwas Slippage).
# Ohne diese Schwelle lernt das Modell "steigt um irgendein Epsilon" – solche
# Signale sind nach Gebuehren systematisch verlustbringend.
MIN_TARGET_MOVE = 0.006

# Alle Feature-Spalten die das Modell bekommt
FEATURE_COLS: list[str] = [
    # Technische Indikatoren
    "rsi_14", "rsi_7",
    "macd", "macd_signal", "macd_hist",
    "bb_pct_b",
    "ema_9", "ema_21", "ema_50",
    "atr_14", "vol_ratio",
    "ret_1", "ret_3", "ret_6",
    # Hoehere-Zeitebenen-Trend + Saisonalitaet
    "mom_24", "mom_72",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "body_size", "wick_up", "wick_down",
    # Cross-Asset: was macht BTC gerade? (Altcoins folgen BTC)
    "btc_ret_1", "btc_ret_6", "rel_strength_6",
    # Sentiment
    "sent_score_24h", "sent_score_6h", "sent_pos_ratio",
    # Whale / On-Chain
    "whale_tx_24h", "whale_fee_usd", "whale_vol_anomaly",
    "whale_bid_wall", "whale_ask_wall",
    # Symbol-Identität (numerisch kodiert)
    "symbol_id",
    # Marktpsychologie
    "fear_greed",
    # Leverage-Sentiment
    "funding_rate",
    # Strategie-Features (0/1)
    "rsi_oversold", "rsi_overbought",
    "macd_bullish_cross", "macd_bearish_cross",
    "golden_cross", "death_cross",
    "bb_squeeze", "bb_breakout_up", "bb_breakout_down",
    "vol_breakout", "above_ema50", "above_ema21",
]


# ── Lade-Funktionen ───────────────────────────────────────────────────────────

def _get_symbols(conn: sqlite3.Connection, timeframe: str) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT symbol FROM features
           WHERE timeframe = ?
             AND symbol NOT IN ('USDC/EUR','USDT/EUR','EURC/EUR','PAXG/EUR')
           ORDER BY symbol""",
        (timeframe,),
    ).fetchall()
    return [r[0] for r in rows]


def _load_features(conn: sqlite3.Connection, symbol: str, timeframe: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT timestamp, open, high, low, close, volume,
                  rsi_14, rsi_7,
                  macd, macd_signal, macd_hist,
                  bb_pct_b,
                  ema_9, ema_21, ema_50,
                  atr_14, vol_ratio,
                  ret_1, ret_3, ret_6,
                  mom_24, mom_72,
                  hour_sin, hour_cos, dow_sin, dow_cos,
                  body_size, wick_up, wick_down,
                  rsi_oversold, rsi_overbought,
                  macd_bullish_cross, macd_bearish_cross,
                  golden_cross, death_cross,
                  bb_squeeze, bb_breakout_up, bb_breakout_down,
                  vol_breakout, above_ema50, above_ema21
           FROM features
           WHERE symbol = ? AND timeframe = ?
           ORDER BY timestamp""",
        conn,
        params=(symbol, timeframe),
    )


def _load_sentiment(conn: sqlite3.Connection) -> pd.DataFrame:
    try:
        return pd.read_sql_query(
            """SELECT COALESCE(published, fetched_at) AS ts, score, sentiment
               FROM news_sentiment ORDER BY ts""",
            conn,
        )
    except Exception:
        return pd.DataFrame(columns=["ts", "score", "sentiment"])


def _load_whale(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='whale_signals'"
        ).fetchone()
        if not has_table:
            return pd.DataFrame()
        return pd.read_sql_query(
            """SELECT timestamp AS ts, tx_count_24h, avg_fee_usd,
                      vol_anomaly, large_order_bid, large_order_ask
               FROM whale_signals WHERE coin = ? ORDER BY ts""",
            conn,
            params=(symbol,),
        )
    except Exception:
        return pd.DataFrame()


# ── Sentiment-Aggregation ─────────────────────────────────────────────────────

def _attach_sentiment(feat: pd.DataFrame, sent: pd.DataFrame) -> pd.DataFrame:
    """Für jeden Feature-Timestamp rollendes Sentiment berechnen."""
    if sent.empty:
        feat["sent_score_24h"] = np.nan
        feat["sent_score_6h"]  = np.nan
        feat["sent_pos_ratio"] = np.nan
        return feat

    ms_24h = 24 * 3600 * 1000
    ms_6h  =  6 * 3600 * 1000

    timestamps  = feat["timestamp"].values
    sent_ts     = sent["ts"].values
    sent_scores = sent["score"].values
    sent_pos    = (sent["sentiment"] == "positive").values.astype(float)

    s24, s6, pos = [], [], []
    for ts in timestamps:
        mask_24 = (sent_ts > ts - ms_24h) & (sent_ts <= ts)
        mask_6  = (sent_ts > ts - ms_6h)  & (sent_ts <= ts)
        s24.append(sent_scores[mask_24].mean() if mask_24.any() else 0.5)
        s6.append(sent_scores[mask_6].mean()   if mask_6.any()  else 0.5)
        pos.append(sent_pos[mask_24].mean()    if mask_24.any() else 0.5)

    feat["sent_score_24h"] = s24
    feat["sent_score_6h"]  = s6
    feat["sent_pos_ratio"] = pos
    return feat


# ── Whale-Features per Nearest-Merge ─────────────────────────────────────────

def _attach_whale(feat: pd.DataFrame, whale: pd.DataFrame) -> pd.DataFrame:
    """Letzten bekannten Whale-Eintrag je Feature-Timestamp zuordnen."""
    if whale.empty:
        for col in ["whale_tx_24h", "whale_fee_usd", "whale_vol_anomaly",
                    "whale_bid_wall", "whale_ask_wall"]:
            feat[col] = np.nan
        return feat

    whale = whale.rename(columns={
        "tx_count_24h":    "whale_tx_24h",
        "avg_fee_usd":     "whale_fee_usd",
        "vol_anomaly":     "whale_vol_anomaly",
        "large_order_bid": "whale_bid_wall",
        "large_order_ask": "whale_ask_wall",
    }).sort_values("ts")

    feat  = feat.sort_values("timestamp")
    merged = pd.merge_asof(
        feat.rename(columns={"timestamp": "ts"}),
        whale[["ts", "whale_tx_24h", "whale_fee_usd",
               "whale_vol_anomaly", "whale_bid_wall", "whale_ask_wall"]],
        on="ts",
        direction="backward",
    ).rename(columns={"ts": "timestamp"})
    return merged


# ── Datensatz aufbauen ────────────────────────────────────────────────────────

def build_dataset(timeframe: str = TIMEFRAME, lookahead: int = LOOKAHEAD) -> pd.DataFrame:
    """Vollständigen Feature-Datensatz für alle Symbole aufbauen.

    Gibt einen DataFrame zurück, der FEATURE_COLS + 'target' + 'symbol' enthält.
    """
    with sqlite3.connect(DB_PATH) as conn:
        symbols = _get_symbols(conn, timeframe)
        sent_df = _load_sentiment(conn)

        log.info("Baue Datensatz: %d Symbole | timeframe=%s | lookahead=%d",
                 len(symbols), timeframe, lookahead)

        all_dfs: list[pd.DataFrame] = []

        for sym_id, symbol in enumerate(symbols):
            feat = _load_features(conn, symbol, timeframe)
            if len(feat) < 60:
                log.warning("Überspringe %s – zu wenig Daten (%d)", symbol, len(feat))
                continue

            # ── Targets: bewegt sich der Preis in `lookahead` Kerzen um mehr als
            #    die Handelskosten? (gebuehren-bewusst, siehe MIN_TARGET_MOVE)
            #    target      = Long-Seite  (steigt um >0.6%)
            #    target_down = Short-Seite (faellt um >0.6%)
            future = feat["close"].shift(-lookahead)
            feat["target"]      = (future > feat["close"] * (1 + MIN_TARGET_MOVE)).astype("Int64")
            feat["target_down"] = (future < feat["close"] * (1 - MIN_TARGET_MOVE)).astype("Int64")
            # NaN > x ergibt False, nicht NaN – Zeilen ohne Zukunftskurs explizit
            # auf NA setzen, sonst landen falsche 0-Targets im Training
            feat.loc[future.isna(), "target"] = pd.NA
            feat.loc[future.isna(), "target_down"] = pd.NA

            # ── Sentiment anhängen
            feat = _attach_sentiment(feat, sent_df)

            # ── Whale-Features anhängen
            whale = _load_whale(conn, symbol)
            feat  = _attach_whale(feat, whale)

            feat["symbol"]    = symbol
            feat["symbol_id"] = sym_id
            all_dfs.append(feat)

    if not all_dfs:
        log.error("Kein Datensatz aufgebaut – zuerst features.py ausführen.")
        return pd.DataFrame()

    dataset = pd.concat(all_dfs, ignore_index=True)

    # ── Cross-Asset: BTC-Returns als globaler Feature (Altcoins folgen BTC)
    #    btc_ret_1/6 = BTC-Momentum; rel_strength_6 = Outperformance ggü. BTC
    try:
        with sqlite3.connect(DB_PATH) as conn:
            btc = pd.read_sql_query(
                """SELECT timestamp AS ts, close FROM features
                   WHERE symbol = 'BTC/EUR' AND timeframe = ? ORDER BY timestamp""",
                conn, params=(timeframe,),
            )
        if len(btc) > 6:
            btc["btc_ret_1"] = btc["close"].pct_change(1)
            btc["btc_ret_6"] = btc["close"].pct_change(6)
            btc = btc[["ts", "btc_ret_1", "btc_ret_6"]].dropna()
            dataset = dataset.sort_values("timestamp")
            dataset = pd.merge_asof(
                dataset.rename(columns={"timestamp": "ts"}),
                btc, on="ts", direction="backward",
            ).rename(columns={"ts": "timestamp"})
        else:
            dataset["btc_ret_1"] = np.nan
            dataset["btc_ret_6"] = np.nan
    except Exception:
        dataset["btc_ret_1"] = np.nan
        dataset["btc_ret_6"] = np.nan
    dataset["rel_strength_6"] = dataset["ret_6"] - dataset["btc_ret_6"]

    # ── Fear & Greed als globaler Feature (gleich für alle Symbole je Timestamp)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            fg_df = pd.read_sql_query(
                "SELECT timestamp AS ts, value AS fear_greed FROM fear_greed ORDER BY ts",
                conn,
            )
        if not fg_df.empty:
            dataset = dataset.sort_values("timestamp")
            fg_df   = fg_df.sort_values("ts")
            dataset = pd.merge_asof(
                dataset.rename(columns={"timestamp": "ts"}),
                fg_df,
                on="ts",
                direction="backward",
            ).rename(columns={"ts": "timestamp"})
            # Normalisieren auf 0-1
            dataset["fear_greed"] = dataset["fear_greed"] / 100.0
        else:
            dataset["fear_greed"] = np.nan
    except Exception:
        dataset["fear_greed"] = np.nan

    # ── Funding Rate je Symbol
    funding = get_latest_funding_rates()
    dataset["funding_rate"] = dataset["symbol"].map(funding)

    # Letzte `lookahead` Zeilen je Symbol haben kein valides Target
    dataset = dataset[dataset["target"].notna()].copy()
    dataset["target"]      = dataset["target"].astype(int)
    dataset["target_down"] = dataset["target_down"].astype(int)

    log.info("Datensatz fertig: %d Zeilen | %d Spalten | %.1f%% UP-Targets",
             len(dataset), len(dataset.columns), dataset["target"].mean() * 100)
    return dataset


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    df = build_dataset()
    if not df.empty:
        available = [c for c in FEATURE_COLS if c in df.columns]
        print(df[available + ["symbol", "target"]].tail(5).to_string())
        print(f"\nTarget-Verteilung:\n{df['target'].value_counts(normalize=True).round(3)}")
