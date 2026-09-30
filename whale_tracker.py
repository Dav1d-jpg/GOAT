"""Whale-Tracker: On-Chain-Daten, Volumen-Anomalien, Orderbook-Walls
und bekannte Whale-Wallets beobachten.

Quellen:
  1. Blockchair API  – aggregierte On-Chain-Statistiken (kein Key nötig)
  2. Blockchain.info – Bitcoin-Wallet-Bewegungen (kein Key nötig)
  3. Etherscan API   – Ethereum-Wallet-Bewegungen (kostenloser Key nötig)
  4. Volumen-Anomalien aus eigenen OHLCV-Daten
  5. Orderbook-Walls via Kraken (ccxt)
"""
import logging
import os
import sqlite3
import time

import requests
from dotenv import load_dotenv

from db import DB_PATH, get_connection

load_dotenv()
log = logging.getLogger(__name__)

ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "")

# ── Bekannte Whale-Wallets ────────────────────────────────────────────────────
# Große Exchange-Wallets und bekannte Adressen – Bewegungen hier sind marktrelevan

BTC_WHALE_WALLETS: dict[str, str] = {
    "Binance Cold 1":   "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo",
    "Binance Cold 2":   "bc1qgdjqv0av3q56jvd82tkdjpy7gdp9ut8tlqmgrpmv24sq90ecnvqqjwvw97",
    "Coinbase Cold":    "3GRdnTq18LyNveWa1gQJcgp8qEnzijv5vR",
    "Kraken":           "3Cbq7aT1tY8kMxWLbitaG7yT6bPbKChq64",
    "MicroStrategy":    "1P5ZEDWTKTFGxQjZphgWPQUpe554WKDfHQ",
}

ETH_WHALE_WALLETS: dict[str, str] = {
    "Binance Hot":      "0x28C6c06298d514Db089934071355E5743bf21d60",
    "Coinbase":         "0xa9D1e08C7793af67e9d92fe308d5697FB81d3E43",
    "Vitalik Buterin":  "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    "Kraken":           "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2",
    "Jump Trading":     "0x7e0188b0312a26ffe64b7e43a7a91d430fb20673",
}


# ── DB-Setup ──────────────────────────────────────────────────────────────────

def init_whale_table() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whale_signals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                coin            TEXT    NOT NULL,
                timestamp       INTEGER NOT NULL,
                tx_count_24h    INTEGER,
                avg_fee_usd     REAL,
                mempool_size    INTEGER,
                hashrate        REAL,
                vol_anomaly     REAL,
                large_order_bid REAL,
                large_order_ask REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whale_wallets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chain         TEXT    NOT NULL,
                label         TEXT    NOT NULL,
                address       TEXT    NOT NULL,
                timestamp     INTEGER NOT NULL,
                balance       REAL,
                tx_count      INTEGER,
                last_tx_value REAL,
                last_tx_type  TEXT,
                signal        TEXT
            )
        """)
        conn.commit()


# ── Blockchair On-Chain-Stats ─────────────────────────────────────────────────

BLOCKCHAIR_COINS: dict[str, str] = {
    "bitcoin":      "BTC/EUR",
    "ethereum":     "ETH/EUR",
    "litecoin":     "LTC/EUR",
    "bitcoin-cash": "BCH/EUR",
    "ripple":       "XRP/EUR",
}


def fetch_blockchair_stats(coin: str) -> dict:
    try:
        r = requests.get(
            f"https://api.blockchair.com/{coin}/stats",
            timeout=15,
            headers={"User-Agent": "GOAT-TradingBot/1.0"},
        )
        r.raise_for_status()
        d = r.json().get("data", {})
        return {
            "tx_count_24h": d.get("transactions_24h"),
            "avg_fee_usd":  d.get("average_transaction_fee_usd_24h"),
            "mempool_size": d.get("mempool_transactions"),
            "hashrate":     d.get("hashrate_24h"),
        }
    except Exception as exc:
        log.warning("Blockchair (%s): %s", coin, exc)
        return {}


# ── Bitcoin Wallet Tracker (blockchain.info) ──────────────────────────────────

def fetch_btc_wallet(label: str, address: str) -> dict | None:
    """Bitcoin-Wallet-Balance und letzte Transaktion prüfen."""
    try:
        r = requests.get(
            f"https://blockchain.info/rawaddr/{address}?limit=1",
            timeout=15,
            headers={"User-Agent": "GOAT-TradingBot/1.0"},
        )
        r.raise_for_status()
        data = r.json()
        balance_btc  = data.get("final_balance", 0) / 1e8
        txs          = data.get("txs", [])
        last_tx      = txs[0] if txs else {}
        # Nettofluss der letzten TX für diese Adresse
        net_btc = 0.0
        if last_tx:
            for out in last_tx.get("out", []):
                if out.get("addr") == address:
                    net_btc += out.get("value", 0) / 1e8
            for inp in last_tx.get("inputs", []):
                if inp.get("prev_out", {}).get("addr") == address:
                    net_btc -= inp.get("prev_out", {}).get("value", 0) / 1e8

        signal = "NEUTRAL"
        if net_btc > 10:    signal = "ACCUMULATING"   # Wallet sammelt BTC
        elif net_btc < -10: signal = "DISTRIBUTING"   # Wallet gibt BTC ab

        log.info("BTC Whale %-20s | Balance: %12.2f BTC | Letzte TX: %+.4f BTC | %s",
                 label, balance_btc, net_btc, signal)
        return {
            "chain": "BTC", "label": label, "address": address,
            "balance": balance_btc, "last_tx_value": net_btc, "signal": signal,
        }
    except Exception as exc:
        log.warning("BTC-Wallet (%s): %s", label, exc)
        return None


# ── Ethereum Wallet Tracker (Etherscan) ───────────────────────────────────────

def fetch_eth_wallet(label: str, address: str) -> dict | None:
    """Ethereum-Wallet-Balance und letzte Transaktion prüfen (Etherscan V2)."""
    base = "https://api.etherscan.io/v2/api?chainid=1"
    key_param = f"&apikey={ETHERSCAN_KEY}" if ETHERSCAN_KEY else ""

    try:
        # Balance
        r_bal = requests.get(
            f"{base}&module=account&action=balance&address={address}&tag=latest{key_param}",
            timeout=15,
        )
        r_bal.raise_for_status()
        result = r_bal.json().get("result", "0")
        if not str(result).lstrip("-").isdigit():
            log.warning("ETH-Wallet (%s): ungültige Antwort – Etherscan-Key nötig", label)
            return None
        balance_eth = int(result) / 1e18

        last_tx_value = None
        last_tx_type  = None
        signal        = "NEUTRAL"

        if ETHERSCAN_KEY:
            r_tx = requests.get(
                f"{base}&module=account&action=txlist&address={address}"
                f"&page=1&offset=1&sort=desc{key_param}",
                timeout=15,
            )
            txs = r_tx.json().get("result", [])
            if isinstance(txs, list) and txs:
                tx           = txs[0]
                eth_value    = int(tx.get("value", 0)) / 1e18
                last_tx_type = "OUT" if tx.get("from", "").lower() == address.lower() else "IN"
                last_tx_value = -eth_value if last_tx_type == "OUT" else eth_value
                if last_tx_value > 100:    signal = "ACCUMULATING"
                elif last_tx_value < -100: signal = "DISTRIBUTING"

        log.info("ETH Whale %-20s | Balance: %12.4f ETH | %s",
                 label, balance_eth, signal)
        return {
            "chain": "ETH", "label": label, "address": address,
            "balance": balance_eth, "last_tx_value": last_tx_value,
            "last_tx_type": last_tx_type, "signal": signal,
        }
    except Exception as exc:
        log.warning("ETH-Wallet (%s): %s", label, exc)
        return None


# ── Volumen-Anomalie + Orderbook ──────────────────────────────────────────────

def get_volume_anomaly(symbol: str) -> float | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """SELECT vol_ratio FROM features
               WHERE symbol = ? AND timeframe = '1h'
               ORDER BY timestamp DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
    return float(row[0]) if row and row[0] else None


def fetch_orderbook_walls(symbol: str) -> dict:
    try:
        from data_fetcher import build_exchange
        exchange = build_exchange()
        ob       = exchange.fetch_order_book(symbol, limit=50)
        top_bid  = max((b[1] for b in ob["bids"]), default=0)
        top_ask  = max((a[1] for a in ob["asks"]), default=0)
        return {"large_order_bid": top_bid, "large_order_ask": top_ask}
    except Exception as exc:
        log.warning("Orderbook (%s): %s", symbol, exc)
        return {}


# ── Haupt-Funktion ────────────────────────────────────────────────────────────

def run_whale_tracker() -> None:
    """Alle Whale-Daten sammeln und in DB speichern."""
    init_whale_table()
    ts = int(time.time() * 1000)

    # 1. Blockchair On-Chain-Stats
    for coin, symbol in BLOCKCHAIR_COINS.items():
        stats  = fetch_blockchair_stats(coin)
        vol    = get_volume_anomaly(symbol)
        orders = fetch_orderbook_walls(symbol)

        with get_connection() as conn:
            conn.execute(
                """INSERT INTO whale_signals
                   (coin, timestamp, tx_count_24h, avg_fee_usd,
                    mempool_size, hashrate, vol_anomaly,
                    large_order_bid, large_order_ask)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    symbol, ts,
                    stats.get("tx_count_24h"), stats.get("avg_fee_usd"),
                    stats.get("mempool_size"), stats.get("hashrate"),
                    vol,
                    orders.get("large_order_bid"), orders.get("large_order_ask"),
                ),
            )
            conn.commit()
        log.info("On-Chain %-12s | TX: %-8s | vol_ratio: %.2f",
                 symbol, stats.get("tx_count_24h") or "–", vol or 0)
        time.sleep(1.5)

    # 2. Bitcoin-Wallets
    log.info("--- Bitcoin Whale Wallets ---")
    for label, address in BTC_WHALE_WALLETS.items():
        result = fetch_btc_wallet(label, address)
        if result:
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO whale_wallets
                       (chain, label, address, timestamp, balance,
                        last_tx_value, last_tx_type, signal)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (result["chain"], result["label"], result["address"], ts,
                     result["balance"], result.get("last_tx_value"),
                     result.get("last_tx_type"), result["signal"]),
                )
                conn.commit()
        time.sleep(2)  # blockchain.info rate-limit

    # 3. Ethereum-Wallets
    log.info("--- Ethereum Whale Wallets ---")
    for label, address in ETH_WHALE_WALLETS.items():
        result = fetch_eth_wallet(label, address)
        if result:
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO whale_wallets
                       (chain, label, address, timestamp, balance,
                        last_tx_value, last_tx_type, signal)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (result["chain"], result["label"], result["address"], ts,
                     result["balance"], result.get("last_tx_value"),
                     result.get("last_tx_type"), result["signal"]),
                )
                conn.commit()
        time.sleep(1)


def get_whale_market_signal() -> str:
    """Zusammenfassung: Sind Whales insgesamt bullish oder bearish?"""
    with sqlite3.connect(DB_PATH) as conn:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='whale_wallets'"
        ).fetchone()
        if not has:
            return "NEUTRAL"
        rows = conn.execute(
            """SELECT signal FROM whale_wallets
               WHERE timestamp = (SELECT MAX(timestamp) FROM whale_wallets)"""
        ).fetchall()
    if not rows:
        return "NEUTRAL"
    signals   = [r[0] for r in rows]
    acc_count = signals.count("ACCUMULATING")
    dis_count = signals.count("DISTRIBUTING")
    if acc_count > dis_count + 1:  return "BULLISH"
    if dis_count > acc_count + 1:  return "BEARISH"
    return "NEUTRAL"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_whale_tracker()
    print(f"\nGesamtsignal: {get_whale_market_signal()}")
