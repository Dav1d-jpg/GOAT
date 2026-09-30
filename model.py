"""ML-Modell: Training, Walk-Forward-Validierung und Signal-Generierung.

Architektur:
  - Random Forest Classifier (Haupt-Modell, interpretierbar)
  - Walk-Forward-Validierung (kein Overfitting durch Zukunftsdaten)
  - Signale werden in der DB gespeichert und im Dashboard angezeigt

DON'T: Das Modell trifft KEINE Trading-Entscheidungen.
       Es liefert nur Wahrscheinlichkeiten → Strategie-Engine entscheidet.
"""
import logging
import pickle
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, classification_report
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from db import DB_PATH, get_connection
from extra_features import get_current_fear_greed, get_latest_funding_rates
from model_data import FEATURE_COLS, LOOKAHEAD, build_dataset

log = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "models"
MODEL_DIR.mkdir(exist_ok=True)


# ── Walk-Forward-Validierung ──────────────────────────────────────────────────

def walk_forward_validation(
    X: np.ndarray,
    y: np.ndarray,
    train_size: int = 200,
    test_size:  int = 50,
    max_folds:  int = 30,
) -> dict:
    """Rollierende Train/Test-Fenstermethode – verhindert Look-Ahead-Bias.

    Prinzip:
        [===Train===][Test]
              [===Train===][Test]
                    [===Train===][Test]

    max_folds begrenzt die Anzahl der Folds bei großen Datensätzen.
    """
    n       = len(X)
    results = []
    start   = train_size

    if start + test_size > n:
        log.warning("Zu wenig Daten für Walk-Forward (%d Zeilen). Mindestens %d benötigt.",
                    n, train_size + test_size)
        return {"folds": 0, "accuracy": 0.0, "std": 0.0, "per_fold": []}

    # Bei großen Datensätzen test_size erhöhen damit max_folds nicht überschritten wird
    total_possible = (n - train_size) // test_size
    if total_possible > max_folds:
        test_size = (n - train_size) // max_folds
        log.info("Datensatz groß (%d Zeilen) – test_size auf %d angepasst für max %d Folds",
                 n, test_size, max_folds)

    while start + test_size <= n:
        X_train = X[start - train_size : start]
        y_train = y[start - train_size : start]
        X_test  = X[start : start + test_size]
        y_test  = y[start : start + test_size]

        clf = _build_pipeline(scale_pos_weight=_pos_weight(y_train))
        clf.fit(X_train, y_train)
        acc = accuracy_score(y_test, clf.predict(X_test))
        results.append(acc)
        start += test_size
        if len(results) >= max_folds:
            break

    return {
        "folds":    len(results),
        "accuracy": float(np.mean(results)),
        "std":      float(np.std(results)),
        "per_fold": results,
    }


# ── Modell-Pipeline ───────────────────────────────────────────────────────────

def _pos_weight(y: np.ndarray) -> float:
    """scale_pos_weight = Negativ/Positiv-Verhaeltnis – haelt prob_up bei
    unbalanciertem Target (z.B. gebuehren-bereinigtes Ziel) um 0.5 zentriert."""
    pos = float(np.mean(y))
    if pos <= 0.0 or pos >= 1.0:
        return 1.0
    return (1.0 - pos) / pos


def _build_pipeline(n_estimators: int = 200, scale_pos_weight: float = 1.0) -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("clf",     XGBClassifier(
            n_estimators      = n_estimators,
            max_depth         = 6,
            learning_rate     = 0.05,
            subsample         = 0.8,
            colsample_bytree  = 0.8,
            scale_pos_weight  = scale_pos_weight,
            eval_metric       = "logloss",
            random_state      = 42,
            n_jobs            = -1,
            verbosity         = 0,
        )),
    ])


# ── Persistenz ────────────────────────────────────────────────────────────────

def save_model(
    model: Pipeline,
    symbols: list[str],
    name: str = "main",
    symbol_ids: dict[str, int] | None = None,
    features: list[str] | None = None,
    model_down: Pipeline | None = None,
) -> Path:
    path = MODEL_DIR / f"{name}.pkl"
    with open(path, "wb") as f:
        pickle.dump({
            "model":      model,
            # Zweites Modell fuer die Short-Seite: P(Kurs faellt >0.6% in 3h).
            # P(up) niedrig heisst NICHT P(down) hoch – Seitwaertsbewegung!
            "model_down": model_down,
            "symbols":    symbols,
            "features":   features or FEATURE_COLS,
            # Exaktes Training-Mapping symbol -> symbol_id. Ohne dieses Mapping
            # bekam jede Live-Vorhersage eine FALSCHE symbol_id (Reihenfolge
            # im Training alphabetisch, bei Inferenz nach Zeitstempel).
            "symbol_ids": symbol_ids or {s: i for i, s in enumerate(symbols)},
        }, f)
    log.info("Modell gespeichert: %s", path)
    return path


def load_model(name: str = "main") -> dict | None:
    path = MODEL_DIR / f"{name}.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


# ── Signal-Generierung ────────────────────────────────────────────────────────

MAX_FEATURE_AGE_MS = 3 * 3600 * 1000  # keine Signale aus eingefrorenen Daten


def generate_signals(model_dict: dict) -> list[dict]:
    """Für jedes Symbol den neuesten Feature-Vektor laden und Signal berechnen."""
    model:    Pipeline   = model_dict["model"]
    model_down           = model_dict.get("model_down")  # None bei alten Modellen
    symbols:  list[str]  = model_dict["symbols"]
    features: list[str]  = model_dict["features"]
    # Fallback fuer alte Modelle ohne gespeichertes Mapping
    symbol_ids: dict[str, int] = model_dict.get("symbol_ids") or {
        s: i for i, s in enumerate(symbols)
    }

    now_ms  = int(time.time() * 1000)
    signals = []
    with sqlite3.connect(DB_PATH) as conn:
        for symbol in symbols:
            row = _latest_feature_row(conn, symbol)
            if row is None:
                continue
            if now_ms - int(row["timestamp"]) > MAX_FEATURE_AGE_MS:
                log.debug("Signal uebersprungen (Daten veraltet): %s", symbol)
                continue

            sym_id   = symbol_ids.get(symbol)
            if sym_id is None:
                continue
            feat_vec = _build_feature_vector(conn, row, sym_id, features, symbol)
            X = np.array(feat_vec, dtype=float).reshape(1, -1)

            proba  = model.predict_proba(X)[0]
            pred   = int(model.predict(X)[0])
            prob_up = float(proba[1]) if len(proba) > 1 else 0.5

            prob_down = None
            if model_down is not None:
                proba_d   = model_down.predict_proba(X)[0]
                prob_down = float(proba_d[1]) if len(proba_d) > 1 else 0.5

            signals.append({
                "symbol":     symbol,
                "signal":     "BUY"  if pred == 1 else "SELL",
                "confidence": float(max(proba)),
                "prob_up":    prob_up,
                "prob_down":  prob_down,
                "close":      float(row["close"]),
                "timestamp":  int(row["timestamp"]),
            })

    return sorted(signals, key=lambda s: s["prob_up"], reverse=True)


def _latest_feature_row(conn: sqlite3.Connection, symbol: str) -> dict | None:
    row = conn.execute(
        """SELECT timestamp, close,
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
           WHERE symbol = ? AND timeframe = '1h'
           ORDER BY timestamp DESC LIMIT 1""",
        (symbol,),
    ).fetchone()
    if not row:
        return None
    cols = ["timestamp","close","rsi_14","rsi_7","macd","macd_signal","macd_hist",
            "bb_pct_b","ema_9","ema_21","ema_50","atr_14","vol_ratio",
            "ret_1","ret_3","ret_6",
            "mom_24","mom_72","hour_sin","hour_cos","dow_sin","dow_cos",
            "body_size","wick_up","wick_down",
            "rsi_oversold","rsi_overbought",
            "macd_bullish_cross","macd_bearish_cross",
            "golden_cross","death_cross",
            "bb_squeeze","bb_breakout_up","bb_breakout_down",
            "vol_breakout","above_ema50","above_ema21"]
    return dict(zip(cols, row))


def _btc_context(conn: sqlite3.Connection) -> tuple[float, float]:
    """BTC-Momentum (1h / 6h) fuer den aktuellen Zeitpunkt – Cross-Asset-Feature.

    MUSS dieselbe Definition wie build_dataset nutzen (sonst Train/Inferenz-Drift).
    """
    rows = conn.execute(
        """SELECT close FROM features
           WHERE symbol = 'BTC/EUR' AND timeframe = '1h'
           ORDER BY timestamp DESC LIMIT 7"""
    ).fetchall()
    closes = [float(r[0]) for r in rows]   # neueste zuerst
    btc_ret_1 = (closes[0] / closes[1] - 1) if len(closes) >= 2 and closes[1] else np.nan
    btc_ret_6 = (closes[0] / closes[6] - 1) if len(closes) >= 7 and closes[6] else np.nan
    return btc_ret_1, btc_ret_6


def _build_feature_vector(
    conn: sqlite3.Connection, row: dict, sym_id: int, features: list[str], symbol: str = ""
) -> list[float]:
    """Feature-Vektor inkl. Sentiment + Whale für aktuellen Zeitpunkt."""
    ts = row["timestamp"]

    # Sentiment (letzte 24h / 6h)
    sent = conn.execute(
        """SELECT
               AVG(score)                                              AS s24,
               AVG(CASE WHEN fetched_at > ? - 21600000 THEN score END) AS s6,
               AVG(CASE WHEN sentiment='positive' THEN 1.0 ELSE 0.0 END) AS pos
           FROM news_sentiment
           WHERE fetched_at > ? - 86400000""",
        (ts, ts),
    ).fetchone()
    sent_24h = sent[0] or 0.5
    sent_6h  = sent[1] or 0.5
    sent_pos = sent[2] or 0.5

    # Whale (letzter Eintrag)
    has_whale = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='whale_signals'"
    ).fetchone()
    whale_vals = [np.nan, np.nan, np.nan, np.nan, np.nan]
    if has_whale:
        w = conn.execute(
            """SELECT tx_count_24h, avg_fee_usd, vol_anomaly,
                      large_order_bid, large_order_ask
               FROM whale_signals WHERE coin = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (row.get("symbol", symbol),),
        ).fetchone()
        if w:
            whale_vals = list(w)

    # Fear & Greed (normalisiert 0-1)
    fg = get_current_fear_greed()
    fear_greed_val = fg["value"] / 100.0 if fg else np.nan

    # Funding Rate für dieses Symbol
    funding_rates = get_latest_funding_rates()
    funding_val = funding_rates.get(symbol, np.nan)

    # Cross-Asset: BTC-Momentum + relative Staerke dieses Symbols ggü. BTC
    btc_ret_1, btc_ret_6 = _btc_context(conn)
    sym_ret_6 = row.get("ret_6")
    rel_strength_6 = (sym_ret_6 - btc_ret_6) if (sym_ret_6 is not None
                     and not np.isnan(btc_ret_6)) else np.nan

    # Mapping: Feature-Name → Wert
    value_map = {
        "rsi_14": row["rsi_14"],       "rsi_7": row["rsi_7"],
        "macd":   row["macd"],          "macd_signal": row["macd_signal"],
        "macd_hist": row["macd_hist"],  "bb_pct_b": row["bb_pct_b"],
        "ema_9":  row["ema_9"],         "ema_21": row["ema_21"],
        "ema_50": row["ema_50"],        "atr_14": row["atr_14"],
        "vol_ratio": row["vol_ratio"],
        "ret_1":  row["ret_1"],         "ret_3": row["ret_3"],
        "ret_6":  row["ret_6"],
        "mom_24": row.get("mom_24", np.nan), "mom_72": row.get("mom_72", np.nan),
        "hour_sin": row.get("hour_sin", np.nan), "hour_cos": row.get("hour_cos", np.nan),
        "dow_sin":  row.get("dow_sin", np.nan),  "dow_cos":  row.get("dow_cos", np.nan),
        "btc_ret_1": btc_ret_1, "btc_ret_6": btc_ret_6,
        "rel_strength_6": rel_strength_6,
        "body_size": row["body_size"],  "wick_up": row["wick_up"],
        "wick_down": row["wick_down"],
        "sent_score_24h": sent_24h,     "sent_score_6h": sent_6h,
        "sent_pos_ratio": sent_pos,
        "whale_tx_24h":     whale_vals[0],
        "whale_fee_usd":    whale_vals[1],
        "whale_vol_anomaly": whale_vals[2],
        "whale_bid_wall":   whale_vals[3],
        "whale_ask_wall":   whale_vals[4],
        "symbol_id":        sym_id,
        "fear_greed":       fear_greed_val,
        "funding_rate":     funding_val,
        "rsi_oversold":     row.get("rsi_oversold", np.nan),
        "rsi_overbought":   row.get("rsi_overbought", np.nan),
        "macd_bullish_cross": row.get("macd_bullish_cross", np.nan),
        "macd_bearish_cross": row.get("macd_bearish_cross", np.nan),
        "golden_cross":     row.get("golden_cross", np.nan),
        "death_cross":      row.get("death_cross", np.nan),
        "bb_squeeze":       row.get("bb_squeeze", np.nan),
        "bb_breakout_up":   row.get("bb_breakout_up", np.nan),
        "bb_breakout_down": row.get("bb_breakout_down", np.nan),
        "vol_breakout":     row.get("vol_breakout", np.nan),
        "above_ema50":      row.get("above_ema50", np.nan),
        "above_ema21":      row.get("above_ema21", np.nan),
    }
    return [value_map.get(f, np.nan) for f in features]


# ── Signale speichern ─────────────────────────────────────────────────────────

def store_signals(signals: list[dict]) -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                symbol     TEXT    NOT NULL,
                timestamp  INTEGER NOT NULL,
                signal     TEXT,
                confidence REAL,
                prob_up    REAL,
                close      REAL,
                PRIMARY KEY (symbol, timestamp)
            )
        """)
        # prob_down nachtraeglich ergaenzen (Bestands-DBs haben die Spalte nicht)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(signals)")]
        if "prob_down" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN prob_down REAL")
        conn.executemany(
            """INSERT OR REPLACE INTO signals
               (symbol, timestamp, signal, confidence, prob_up, prob_down, close)
               VALUES (:symbol, :timestamp, :signal, :confidence, :prob_up, :prob_down, :close)""",
            signals,
        )
        conn.commit()


# ── Haupt-Training ────────────────────────────────────────────────────────────

def train_and_evaluate() -> None:
    """Vollständige Trainings-Pipeline mit Walk-Forward-Validierung."""
    log.info("=" * 60)
    log.info("GOAT ML-Modell – Training gestartet")
    log.info("=" * 60)

    # 1. Datensatz laden
    df = build_dataset()
    if df.empty or len(df) < 150:
        log.error("Zu wenig Daten (%d Zeilen). Mindestens 150 benötigt. "
                  "Mehr Daten sammeln und erneut versuchen.", len(df) if not df.empty else 0)
        return

    available = [c for c in FEATURE_COLS if c in df.columns]
    missing   = [c for c in FEATURE_COLS if c not in df.columns]
    if missing:
        log.warning("Fehlende Feature-Spalten (übersprungen): %s", missing)

    df_clean = df.dropna(subset=["target"])

    # Spalten entfernen die ausschließlich NaN enthalten (z.B. Whale-Features ohne Daten)
    all_nan = [c for c in available if df_clean[c].isna().all()]
    if all_nan:
        log.warning("Spalten komplett NaN – werden weggelassen (sammle mehr Daten): %s", all_nan)
        available = [c for c in available if c not in all_nan]

    X          = df_clean[available].values.astype(float)
    y          = df_clean["target"].values.astype(int)
    symbols    = df_clean["symbol"].unique().tolist()
    # Exaktes Mapping aus dem Trainingsdatensatz – MUSS bei Inferenz identisch sein
    symbol_ids = (
        df_clean[["symbol", "symbol_id"]]
        .drop_duplicates()
        .set_index("symbol")["symbol_id"]
        .astype(int)
        .to_dict()
    )

    log.info("Datensatz: %d Zeilen | %d Features | %d Symbole",
             len(X), len(available), len(symbols))
    log.info("Target: %.1f%% UP  |  %.1f%% DOWN",
             y.mean() * 100, (1 - y.mean()) * 100)

    # 2. Walk-Forward-Validierung
    # Trainingsfenster muss gross genug sein um das finale Modell zu
    # repraesentieren (300 Zeilen Training gegen 20k Zeilen Test waren wertlos)
    wf_train = min(100_000, len(X) // 2)
    log.info("-" * 40)
    log.info("Walk-Forward-Validierung (train=%d) …", wf_train)
    wf = walk_forward_validation(
        X, y,
        train_size = wf_train,
        test_size  = 50,
    )
    if wf["folds"] > 0:
        log.info("Ergebnis: %.1f%% Genauigkeit (±%.1f%%) über %d Folds",
                 wf["accuracy"] * 100, wf["std"] * 100, wf["folds"])
        for i, acc in enumerate(wf["per_fold"]):
            log.info("  Fold %2d: %.1f%%", i + 1, acc * 100)
    else:
        log.warning("Walk-Forward nicht möglich – zu wenig Daten.")

    # 3. Finale Modelle auf allen Daten (Long- und Short-Seite)
    log.info("-" * 40)
    log.info("Trainiere finales Long-Modell auf allen %d Zeilen …", len(X))
    model = _build_pipeline(n_estimators=300, scale_pos_weight=_pos_weight(y))
    model.fit(X, y)

    y_down = df_clean["target_down"].values.astype(int)
    log.info("Trainiere Short-Modell (P(faellt >0.6%%), Basisrate %.1f%%) …",
             y_down.mean() * 100)
    model_down = _build_pipeline(n_estimators=300, scale_pos_weight=_pos_weight(y_down))
    model_down.fit(X, y_down)

    # Train-Genauigkeit (Referenzwert)
    train_acc = accuracy_score(y, model.predict(X))
    log.info("Train-Genauigkeit: %.1f%% (Overfitting-Check: sollte >> Walk-Forward sein)",
             train_acc * 100)

    # Feature Importance
    log.info("-" * 40)
    log.info("Feature Importance (Top 12):")
    rf         = model.named_steps["clf"]
    importance = pd.Series(rf.feature_importances_, index=available).sort_values(ascending=False)
    for feat, imp in importance.head(12).items():
        bar = "█" * int(imp * 200)
        log.info("  %-25s %.4f  %s", feat, imp, bar)

    # 4. Modell speichern
    save_model(model, symbols, name="main", symbol_ids=symbol_ids,
               features=available, model_down=model_down)

    # 5. Aktuelle Signale generieren
    log.info("-" * 40)
    log.info("Generiere aktuelle Signale …")
    model_dict = {"model": model, "model_down": model_down, "symbols": symbols,
                  "features": available, "symbol_ids": symbol_ids}
    signals    = generate_signals(model_dict)
    store_signals(signals)

    log.info("=" * 60)
    log.info("AKTUELLE SIGNALE")
    log.info("=" * 60)
    for s in signals:
        bar   = "█" * int(s["prob_up"] * 20)
        arrow = "▲" if s["signal"] == "BUY" else "▼"
        log.info("  %s %-12s  %4s  P(up)=%.0f%%  Conf=%.0f%%  Kurs=%.4f",
                 arrow, s["symbol"], s["signal"],
                 s["prob_up"] * 100, s["confidence"] * 100, s["close"])

    log.info("=" * 60)
    log.info("Fertig. Modell: models/main.pkl")


def refresh_signals() -> list[dict]:
    """Nur Signale neu berechnen (kein Re-Training). Für den Scheduler."""
    model_dict = load_model("main")
    if model_dict is None:
        log.warning("Kein trainiertes Modell gefunden. Zuerst model.py ausführen.")
        return []
    signals = generate_signals(model_dict)
    store_signals(signals)
    log.info("Signale aktualisiert: %d Symbole", len(signals))
    return signals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    train_and_evaluate()
