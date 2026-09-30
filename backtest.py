"""Backtester: simuliert die exakten Trading-Regeln ueber die historischen Daten.

Methodik (ehrlich, kein Look-Ahead):
  - Chronologischer Split: Modelle werden NUR auf dem Train-Teil trainiert,
    gehandelt wird ausschliesslich auf dem Out-of-Sample-Test-Teil
  - Stops werden gegen High/Low der Stunden-Kerzen geprueft; treffen Stop-Loss
    und Take-Profit in derselben Kerze, zaehlt konservativ der Stop-Loss
  - Gebuehren wie live: 0.26% Taker je Seite
  - Gleiche Regeln wie paper_trader.py: Regime-Abstufung, Shorts im BEAR,
    Cooldowns, Max-Positionen, Positionsgroessen

Grenzen (bewusst dokumentiert):
  - Kein Intra-Stunden-Verlauf (1-Min-Loop), kein News-Panic, kein Whale-Signal
  - Regime vereinfacht: BTC EMA-Trend + Fear&Greed (ohne Dominanz/Volatilitaet)
  - Ein statisches Modell fuer den ganzen Test-Zeitraum (kein Rolling-Retrain)
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from model import _build_pipeline, _pos_weight
from model_data import FEATURE_COLS, build_dataset
from paper_trader import (
    BLACKLIST, MAX_POSITIONS, MAX_SHORTS, MIN_CONFIDENCE_BUY,
    POSITION_SIZE_PCT, SELL_THRESHOLD, SHORT_MIN_CONFIDENCE,
    SHORT_SIZE_PCT, SHORT_STOP_LOSS_PCT, SHORT_TAKE_PROFIT_PCT,
    SIDEWAYS_MIN_CONFIDENCE, SIDEWAYS_SIZE_PCT, STOP_LOSS_PCT,
    STOPLOSS_COOLDOWN_H, ML_LOSS_COOLDOWN_H, TAKE_PROFIT_PCT,
    TAKER_FEE, TRAILING_STOP_PCT,
)

log = logging.getLogger(__name__)

RESULT_PATH = Path(__file__).parent / "data" / "backtest_result.json"

MS_HOUR = 3600 * 1000


# ── Regime-Rekonstruktion ─────────────────────────────────────────────────────

def _build_regime_map(test: pd.DataFrame) -> dict[int, str]:
    """Marktregime je Timestamp aus BTC-EMA-Trend + Fear&Greed rekonstruieren.

    Vereinfachte Version von market_regime.detect_regime() – Dominanz und
    Volatilitaet fehlen historisch, EMA-Trend ist ohnehin das staerkste Signal.
    """
    btc = test[test["symbol"] == "BTC/EUR"]
    regime_map: dict[int, str] = {}
    for row in btc.itertuples():
        bull, bear = 0, 0
        if getattr(row, "golden_cross", 0) == 1:
            bull += 3
        if getattr(row, "death_cross", 0) == 1:
            bear += 3
        fg = getattr(row, "fear_greed", None)
        if fg is not None and not pd.isna(fg):
            fg100 = fg * 100
            if fg100 < 25:   bull += 2     # Extreme Fear = contrarian bullish
            elif fg100 < 40: bull += 1
            elif fg100 > 75: bear += 2
            elif fg100 > 60: bear += 1
        if bull > bear:   regime_map[row.timestamp] = "BULL"
        elif bear > bull: regime_map[row.timestamp] = "BEAR"
        else:             regime_map[row.timestamp] = "SIDEWAYS"
    return regime_map


# ── Haupt-Backtest ────────────────────────────────────────────────────────────

def run_backtest(
    train_frac:      float = 0.7,
    buy_threshold:   float = MIN_CONFIDENCE_BUY,
    short_threshold: float = SHORT_MIN_CONFIDENCE,
    start_capital:   float = 1000.0,
    enable_shorts:   bool  = True,
    stop_loss_pct:   float = STOP_LOSS_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    short_sl_pct:    float = SHORT_STOP_LOSS_PCT,
    short_tp_pct:    float = SHORT_TAKE_PROFIT_PCT,
    use_fixed_stop:  bool  = True,   # False = nur Trailing-Stop als Absicherung
    vol_target:      float | None = None,  # Ziel-ATR%/h fuer Vola-Sizing (z.B. 0.015)
    max_positions:   int   = MAX_POSITIONS,
    max_shorts:      int   = MAX_SHORTS,
    # ── Dynamische Bull-Exposure (nur Backtest-Experiment) ──
    bull_size_pct:      float | None = None,  # Positionsgroesse in BULL (sonst POSITION_SIZE_PCT)
    bull_max_positions: int | None   = None,  # max Longs in BULL (sonst max_positions)
    buys_per_cycle:     int          = 1,     # neue Longs pro Stunde (Auffuell-Tempo)
    short_regimes:      tuple        = ("BEAR",),  # in welchen Regimes Shorts erlaubt sind
    save:            bool  = True,
) -> dict:
    """Backtest ueber den Out-of-Sample-Teil der Historie. Gibt Ergebnis-Dict zurueck."""
    t0 = time.time()
    log.info("Backtest: lade Datensatz …")
    df = build_dataset()
    if df.empty:
        return {"error": "Kein Datensatz – zuerst Daten sammeln."}

    available = [c for c in FEATURE_COLS if c in df.columns]

    # Chronologischer Split auf Timestamp-Ebene
    ts_sorted = np.sort(df["timestamp"].unique())
    split_ts  = ts_sorted[int(len(ts_sorted) * train_frac)]
    train     = df[df["timestamp"] <  split_ts]
    test      = df[df["timestamp"] >= split_ts].copy()
    if len(train) < 1000 or len(test) < 500:
        return {"error": f"Zu wenig Daten fuer Split ({len(train)} Train / {len(test)} Test)."}

    # Komplett leere Spalten im Train-Teil weglassen (wie model.py)
    all_nan   = [c for c in available if train[c].isna().all()]
    available = [c for c in available if c not in all_nan]

    log.info("Backtest: Train %d Zeilen | Test %d Zeilen | Split bei %s",
             len(train), len(test),
             datetime.fromtimestamp(split_ts / 1000).strftime("%Y-%m-%d"))

    # Modelle ausschliesslich auf dem Train-Teil fitten
    X_tr   = train[available].values.astype(float)
    y_up   = train["target"].values.astype(int)
    y_down = train["target_down"].values.astype(int)

    m_up = _build_pipeline(n_estimators=300, scale_pos_weight=_pos_weight(y_up))
    m_up.fit(X_tr, y_up)
    m_down = _build_pipeline(n_estimators=300, scale_pos_weight=_pos_weight(y_down))
    m_down.fit(X_tr, y_down)

    X_te = test[available].values.astype(float)
    test["prob_up"]   = m_up.predict_proba(X_te)[:, 1]
    test["prob_down"] = m_down.predict_proba(X_te)[:, 1]

    regime_map = _build_regime_map(test)

    # ── Event-Loop ueber die Stunden des Test-Zeitraums ──────────────────────
    cash     = start_capital
    longs:  dict[str, dict] = {}
    shorts: dict[str, dict] = {}
    cooldown_until: dict[str, int] = {}
    trades:  list[dict] = []
    equity:  list[tuple[int, float]] = []
    regime   = "SIDEWAYS"
    regime_hours = {"BULL": 0, "BEAR": 0, "SIDEWAYS": 0}

    def close_long(sym: str, price: float, reason: str, ts: int) -> None:
        nonlocal cash
        pos      = longs.pop(sym)
        proceeds = pos["amount"] * price * (1 - TAKER_FEE)
        pl       = proceeds - pos["amount"] * pos["entry"]
        cash    += proceeds
        if "STOP_LOSS" in reason or "TRAILING" in reason:
            cooldown_until[sym] = ts + STOPLOSS_COOLDOWN_H * MS_HOUR
        elif reason == "ML_SIGNAL" and pl < 0:
            cooldown_until[sym] = ts + ML_LOSS_COOLDOWN_H * MS_HOUR
        trades.append({"ts": ts, "symbol": sym, "side": "LONG", "reason": reason,
                       "entry": pos["entry"], "exit": price, "pl": pl,
                       "held_h": (ts - pos["opened"]) // MS_HOUR})

    def close_short(sym: str, price: float, reason: str, ts: int) -> None:
        nonlocal cash
        pos  = shorts.pop(sym)
        pl   = pos["amount"] * (pos["entry"] - price) - pos["amount"] * price * TAKER_FEE
        cash += pos["margin"] + pl
        if "STOP_LOSS" in reason or (reason.startswith("ML_SIGNAL") and pl < 0):
            cooldown_until[sym] = ts + STOPLOSS_COOLDOWN_H * MS_HOUR
        trades.append({"ts": ts, "symbol": sym, "side": "SHORT", "reason": reason,
                       "entry": pos["entry"], "exit": price, "pl": pl,
                       "held_h": (ts - pos["opened"]) // MS_HOUR})

    grouped = test.groupby("timestamp", sort=True)
    for ts, grp in grouped:
        ts = int(ts)
        rows   = {r.symbol: r for r in grp.itertuples()}
        regime = regime_map.get(ts, regime)
        regime_hours[regime] = regime_hours.get(regime, 0) + 1

        # 1) Long-Exits (High/Low-basiert, konservativ Stop zuerst)
        for sym in list(longs):
            r = rows.get(sym)
            if r is None:
                continue
            pos = longs[sym]
            if r.high > pos["highest"]:
                pos["highest"] = r.high
            sl    = pos["entry"] * (1 - stop_loss_pct)
            tp    = pos["entry"] * (1 + take_profit_pct)
            trail = pos["highest"] * (1 - TRAILING_STOP_PCT)
            if use_fixed_stop and r.low <= sl:
                close_long(sym, sl, "STOP_LOSS", ts)
            elif r.high >= tp:
                close_long(sym, tp, "TAKE_PROFIT", ts)
            elif r.low <= trail and trail > pos["entry"] * 1.01:
                close_long(sym, trail, "TRAILING_STOP", ts)
            elif r.prob_up < SELL_THRESHOLD:
                close_long(sym, r.close, "ML_SIGNAL", ts)

        # 2) Short-Exits
        for sym in list(shorts):
            r = rows.get(sym)
            if r is None:
                continue
            pos = shorts[sym]
            sl  = pos["entry"] * (1 + short_sl_pct)
            tp  = pos["entry"] * (1 - short_tp_pct)
            if r.high >= sl:
                close_short(sym, sl, "SHORT_STOP_LOSS", ts)
            elif r.low <= tp:
                close_short(sym, tp, "SHORT_TAKE_PROFIT", ts)
            elif r.prob_down < SELL_THRESHOLD:
                close_short(sym, r.close, "ML_SIGNAL_COVER", ts)

        # 3) Long-Entries (regime-abgestuft; in BULL optional aggressiver)
        if regime in ("BULL", "SIDEWAYS"):
            if regime == "BULL":
                thr     = buy_threshold
                size    = bull_size_pct if bull_size_pct is not None else POSITION_SIZE_PCT
                pos_cap = bull_max_positions if bull_max_positions is not None else max_positions
                per_cyc = buys_per_cycle
            else:
                thr     = SIDEWAYS_MIN_CONFIDENCE
                size    = SIDEWAYS_SIZE_PCT
                pos_cap = max_positions
                per_cyc = 1
            buys_done = 0
            for r in sorted(rows.values(), key=lambda x: x.prob_up, reverse=True):
                if r.prob_up < thr or buys_done >= per_cyc:
                    break
                sym = r.symbol
                if (len(longs) >= pos_cap or sym in longs or sym in shorts
                        or sym in BLACKLIST or cash < 50.0
                        or cooldown_until.get(sym, 0) > ts):
                    continue
                sized = size
                if vol_target and r.close:
                    atr_pct = (r.atr_14 / r.close) if r.atr_14 else None
                    if atr_pct and atr_pct > 0:
                        # invers zur Vola: hohe Vola -> kleinere Position
                        sized = size * max(0.4, min(1.5, vol_target / atr_pct))
                invest = cash * sized
                amount = (invest - invest * TAKER_FEE) / r.close
                cash  -= invest
                longs[sym] = {"amount": amount, "entry": r.close,
                              "highest": r.close, "opened": ts}
                buys_done += 1

        # 4) Short-Entries (Regimes per short_regimes konfigurierbar; eigenes
        #    'if', damit in BULL Longs UND Shorts moeglich sind je nach Modell-Sicht)
        if enable_shorts and regime in short_regimes:
            for r in sorted(rows.values(), key=lambda x: x.prob_down, reverse=True):
                if r.prob_down < short_threshold:
                    break
                sym = r.symbol
                if (len(shorts) >= max_shorts or sym in shorts or sym in longs
                        or sym in BLACKLIST or cash < 50.0
                        or cooldown_until.get(sym, 0) > ts):
                    continue
                margin = cash * SHORT_SIZE_PCT
                amount = (margin - margin * TAKER_FEE) / r.close
                cash  -= margin
                shorts[sym] = {"amount": amount, "entry": r.close,
                               "margin": margin, "opened": ts}
                break

        # 5) Equity-Snapshot
        value = cash
        for sym, pos in longs.items():
            r = rows.get(sym)
            value += pos["amount"] * (r.close if r else pos["entry"])
        for sym, pos in shorts.items():
            r = rows.get(sym)
            px = r.close if r else pos["entry"]
            value += pos["margin"] + pos["amount"] * (pos["entry"] - px)
        equity.append((ts, value))

    # Offene Positionen am Ende schliessen (zum letzten Kurs)
    last_ts   = int(test["timestamp"].max())
    last_grp  = test[test["timestamp"] == last_ts]
    last_rows = {r.symbol: r for r in last_grp.itertuples()}
    for sym in list(longs):
        r = last_rows.get(sym)
        close_long(sym, r.close if r else longs[sym]["entry"], "END_OF_TEST", last_ts)
    for sym in list(shorts):
        r = last_rows.get(sym)
        close_short(sym, r.close if r else shorts[sym]["entry"], "END_OF_TEST", last_ts)
    if equity:
        equity[-1] = (last_ts, cash)

    # ── Metriken ──────────────────────────────────────────────────────────────
    eq = pd.DataFrame(equity, columns=["ts", "value"])
    final_value  = float(eq["value"].iloc[-1]) if not eq.empty else start_capital
    total_return = (final_value / start_capital - 1) * 100

    pls       = [t["pl"] for t in trades]
    wins      = [p for p in pls if p > 0]
    losses    = [p for p in pls if p <= 0]
    win_rate  = len(wins) / len(pls) * 100 if pls else 0.0
    pf        = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")

    peak     = eq["value"].cummax()
    drawdown = float(((eq["value"] - peak) / peak * 100).min()) if len(eq) > 1 else 0.0

    rets   = eq["value"].pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(24 * 365)) if len(rets) > 2 and rets.std() > 0 else 0.0

    # Buy&Hold BTC als Benchmark ueber denselben Zeitraum
    btc = test[test["symbol"] == "BTC/EUR"].sort_values("timestamp")
    bh_return = float((btc["close"].iloc[-1] / btc["close"].iloc[0] - 1) * 100) if len(btc) > 1 else 0.0

    # Aufschluesselung nach Grund und Seite
    reason_stats: dict[str, dict] = {}
    for t in trades:
        s = reason_stats.setdefault(t["reason"], {"n": 0, "pl": 0.0})
        s["n"]  += 1
        s["pl"] += t["pl"]
    side_stats: dict[str, dict] = {}
    for t in trades:
        s = side_stats.setdefault(t["side"], {"n": 0, "pl": 0.0, "wins": 0})
        s["n"]  += 1
        s["pl"] += t["pl"]
        if t["pl"] > 0:
            s["wins"] += 1

    # Equity-Kurve fuer das Dashboard auf max 2000 Punkte eindampfen
    step      = max(1, len(eq) // 2000)
    eq_points = eq.iloc[::step].values.tolist()

    result = {
        "computed_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        "runtime_s":     round(time.time() - t0, 1),
        "params": {
            "train_frac":      train_frac,
            "buy_threshold":   buy_threshold,
            "short_threshold": short_threshold,
            "start_capital":   start_capital,
            "enable_shorts":   enable_shorts,
            "stop_loss_pct":   stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "short_sl_pct":    short_sl_pct,
            "short_tp_pct":    short_tp_pct,
            "use_fixed_stop":  use_fixed_stop,
            "vol_target":      vol_target,
            "max_positions":   max_positions,
            "max_shorts":      max_shorts,
            "bull_size_pct":      bull_size_pct,
            "bull_max_positions": bull_max_positions,
            "buys_per_cycle":     buys_per_cycle,
            "short_regimes":      list(short_regimes),
        },
        "period": {
            "from": datetime.fromtimestamp(int(split_ts) / 1000).strftime("%Y-%m-%d"),
            "to":   datetime.fromtimestamp(last_ts / 1000).strftime("%Y-%m-%d"),
            "hours": len(eq),
        },
        "metrics": {
            "final_value":   round(final_value, 2),
            "total_return":  round(total_return, 2),
            "bh_btc_return": round(bh_return, 2),
            "n_trades":      len(trades),
            "win_rate":      round(win_rate, 1),
            "profit_factor": round(pf, 2) if pf != float("inf") else None,
            "avg_win":       round(float(np.mean(wins)), 2) if wins else 0.0,
            "avg_loss":      round(float(np.mean(losses)), 2) if losses else 0.0,
            "best":          round(max(pls), 2) if pls else 0.0,
            "worst":         round(min(pls), 2) if pls else 0.0,
            "max_drawdown":  round(drawdown, 1),
            "sharpe":        round(sharpe, 2),
        },
        "regime_hours": regime_hours,
        "reason_stats": {k: {"n": v["n"], "pl": round(v["pl"], 2)}
                         for k, v in sorted(reason_stats.items())},
        "side_stats":   {k: {"n": v["n"], "pl": round(v["pl"], 2), "wins": v["wins"]}
                         for k, v in side_stats.items()},
        "equity":       eq_points,
        "trades":       trades[-500:],  # letzte 500 fuer die Tabelle
    }

    if save:
        RESULT_PATH.parent.mkdir(exist_ok=True)
        with open(RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        log.info("Backtest-Ergebnis gespeichert: %s", RESULT_PATH)

    return result


def load_backtest_result() -> dict | None:
    """Letztes gespeichertes Backtest-Ergebnis laden (fuer das Dashboard)."""
    if not RESULT_PATH.exists():
        return None
    try:
        with open(RESULT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run_backtest()
    if "error" in res:
        print("FEHLER:", res["error"])
    else:
        m = res["metrics"]
        print(f"\n{'='*52}\nBACKTEST {res['period']['from']} -> {res['period']['to']}"
              f"  ({res['runtime_s']}s)\n{'='*52}")
        print(f"Endwert:        {m['final_value']:.2f} EUR  ({m['total_return']:+.2f}%)")
        print(f"Buy&Hold BTC:   {m['bh_btc_return']:+.2f}%")
        print(f"Trades:         {m['n_trades']}  |  Win-Rate: {m['win_rate']:.1f}%")
        print(f"Profit-Faktor:  {m['profit_factor']}  |  Sharpe: {m['sharpe']}")
        print(f"Max Drawdown:   {m['max_drawdown']:.1f}%")
        print(f"Regime-Stunden: {res['regime_hours']}")
        print("\nNach Grund:")
        for r, s in res["reason_stats"].items():
            print(f"  {r:<22} n={s['n']:<5} P/L={s['pl']:+9.2f} EUR")
