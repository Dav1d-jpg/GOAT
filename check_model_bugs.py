import sqlite3
import numpy as np
conn = sqlite3.connect('data/market_data.db')

# 1. Whale-Tabelle
has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='whale_signals'").fetchone()
print("whale_signals:", "vorhanden" if has else "FEHLT")
if has:
    count = conn.execute("SELECT COUNT(*) FROM whale_signals").fetchone()[0]
    print(f"  Zeilen: {count}")

# 2. Was fehlt in _build_feature_vector vs. FEATURE_COLS?
from model_data import FEATURE_COLS
import pickle
with open("models/main.pkl", "rb") as f:
    bundle = pickle.load(f)
model_features = bundle.get("features", [])

# Was wird in _latest_feature_row geladen?
loaded_in_query = [
    "timestamp","close","rsi_14","rsi_7","macd","macd_signal","macd_hist",
    "bb_pct_b","ema_9","ema_21","ema_50","atr_14","vol_ratio",
    "ret_1","ret_3","ret_6","body_size","wick_up","wick_down"
]
# Was wird in value_map gebaut?
in_value_map = [
    "rsi_14","rsi_7","macd","macd_signal","macd_hist","bb_pct_b",
    "ema_9","ema_21","ema_50","atr_14","vol_ratio",
    "ret_1","ret_3","ret_6","body_size","wick_up","wick_down",
    "sent_score_24h","sent_score_6h","sent_pos_ratio",
    "whale_tx_24h","whale_fee_usd","whale_vol_anomaly","whale_bid_wall","whale_ask_wall",
    "symbol_id",
]

print("\n=== Features die im Live-Signal NaN sind (im Modell aber vorhanden) ===")
for i, f in enumerate(model_features):
    if f not in in_value_map:
        print(f"  [{i:2d}] {f}  <- NaN bei live prediction")

conn.close()
