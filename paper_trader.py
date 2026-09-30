"""Paper-Trading: simuliert Kauf- und Verkaufsentscheidungen ohne echtes Geld.

Zwei Signal-Schichten:
  SCHNELL (alle 5 Min) – fast_signal_check():
    - Stark negative News in letzten 15 Min  → sofort verkaufen
    - Whale plötzlich distribuierend          → sofort verkaufen
    - 5min RSI > 80 (überkauft)              → sofort verkaufen
    - Sehr positive News + RSI ok            → sofort kaufen

  LANGSAM (stündlich) – run_paper_trading_cycle():
    - ML-Modell P(up) > 49%                  → kaufen
    - ML-Modell P(up) < 44%                  → verkaufen
    - Stop-Loss -5% / Take-Profit +8%        → verkaufen

  LIVE (jede Minute) – check_stops_live():
    - Stop-Loss und Take-Profit gegen Live-Preise
"""
import logging
import sqlite3
from datetime import datetime

from db import DB_PATH, get_connection
from market_regime import get_current_regime
from telegram_notify import notify_buy, notify_sell, notify_panic, notify_portfolio

log = logging.getLogger(__name__)

INITIAL_CAPITAL    = 1000.0
MAX_POSITIONS      = 5        # max 5 Positionen – Qualität statt Quantität
TOP_BUYS_PER_CYCLE = 1        # nur 1 neuer Kauf pro Stunde
POSITION_SIZE_PCT  = 0.06     # 6% pro Trade – ~2.5 EUR Verlust pro Stop-Loss statt ~5 EUR
MIN_CONFIDENCE_BUY = 0.68     # Backtest 2026-06-11: 0.62 -> 0.68 = weniger, bessere Trades
SELL_THRESHOLD     = 0.40     # ML-Verkauf nur bei starkem SELL-Signal

# Regime-abgestuft statt Alles-oder-Nichts: Die Komplett-Sperre ausser BULL
# hiess wochenlang NULL Trades – damit ist die Win-Rate nie validierbar.
# SIDEWAYS: kaufen erlaubt, aber strengere Schwelle + halbe Positionsgroesse.
SIDEWAYS_MIN_CONFIDENCE = 0.68
SIDEWAYS_SIZE_PCT       = 0.03

# Paper-Shorts: NUR im BEAR-Regime, nur simuliert. Edge gemessen am 2026-06-11
# (check_buy_precision.py, Holdout): P(down)>=0.65 trifft 41.5% vs. 27.1% Basis.
ENABLE_SHORTS         = True
SHORT_MIN_CONFIDENCE  = 0.70   # Backtest 2026-06-11: 0.65 -> 0.70 = weniger, bessere Shorts
SHORT_SIZE_PCT        = 0.05   # etwas kleiner als Long-Positionen
MAX_SHORTS            = 4      # Backtest 2026-06-12: 2->4 hob Return -0.0%->+3.0%,
                              # Sharpe +0.05->+0.20, Drawdown -11.8%->-10.0% (alle besser).
                              # NICHT auf 5-6 trotz minimal besserer Zahlen: Test-Zeitraum
                              # baerenlastig (ueberschaetzt Short-Nutzen) + korreliertes
                              # Squeeze-Risiko waechst. 4 = 20% Exposure, unter Long-Limit.
TOP_SHORTS_PER_CYCLE  = 1      # max 1 neuer Short pro Stunde
SHORT_STOP_LOSS_PCT   = 0.04   # Cover wenn Kurs 4% UEBER Einstieg steigt
SHORT_TAKE_PROFIT_PCT = 0.06   # Cover wenn Kurs 6% UNTER Einstieg faellt
STOP_LOSS_PCT      = 0.30     # Reine Katastrophen-Notbremse! Backtest 2026-06-12 mit
                              # neuen Features: JEDER fixe Stop schadet Return UND Drawdown
                              # (0.15->-7.5%, 0.20->-5.0%, 0.25->-0.9%, kein Stop->+2.2%).
                              # Der fixe Stop realisiert Verluste die sich erholt haetten.
                              # Ausstieg macht Trailing-Stop + ML-Exit; 0.30 faengt nur
                              # einen echten Single-Candle-Kollaps ab.
TAKE_PROFIT_PCT    = 0.08     # Take-Profit 8%
WHALE_VOL_TRIGGER  = 2.5
TAKER_FEE          = 0.0026   # Kraken Taker-Gebühr 0.26%
TRAILING_STOP_PCT  = 0.03     # Trailing Stop: 3% unter Höchststand
MIN_HOLD_HOURS     = 3        # Mindest-Haltezeit: 3h bevor Panic-Sell greift
PANIC_COOLDOWN_H   = 2        # Keine Neukäufe 2h nach Panic-Sell
STOPLOSS_COOLDOWN_H = 24     # Kein Neukauf 24h nach Stop-Loss desselben Symbols
ML_LOSS_COOLDOWN_H  = 6      # Kein Neukauf 6h nach ML_SIGNAL-Verlust desselben Symbols
MAX_SIGNAL_AGE_MS   = 3 * 3600 * 1000  # Signale aelter als 3h sind wertlos – ignorieren
MAX_PRICE_AGE_MS    = 3 * 3600 * 1000  # Preise aelter als 3h = Symbol eingefroren – nicht handeln
BLACKLIST           = {
    "ZEC/EUR",                              # Zu volatil, kein Edge
    "USDC/EUR", "USDT/EUR", "EURC/EUR",     # Stablecoins – nur Gebuehren, kein Edge
    "PAXG/EUR",                             # Gold-Token – passt nicht zur Krypto-Strategie
}


# ── DB-Setup ──────────────────────────────────────────────────────────────────

def init_paper_trading() -> None:
    """Tabellen anlegen und Startkapital einsetzen (einmalig)."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stoploss_cooldown (
                symbol     TEXT    PRIMARY KEY,
                triggered_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trailing_stops (
                symbol     TEXT    PRIMARY KEY,
                highest    REAL    NOT NULL,
                stop_price REAL    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       INTEGER NOT NULL,
                symbol          TEXT    NOT NULL,
                action          TEXT    NOT NULL,
                price           REAL    NOT NULL,
                amount          REAL    NOT NULL,
                value_eur       REAL    NOT NULL,
                pl_eur          REAL,
                reason          TEXT,
                prob_up         REAL,
                portfolio_value REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_portfolio (
                symbol        TEXT    PRIMARY KEY,
                amount        REAL    NOT NULL,
                avg_buy_price REAL    NOT NULL,
                bought_at     INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_shorts (
                symbol      TEXT    PRIMARY KEY,
                amount      REAL    NOT NULL,
                entry_price REAL    NOT NULL,
                margin_eur  REAL    NOT NULL,
                opened_at   INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_balance (
                timestamp    INTEGER PRIMARY KEY,
                cash_eur     REAL    NOT NULL,
                invested_eur REAL    NOT NULL,
                total_value  REAL    NOT NULL
            )
        """)
        exists = conn.execute("SELECT COUNT(*) FROM paper_balance").fetchone()[0]
        if exists == 0:
            ts = int(datetime.now().timestamp() * 1000)
            conn.execute(
                "INSERT INTO paper_balance VALUES (?,?,?,?)",
                (ts, INITIAL_CAPITAL, 0.0, INITIAL_CAPITAL),
            )
            log.info("Paper-Trading initialisiert: %.2f EUR Startkapital", INITIAL_CAPITAL)
        conn.commit()


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _get_cash(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT cash_eur FROM paper_balance ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    return float(row[0]) if row else INITIAL_CAPITAL


def _load_portfolio(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT symbol, amount, avg_buy_price, bought_at FROM paper_portfolio"
    ).fetchall()
    return {r[0]: {"amount": r[1], "avg_buy_price": r[2], "bought_at": r[3]} for r in rows}


def _load_signals(conn: sqlite3.Connection) -> dict:
    """Neueste ML-Signale je Symbol laden – veraltete Signale werden ignoriert.

    Symbole die aus den Top-20 fallen bekommen keine neuen Daten mehr;
    ihre alten Signale duerfen keine Trades mehr ausloesen (LINK-Bug).
    """
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals'"
    ).fetchone()
    if not has:
        return {}
    # prob_down existiert erst seit dem Short-Update – bei alten DBs NULL liefern
    cols = [r[1] for r in conn.execute("PRAGMA table_info(signals)")]
    down_col = "s.prob_down" if "prob_down" in cols else "NULL"
    cutoff = int(datetime.now().timestamp() * 1000) - MAX_SIGNAL_AGE_MS
    rows = conn.execute(
        f"""SELECT s.symbol, s.prob_up, s.signal, s.close, {down_col}
           FROM signals s
           INNER JOIN (
               SELECT symbol, MAX(timestamp) AS max_ts FROM signals GROUP BY symbol
           ) l ON s.symbol = l.symbol AND s.timestamp = l.max_ts
           WHERE s.timestamp > ?""",
        (cutoff,),
    ).fetchall()
    return {r[0]: {"prob_up": r[1], "signal": r[2], "close": r[3], "prob_down": r[4]}
            for r in rows}


def _get_latest_price(
    conn: sqlite3.Connection, symbol: str, max_age_ms: int | None = MAX_PRICE_AGE_MS
) -> float | None:
    """Letzten 1h-Schlusskurs liefern; None wenn der Preis eingefroren/veraltet ist."""
    row = conn.execute(
        """SELECT close, timestamp FROM features
           WHERE symbol = ? AND timeframe = '1h'
           ORDER BY timestamp DESC LIMIT 1""",
        (symbol,),
    ).fetchone()
    if not row:
        return None
    if max_age_ms is not None:
        age_ms = int(datetime.now().timestamp() * 1000) - int(row[1])
        if age_ms > max_age_ms:
            return None
    return float(row[0])


def get_portfolio_symbols() -> list[str]:
    """Aktuell gehaltene Symbole – der Scheduler muss diese IMMER beobachten,
    auch wenn sie aus den Top-20 nach Volumen fallen (sonst Zombie-Positionen)."""
    with sqlite3.connect(DB_PATH) as conn:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_portfolio'"
        ).fetchone()
        if not has:
            return []
        return [r[0] for r in conn.execute("SELECT symbol FROM paper_portfolio")]


def _get_whale_boost(conn: sqlite3.Connection, symbol: str) -> bool:
    """True wenn Whale-Aktivität über dem Trigger liegt."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='whale_signals'"
    ).fetchone()
    if not has:
        return False
    row = conn.execute(
        """SELECT vol_anomaly FROM whale_signals
           WHERE coin = ? ORDER BY timestamp DESC LIMIT 1""",
        (symbol,),
    ).fetchone()
    return bool(row and row[0] and float(row[0]) >= WHALE_VOL_TRIGGER)


def _calc_invested_value(conn: sqlite3.Connection) -> float:
    portfolio = _load_portfolio(conn)
    total = 0.0
    for symbol, pos in portfolio.items():
        # Fuer die Bewertung zaehlt der letzte bekannte Preis (auch wenn alt),
        # sonst wuerden eingefrorene Positionen faelschlich mit 0 bewertet
        price = _get_latest_price(conn, symbol, max_age_ms=None) or pos["avg_buy_price"]
        total += pos["amount"] * price
    return total


def _load_shorts(conn: sqlite3.Connection) -> dict:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_shorts'"
    ).fetchone()
    if not has:
        return {}
    rows = conn.execute(
        "SELECT symbol, amount, entry_price, margin_eur, opened_at FROM paper_shorts"
    ).fetchall()
    return {r[0]: {"amount": r[1], "entry_price": r[2],
                   "margin_eur": r[3], "opened_at": r[4]} for r in rows}


def _calc_shorts_value(conn: sqlite3.Connection, live_prices: dict | None = None) -> float:
    """Aktueller Wert aller Short-Positionen: Margin + unrealisierter Gewinn/Verlust."""
    total = 0.0
    for symbol, pos in _load_shorts(conn).items():
        price = (live_prices or {}).get(symbol) \
            or _get_latest_price(conn, symbol, max_age_ms=None) \
            or pos["entry_price"]
        total += pos["margin_eur"] + pos["amount"] * (pos["entry_price"] - price)
    return total


def _total_portfolio_value(conn: sqlite3.Connection, cash: float) -> float:
    return cash + _calc_invested_value(conn) + _calc_shorts_value(conn)


def _in_cooldown(conn: sqlite3.Connection, symbol: str) -> bool:
    """Prueft die Wiederkauf-Sperre (Stop-Loss: 24h, ML-Verlust: 6h)."""
    row = conn.execute(
        "SELECT triggered_at FROM stoploss_cooldown WHERE symbol = ?", (symbol,)
    ).fetchone()
    if not row:
        return False
    last_trade = conn.execute(
        """SELECT reason FROM paper_trades
           WHERE symbol=? AND action IN ('SELL','COVER')
           ORDER BY timestamp DESC LIMIT 1""",
        (symbol,),
    ).fetchone()
    is_stoploss = last_trade and ("STOP_LOSS" in last_trade[0]
                                  or "TRAILING_STOP" in last_trade[0])
    cooldown_h  = STOPLOSS_COOLDOWN_H if is_stoploss else ML_LOSS_COOLDOWN_H
    cooldown_ms = cooldown_h * 3600 * 1000
    elapsed_ms  = int(datetime.now().timestamp() * 1000) - row[0]
    if elapsed_ms < cooldown_ms:
        log.info("COOLDOWN %s – noch %.1fh gesperrt (%s)", symbol,
                 (cooldown_ms - elapsed_ms) / 3600000,
                 "Stop-Loss" if is_stoploss else "ML-Verlust")
        return True
    conn.execute("DELETE FROM stoploss_cooldown WHERE symbol = ?", (symbol,))
    return False


# ── Trade-Ausführung ──────────────────────────────────────────────────────────

def _execute_buy(
    conn: sqlite3.Connection,
    symbol: str,
    price: float,
    invest_eur: float,
    reason: str,
    prob_up: float,
    cash: float,
) -> float:
    fee      = invest_eur * TAKER_FEE
    amount   = (invest_eur - fee) / price
    new_cash = cash - invest_eur
    ts       = int(datetime.now().timestamp() * 1000)

    # Portfolio aktualisieren
    existing = conn.execute(
        "SELECT amount, avg_buy_price FROM paper_portfolio WHERE symbol = ?", (symbol,)
    ).fetchone()
    if existing:
        total_amount = existing[0] + amount
        avg_price    = (existing[0] * existing[1] + amount * price) / total_amount
        conn.execute(
            "UPDATE paper_portfolio SET amount=?, avg_buy_price=? WHERE symbol=?",
            (total_amount, avg_price, symbol),
        )
    else:
        conn.execute(
            "INSERT INTO paper_portfolio VALUES (?,?,?,?)",
            (symbol, amount, price, ts),
        )

    port_value = _total_portfolio_value(conn, new_cash)
    conn.execute(
        """INSERT INTO paper_trades
           (timestamp, symbol, action, price, amount, value_eur, pl_eur, reason, prob_up, portfolio_value)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ts, symbol, "BUY", price, amount, invest_eur, None, reason, prob_up, port_value),
    )
    conn.commit()
    log.info("BUY  %-12s | %.4f @ %.4f EUR | Invest: %.2f EUR | Grund: %s | P(up)=%.0f%%",
             symbol, amount, price, invest_eur, reason, prob_up * 100)
    notify_buy(symbol, price, amount, invest_eur, reason, prob_up)
    return new_cash


def _execute_sell(
    conn: sqlite3.Connection,
    symbol: str,
    pos: dict,
    price: float,
    reason: str,
    prob_up: float,
    cash: float,
) -> float:
    fee       = pos["amount"] * price * TAKER_FEE
    value_eur = pos["amount"] * price - fee
    pl_eur    = value_eur - (pos["amount"] * pos["avg_buy_price"])
    new_cash  = cash + value_eur
    ts        = int(datetime.now().timestamp() * 1000)

    conn.execute("DELETE FROM paper_portfolio WHERE symbol = ?", (symbol,))
    conn.execute("DELETE FROM trailing_stops WHERE symbol = ?", (symbol,))

    # Cooldown setzen – verhindert sofortigen Wiederkauf nach Verlust
    if "STOP_LOSS" in reason or "TRAILING_STOP" in reason:
        conn.execute(
            "INSERT OR REPLACE INTO stoploss_cooldown (symbol, triggered_at) VALUES (?,?)",
            (symbol, ts),
        )
    elif reason == "ML_SIGNAL" and pl_eur < 0:
        conn.execute(
            "INSERT OR REPLACE INTO stoploss_cooldown (symbol, triggered_at) VALUES (?,?)",
            (symbol, ts),
        )

    port_value = _total_portfolio_value(conn, new_cash)
    conn.execute(
        """INSERT INTO paper_trades
           (timestamp, symbol, action, price, amount, value_eur, pl_eur, reason, prob_up, portfolio_value)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ts, symbol, "SELL", price, pos["amount"], value_eur, pl_eur, reason, prob_up, port_value),
    )
    conn.commit()
    log.info("SELL %-12s | %.4f @ %.4f EUR | Erlös: %.2f EUR | P/L: %+.2f EUR | Grund: %s",
             symbol, pos["amount"], price, value_eur, pl_eur, reason)
    notify_sell(symbol, price, value_eur, pl_eur, reason)
    return new_cash


def _execute_short_open(
    conn: sqlite3.Connection,
    symbol: str,
    price: float,
    margin_eur: float,
    reason: str,
    prob_down: float,
    cash: float,
) -> float:
    """Simulierten Short eroeffnen: Margin wird aus dem Cash reserviert."""
    fee      = margin_eur * TAKER_FEE
    amount   = (margin_eur - fee) / price
    new_cash = cash - margin_eur
    ts       = int(datetime.now().timestamp() * 1000)

    conn.execute(
        "INSERT INTO paper_shorts VALUES (?,?,?,?,?)",
        (symbol, amount, price, margin_eur, ts),
    )
    port_value = _total_portfolio_value(conn, new_cash)
    conn.execute(
        """INSERT INTO paper_trades
           (timestamp, symbol, action, price, amount, value_eur, pl_eur, reason, prob_up, portfolio_value)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ts, symbol, "SHORT", price, amount, margin_eur, None, reason, prob_down, port_value),
    )
    conn.commit()
    log.info("SHORT %-12s | %.4f @ %.4f EUR | Margin: %.2f EUR | Grund: %s | P(down)=%.0f%%",
             symbol, amount, price, margin_eur, reason, prob_down * 100)
    notify_buy(symbol, price, amount, margin_eur, reason, prob_down)
    return new_cash


def _execute_short_cover(
    conn: sqlite3.Connection,
    symbol: str,
    pos: dict,
    price: float,
    reason: str,
    prob_down: float,
    cash: float,
) -> float:
    """Short schliessen (zurueckkaufen). Gewinn wenn der Kurs gefallen ist."""
    fee_close = pos["amount"] * price * TAKER_FEE
    pl_eur    = pos["amount"] * (pos["entry_price"] - price) - fee_close
    value_eur = pos["margin_eur"] + pl_eur
    new_cash  = cash + value_eur
    ts        = int(datetime.now().timestamp() * 1000)

    conn.execute("DELETE FROM paper_shorts WHERE symbol = ?", (symbol,))

    # Gleiche Wiederkauf-Sperren wie bei Longs
    if "STOP_LOSS" in reason or (reason.startswith("ML_SIGNAL") and pl_eur < 0):
        conn.execute(
            "INSERT OR REPLACE INTO stoploss_cooldown (symbol, triggered_at) VALUES (?,?)",
            (symbol, ts),
        )

    port_value = _total_portfolio_value(conn, new_cash)
    conn.execute(
        """INSERT INTO paper_trades
           (timestamp, symbol, action, price, amount, value_eur, pl_eur, reason, prob_up, portfolio_value)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ts, symbol, "COVER", price, pos["amount"], value_eur, pl_eur, reason, prob_down, port_value),
    )
    conn.commit()
    log.info("COVER %-12s | %.4f @ %.4f EUR | Erlös: %.2f EUR | P/L: %+.2f EUR | Grund: %s",
             symbol, pos["amount"], price, value_eur, pl_eur, reason)
    notify_sell(symbol, price, value_eur, pl_eur, reason)
    return new_cash


# ── Haupt-Zyklus ──────────────────────────────────────────────────────────────

def run_paper_trading_cycle() -> None:
    """Einen vollständigen Paper-Trading-Zyklus ausführen."""
    init_paper_trading()

    with sqlite3.connect(DB_PATH) as conn:
        signals   = _load_signals(conn)
        if not signals:
            log.warning("Keine ML-Signale – zuerst model.py ausführen.")
            return

        portfolio = _load_portfolio(conn)
        cash      = _get_cash(conn)

        log.info("Paper-Trading Zyklus | Cash: %.2f EUR | Positionen: %d",
                 cash, len(portfolio))

        # ── 1. Verkaufsentscheidungen ─────────────────────────────────────────
        for symbol, pos in list(portfolio.items()):
            price = _get_latest_price(conn, symbol)
            if price is None or pos["avg_buy_price"] <= 0:
                continue

            pl_pct  = (price - pos["avg_buy_price"]) / pos["avg_buy_price"]
            signal  = signals.get(symbol, {})
            prob_up = signal.get("prob_up", 0.5)

            reason = None
            if pl_pct <= -STOP_LOSS_PCT:
                reason = "STOP_LOSS"
            elif pl_pct >= TAKE_PROFIT_PCT:
                reason = "TAKE_PROFIT"
            elif prob_up < SELL_THRESHOLD:
                reason = "ML_SIGNAL"

            if reason:
                cash      = _execute_sell(conn, symbol, pos, price, reason, prob_up, cash)
                portfolio = _load_portfolio(conn)

        # ── 1b. Short-Cover-Entscheidungen ────────────────────────────────────
        for symbol, pos in list(_load_shorts(conn).items()):
            price = _get_latest_price(conn, symbol)
            if price is None or pos["entry_price"] <= 0:
                continue

            move      = (price - pos["entry_price"]) / pos["entry_price"]  # >0 = gegen uns
            prob_down = signals.get(symbol, {}).get("prob_down")

            reason = None
            if move >= SHORT_STOP_LOSS_PCT:
                reason = "SHORT_STOP_LOSS"
            elif move <= -SHORT_TAKE_PROFIT_PCT:
                reason = "SHORT_TAKE_PROFIT"
            elif prob_down is not None and prob_down < SELL_THRESHOLD:
                reason = "ML_SIGNAL_COVER"

            if reason:
                cash = _execute_short_cover(conn, symbol, pos, price, reason,
                                            prob_down or 0.5, cash)

        # ── 2. Kaufentscheidungen (regime-abgestuft) ─────────────────────────
        regime = get_current_regime()
        reg    = regime["regime"]
        if reg == "BULL":
            min_conf, size_pct = MIN_CONFIDENCE_BUY, POSITION_SIZE_PCT
        elif reg == "SIDEWAYS":
            min_conf, size_pct = SIDEWAYS_MIN_CONFIDENCE, SIDEWAYS_SIZE_PCT
        else:  # BEAR / UNKNOWN
            min_conf, size_pct = None, None
            log.info("Regime %s (%.0f%% Konfidenz) – keine Long-Käufe.",
                     reg, regime["confidence"] * 100)

        if min_conf is not None:
            portfolio  = _load_portfolio(conn)
            shorts     = _load_shorts(conn)
            buys_made  = 0
            ranked     = sorted(signals.items(), key=lambda x: x[1]["prob_up"], reverse=True)

            for symbol, signal in ranked:
                if len(portfolio) >= MAX_POSITIONS:
                    break
                if buys_made >= TOP_BUYS_PER_CYCLE:
                    break
                if symbol in portfolio or symbol in shorts:
                    continue
                if symbol in BLACKLIST:
                    continue
                if _in_cooldown(conn, symbol):
                    continue

                prob_up = signal["prob_up"]
                if prob_up >= min_conf and cash >= 50.0:
                    price = _get_latest_price(conn, symbol)
                    if not price or price <= 0:
                        continue
                    invest = cash * size_pct
                    cash   = _execute_buy(conn, symbol, price, invest, "ML_SIGNAL", prob_up, cash)
                    portfolio  = _load_portfolio(conn)
                    buys_made += 1

        # ── 2b. Shorts eröffnen (regime-UNABHAENGIG, vom Modell gesteuert) ───
        # Backtest 2026-06-18: Shorts ans Regime zu koppeln war ein Fehler – der
        # Regime-Detektor (langsamer BTC-EMA) hinkt nach und labelt kippende
        # Maerkte noch als BULL, wodurch die Shorts blockiert wurden (Bot handelte
        # tagelang nicht). Der prob_down>=0.70-Filter ist selbst-schuetzend: in
        # einem echten Bullen ist das Modell nicht bearish -> keine Shorts.
        # Robust ueber 4 Splits: nie schlechter, 2x +7pp, Drawdown stets <= vorher.
        if ENABLE_SHORTS:
            portfolio   = _load_portfolio(conn)
            shorts      = _load_shorts(conn)
            opens_made  = 0
            ranked_down = sorted(
                (item for item in signals.items() if item[1].get("prob_down") is not None),
                key=lambda x: x[1]["prob_down"], reverse=True,
            )

            for symbol, signal in ranked_down:
                if len(shorts) >= MAX_SHORTS or opens_made >= TOP_SHORTS_PER_CYCLE:
                    break
                if signal["prob_down"] < SHORT_MIN_CONFIDENCE:
                    break  # absteigend sortiert – darunter kommt nichts Besseres
                if symbol in shorts or symbol in portfolio or symbol in BLACKLIST:
                    continue
                if _in_cooldown(conn, symbol):
                    continue
                if cash < 50.0:
                    break

                price = _get_latest_price(conn, symbol)
                if not price or price <= 0:
                    continue
                margin = cash * SHORT_SIZE_PCT
                cash   = _execute_short_open(conn, symbol, price, margin,
                                             "SHORT_ML_SIGNAL", signal["prob_down"], cash)
                shorts      = _load_shorts(conn)
                opens_made += 1

        # ── 3. Balance-Snapshot speichern ─────────────────────────────────────
        invested   = _calc_invested_value(conn) + _calc_shorts_value(conn)
        total      = cash + invested
        ts         = int(datetime.now().timestamp() * 1000)
        conn.execute(
            "INSERT OR REPLACE INTO paper_balance VALUES (?,?,?,?)",
            (ts, cash, invested, total),
        )
        conn.commit()
        log.info("Portfolio-Wert: %.2f EUR | Cash: %.2f EUR | Investiert: %.2f EUR",
                 total, cash, invested)


def _get_fast_news_signal(conn: sqlite3.Connection) -> tuple[str, float]:
    """Sentiment der letzten 15 Minuten auswerten.

    Gibt (signal, avg_score) zurück:
      'PANIC'   score < 0.25  → alles verkaufen
      'BULLISH' score > 0.75  → kaufen falls möglich
      'NEUTRAL' sonst
    """
    cutoff = int(datetime.now().timestamp() * 1000) - 15 * 60 * 1000
    row = conn.execute(
        """SELECT AVG(score), COUNT(*) FROM news_sentiment
           WHERE fetched_at > ?""",
        (cutoff,),
    ).fetchone()
    if not row or not row[1] or row[1] == 0:
        return "NEUTRAL", 0.5
    article_count = row[1]
    avg_score = float(row[0])
    # Mindestens 5 Artikel nötig – ein Einzelartikel darf keinen Massenverkauf auslösen
    if avg_score < 0.12 and article_count >= 5:   return "PANIC",   avg_score
    if avg_score > 0.75:                           return "BULLISH",  avg_score
    return "NEUTRAL", avg_score


def _get_5min_rsi(conn: sqlite3.Connection, symbol: str) -> float | None:
    """5-Minuten-RSI für ein Symbol aus der features-Tabelle."""
    row = conn.execute(
        """SELECT rsi_14 FROM features
           WHERE symbol = ? AND timeframe = '5m'
           ORDER BY timestamp DESC LIMIT 1""",
        (symbol,),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def _whale_is_distributing(conn: sqlite3.Connection) -> bool:
    """True wenn Whale-Wallets gerade netto verkaufen."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='whale_wallets'"
    ).fetchone()
    if not has:
        return False
    rows = conn.execute(
        """SELECT signal FROM whale_wallets
           WHERE timestamp = (SELECT MAX(timestamp) FROM whale_wallets)"""
    ).fetchall()
    signals   = [r[0] for r in rows]
    dis_count = signals.count("DISTRIBUTING")
    acc_count = signals.count("ACCUMULATING")
    return dis_count > acc_count + 1


def fast_signal_check(live_prices: dict[str, float]) -> None:
    """Schnelle Signalprüfung alle 5 Minuten.

    Reagiert auf News-Events und Whale-Bewegungen sofort —
    unabhängig vom stündlichen ML-Zyklus.
    """
    with sqlite3.connect(DB_PATH) as conn:
        has_bal = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_balance'"
        ).fetchone()
        if not has_bal:
            return

        news_signal, news_score = _get_fast_news_signal(conn)
        whale_selling           = _whale_is_distributing(conn)
        portfolio               = _load_portfolio(conn)
        cash                    = _get_cash(conn)

        if not portfolio and news_signal != "BULLISH":
            return

        now_ms        = int(datetime.now().timestamp() * 1000)
        min_hold_ms   = MIN_HOLD_HOURS * 3600 * 1000

        # ── SCHNELL-VERKAUF (nur bei Positionen die MIN_HOLD_HOURS gehalten wurden) ──
        if news_signal == "PANIC" or whale_selling:
            reason = f"FAST_NEWS_PANIC(score={news_score:.2f})" if news_signal == "PANIC" \
                     else "FAST_WHALE_SELL"
            for symbol, pos in list(portfolio.items()):
                held_ms = now_ms - pos.get("bought_at", now_ms)
                if held_ms < min_hold_ms:
                    log.info("PANIC ignoriert fuer %s – zu jung (%.1fh < %dh)",
                             symbol, held_ms / 3600000, MIN_HOLD_HOURS)
                    continue
                price = live_prices.get(symbol)
                if not price:
                    continue
                rsi = _get_5min_rsi(conn, symbol)
                if rsi is None or rsi > 30:
                    prob = _load_signals(conn).get(symbol, {}).get("prob_up", 0.5)
                    log.warning("SCHNELL-VERKAUF: %s | %s", symbol, reason)
                    cash = _execute_sell(conn, symbol, pos, price, reason, prob, cash)
                    portfolio = _load_portfolio(conn)

        # ── 5min-RSI überkauft → Gewinne sichern (nur wenn im Gewinn) ────────
        elif not whale_selling:
            for symbol, pos in list(portfolio.items()):
                rsi = _get_5min_rsi(conn, symbol)
                if rsi is not None and rsi > 80:
                    price = live_prices.get(symbol)
                    if not price or pos["avg_buy_price"] <= 0:
                        continue
                    pl_pct = (price - pos["avg_buy_price"]) / pos["avg_buy_price"]
                    if pl_pct > 0.02:  # mind. 2% Gewinn
                        reason = f"FAST_RSI_OVERBOUGHT(rsi={rsi:.1f})"
                        log.info("RSI überkauft bei %s – Gewinn sichern (P&L: %.1f%%)",
                                 symbol, pl_pct * 100)
                        cash = _execute_sell(conn, symbol, pos, price, reason, 0.5, cash)
                        portfolio = _load_portfolio(conn)

        # FAST_NEWS_BULLISH Käufe deaktiviert – nur ML-Zyklus kauft
        # Grund: News flippt zu schnell, verursacht Buy-High/Sell-Low Whipsaw

        # Balance-Snapshot
        invested = sum(
            pos["amount"] * live_prices.get(sym, pos["avg_buy_price"])
            for sym, pos in _load_portfolio(conn).items()
        ) + _calc_shorts_value(conn, live_prices)
        ts = int(datetime.now().timestamp() * 1000)
        conn.execute(
            "INSERT OR REPLACE INTO paper_balance VALUES (?,?,?,?)",
            (ts, cash, invested, cash + invested),
        )
        conn.commit()


def snapshot_balance(live_prices: dict[str, float]) -> None:
    """Portfolio-Wert mit aktuellen Live-Preisen speichern (für den Chart)."""
    with sqlite3.connect(DB_PATH) as conn:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_balance'"
        ).fetchone()
        if not has:
            return
        cash = _get_cash(conn)
        portfolio = _load_portfolio(conn)
        invested = sum(
            pos["amount"] * live_prices.get(sym, pos["avg_buy_price"])
            for sym, pos in portfolio.items()
        ) + _calc_shorts_value(conn, live_prices)
        ts = int(datetime.now().timestamp() * 1000)
        conn.execute(
            "INSERT OR REPLACE INTO paper_balance VALUES (?,?,?,?)",
            (ts, cash, invested, cash + invested),
        )
        conn.commit()


def _update_trailing_stop(conn: sqlite3.Connection, symbol: str, price: float) -> float | None:
    """Trailing Stop aktualisieren und Stop-Preis zurückgeben."""
    row = conn.execute(
        "SELECT highest, stop_price FROM trailing_stops WHERE symbol = ?", (symbol,)
    ).fetchone()

    if row:
        highest, stop_price = row
        if price > highest:
            highest    = price
            stop_price = price * (1 - TRAILING_STOP_PCT)
            conn.execute(
                "UPDATE trailing_stops SET highest=?, stop_price=? WHERE symbol=?",
                (highest, stop_price, symbol),
            )
    else:
        highest    = price
        stop_price = price * (1 - TRAILING_STOP_PCT)
        conn.execute(
            "INSERT INTO trailing_stops VALUES (?,?,?)", (symbol, highest, stop_price)
        )
    conn.commit()
    return stop_price


def check_stops_live(live_prices: dict[str, float]) -> None:
    """Stop-Loss und Take-Profit gegen Live-Preise prüfen (jede Minute).

    Öffnet keine neuen Positionen – nur Ausstieg aus bestehenden.
    Wird vom 1-Min-Loop im Scheduler aufgerufen.
    """
    if not live_prices:
        return
    with sqlite3.connect(DB_PATH) as conn:
        portfolio = _load_portfolio(conn)
        shorts    = _load_shorts(conn)
        if not portfolio and not shorts:
            return

        cash = _get_cash(conn)
        changed = False

        for symbol, pos in list(portfolio.items()):
            price = live_prices.get(symbol)
            if price is None or pos["avg_buy_price"] <= 0:
                continue

            pl_pct      = (price - pos["avg_buy_price"]) / pos["avg_buy_price"]
            trail_stop  = _update_trailing_stop(conn, symbol, price)
            reason      = None

            if pl_pct <= -STOP_LOSS_PCT:
                reason = "STOP_LOSS"
            elif pl_pct >= TAKE_PROFIT_PCT:
                reason = "TAKE_PROFIT"
            elif trail_stop and price < trail_stop and pl_pct > 0.01:
                reason = f"TRAILING_STOP({trail_stop:.4f}€)"

            if reason:
                cash    = _execute_sell(conn, symbol, pos, price, reason, 0.5, cash)
                changed = True

        # Shorts: Live-Schutz ist hier am wichtigsten – ein Squeeze kann
        # innerhalb von Minuten passieren, nicht erst im Stunden-Zyklus
        for symbol, pos in list(shorts.items()):
            price = live_prices.get(symbol)
            if price is None or pos["entry_price"] <= 0:
                continue

            move   = (price - pos["entry_price"]) / pos["entry_price"]
            reason = None
            if move >= SHORT_STOP_LOSS_PCT:
                reason = "SHORT_STOP_LOSS"
            elif move <= -SHORT_TAKE_PROFIT_PCT:
                reason = "SHORT_TAKE_PROFIT"

            if reason:
                cash    = _execute_short_cover(conn, symbol, pos, price, reason, 0.5, cash)
                changed = True

        if changed:
            invested = _calc_invested_value(conn) + _calc_shorts_value(conn, live_prices)
            ts       = int(datetime.now().timestamp() * 1000)
            conn.execute(
                "INSERT OR REPLACE INTO paper_balance VALUES (?,?,?,?)",
                (ts, cash, invested, cash + invested),
            )
            conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_paper_trading_cycle()
