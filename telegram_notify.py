"""Telegram-Benachrichtigungen für Kauf/Verkauf und wichtige Events."""
import logging
import os
import sqlite3
import threading
import time

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send(message: str) -> bool:
    """Nachricht an Telegram schicken. Gibt True bei Erfolg zurück."""
    if not TOKEN or not CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as exc:
        log.warning("Telegram-Fehler: %s", exc)
        return False


def notify_buy(symbol: str, price: float, amount: float,
               invest: float, reason: str, prob_up: float) -> None:
    send(
        f"🔵 <b>KAUF</b> — {symbol}\n"
        f"💰 Preis:     {price:,.4f} €\n"
        f"📦 Menge:     {amount:.6f}\n"
        f"💶 Investiert: {invest:.2f} €\n"
        f"🧠 Signal:    P(up) = {prob_up*100:.0f}%\n"
        f"📌 Grund:     {reason}"
    )


def notify_sell(symbol: str, price: float, value: float,
                pl_eur: float, reason: str) -> None:
    emoji = "🟢" if pl_eur >= 0 else "🔴"
    send(
        f"{emoji} <b>VERKAUF</b> — {symbol}\n"
        f"💰 Preis:  {price:,.4f} €\n"
        f"💶 Erlös:  {value:.2f} €\n"
        f"📊 P/L:    {pl_eur:+.2f} €\n"
        f"📌 Grund:  {reason}"
    )


def notify_panic(reason: str, positions: list[str]) -> None:
    send(
        f"🚨 <b>SCHNELL-VERKAUF</b>\n"
        f"⚠️ Grund: {reason}\n"
        f"📉 Positionen geschlossen: {', '.join(positions)}"
    )


def notify_portfolio(total: float, pl: float, win_rate: float, trades: int) -> None:
    emoji = "📈" if pl >= 0 else "📉"
    send(
        f"{emoji} <b>Portfolio-Update</b>\n"
        f"💼 Wert:     {total:.2f} €\n"
        f"📊 P/L:      {pl:+.2f} €\n"
        f"🎯 Win-Rate: {win_rate:.0f}%\n"
        f"🔄 Trades:   {trades}"
    )


def _get_portfolio_stats() -> tuple[float, float, float, int] | None:
    """Portfolio-Kennzahlen aus DB lesen."""
    try:
        from db import DB_PATH
        with sqlite3.connect(DB_PATH) as conn:
            bal = conn.execute(
                "SELECT total_value FROM paper_balance ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            sells = conn.execute(
                "SELECT pl_eur FROM paper_trades WHERE action='SELL' AND pl_eur IS NOT NULL"
            ).fetchall()
            total_trades = conn.execute(
                "SELECT COUNT(*) FROM paper_trades WHERE action='SELL'"
            ).fetchone()[0]
        if not bal:
            return None
        pl_vals  = [r[0] for r in sells]
        win_rate = (sum(1 for p in pl_vals if p > 0) / len(pl_vals) * 100) if pl_vals else 0
        return bal[0], bal[0] - 1000.0, win_rate, total_trades
    except Exception as exc:
        log.warning("Portfolio-Stats Fehler: %s", exc)
        return None


def _command_listener() -> None:
    """Background-Thread: lauscht auf /portfolio Befehle via Telegram."""
    if not TOKEN or not CHAT_ID:
        return
    offset = 0
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            if r.status_code != 200:
                time.sleep(5)
                continue
            updates = r.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                if text in ("/portfolio", "/portfolio@goatbot"):
                    stats = _get_portfolio_stats()
                    if stats:
                        notify_portfolio(*stats)
                    else:
                        send("Keine Portfolio-Daten verfügbar.")
        except Exception as exc:
            log.warning("Telegram-Listener Fehler: %s", exc)
            time.sleep(10)


def start_command_listener() -> None:
    """Startet den Telegram-Command-Listener als Daemon-Thread."""
    if not TOKEN or not CHAT_ID:
        log.warning("Telegram nicht konfiguriert – kein Command-Listener.")
        return
    t = threading.Thread(target=_command_listener, daemon=True, name="telegram-listener")
    t.start()
    log.info("Telegram-Command-Listener gestartet (/portfolio aktiv)")


if __name__ == "__main__":
    if send("✅ GOAT Trading Bot verbunden! Du bekommst ab jetzt Nachrichten bei jedem Trade."):
        print("Telegram-Verbindung erfolgreich!")
    else:
        print("Fehler – TOKEN oder CHAT_ID prüfen.")
