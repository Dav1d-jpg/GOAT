"""Dauerhafter Daten-Sammler mit kaskadierten Intervallen:
   1 Min  → Live-Preise + Stop-Loss/Take-Profit prüfen
   5 Min  → 5min-Kerzen + Ticker speichern
  15 Min  → News scrapen + Sentiment analysieren
  60 Min  → 1h-Kerzen + Features + Whale + ML-Signale + neue Trades
"""
import logging
import time
from pathlib import Path

from data_fetcher import (
    TIMEFRAMES, build_exchange, fetch_and_store_ohlcv,
    fetch_and_store_tickers, fetch_live_prices,
    get_top_eur_symbols, show_account_balance,
)
from db import init_db
from features import compute_and_store_all
from model import refresh_signals
from news_scraper import scrape_and_analyze
from paper_trader import (
    check_stops_live, fast_signal_check, get_portfolio_symbols,
    run_paper_trading_cycle, snapshot_balance,
)
from extra_features import run_extra_features
from market_regime import detect_regime
from telegram_notify import notify_portfolio, start_command_listener
from whale_tracker import run_whale_tracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

INTERVAL_1MIN      = 60
INTERVAL_5MIN      = 300
INTERVAL_NEWS      = 900    # 15 Min – wie dokumentiert (5 Min ueberlastete Ollama unnoetig)
INTERVAL_1H        = 3600
INTERVAL_TELEGRAM  = 7200   # Portfolio-Update alle 2 Stunden


if __name__ == "__main__":
    init_db()
    log.info("Scheduler gestartet.")
    log.info("   1min → Live-Preise + Stop-Loss/Take-Profit")
    log.info("   5min → 5min-Kerzen + Ticker")
    log.info("  15min → News + Sentiment")
    log.info("  60min → 1h-Kerzen + Features + Whales + ML + Paper-Trading")
    log.info("Stoppen mit Ctrl+C")

    last_5min:     float = 0.0
    last_news:     float = 0.0
    last_1h:       float = 0.0
    last_telegram: float = 0.0
    last_retrain_model: float = 0.0
    exchange = None
    symbols: list[str] = []

    start_command_listener()

    while True:
        try:
            now = time.time()

            # Exchange-Verbindung auffrischen falls nötig
            if not symbols or exchange is None or now - last_1h >= INTERVAL_1H:
                exchange = build_exchange()
                symbols  = get_top_eur_symbols(exchange)
                # Gehaltene Positionen IMMER beobachten – auch wenn sie aus den
                # Top-20 fallen. Sonst frieren ihre Preise ein und Stop-Loss /
                # Verkauf koennen nie ausloesen (Zombie-Positionen).
                symbols  = list(dict.fromkeys(symbols + get_portfolio_symbols()))

            # ── 1min: Live-Preise + schnelle Signale ─────────────────────────
            try:
                live_prices = fetch_live_prices(exchange, symbols)
                if Path("models/main.pkl").exists():
                    check_stops_live(live_prices)
                    fast_signal_check(live_prices)
                snapshot_balance(live_prices)
            except Exception as exc:
                log.error("1min-Fehler: %s", exc)
                live_prices = {}

            # ── 5min: 5min-Kerzen + Ticker ────────────────────────────────────
            if now - last_5min >= INTERVAL_5MIN:
                log.info("--- 5min-Zyklus ---")
                try:
                    fetch_and_store_ohlcv(exchange, symbols, timeframe="5m", limit=TIMEFRAMES["5m"])
                    fetch_and_store_tickers(exchange, symbols)
                except Exception as exc:
                    log.error("5min-Fehler: %s", exc)
                last_5min = now

            # ── 15min: News + Sentiment ───────────────────────────────────────
            if now - last_news >= INTERVAL_NEWS:
                log.info("--- News-Zyklus ---")
                try:
                    scrape_and_analyze()
                except Exception as exc:
                    log.error("News-Fehler: %s", exc)
                last_news = now

            # ── 60min: 1h-Kerzen + alles andere ──────────────────────────────
            if now - last_1h >= INTERVAL_1H:
                log.info("--- 1h-Zyklus ---")
                try:
                    fetch_and_store_ohlcv(exchange, symbols, timeframe="1h", limit=TIMEFRAMES["1h"])
                    compute_and_store_all("1h")
                    compute_and_store_all("5m")
                    run_whale_tracker()
                    run_extra_features()
                    detect_regime()
                    if Path("models/main.pkl").exists():
                        refresh_signals()
                        run_paper_trading_cycle()
                    show_account_balance(exchange)
                except Exception as exc:
                    log.error("1h-Zyklus Fehler: %s", exc)
                last_1h = now

            # ── 2h: Portfolio-Update via Telegram ────────────────────────────
            if now - last_telegram >= INTERVAL_TELEGRAM:
                try:
                    from db import DB_PATH
                    import sqlite3 as _sqlite3
                    with _sqlite3.connect(DB_PATH) as conn:
                        bal = conn.execute("SELECT total_value FROM paper_balance ORDER BY timestamp DESC LIMIT 1").fetchone()
                        sells = conn.execute("SELECT pl_eur FROM paper_trades WHERE action IN ('SELL','COVER') AND pl_eur IS NOT NULL").fetchall()
                        total_trades = conn.execute("SELECT COUNT(*) FROM paper_trades WHERE action IN ('SELL','COVER')").fetchone()[0]
                    if bal:
                        pl_vals  = [r[0] for r in sells]
                        win_rate = (sum(1 for p in pl_vals if p > 0) / len(pl_vals) * 100) if pl_vals else 0
                        notify_portfolio(bal[0], bal[0] - 1000.0, win_rate, total_trades)
                except Exception as exc:
                    log.warning("Telegram Portfolio-Update Fehler: %s", exc)
                last_telegram = now

            log.debug("Nächster 1min-Tick in %ds", INTERVAL_1MIN)
            time.sleep(INTERVAL_1MIN)

        except KeyboardInterrupt:
            log.info("Scheduler gestoppt.")
            break
        except Exception as exc:
            log.error("Unerwarteter Fehler – starte in 60s neu: %s", exc)
            time.sleep(INTERVAL_1MIN)
