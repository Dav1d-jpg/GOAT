import sqlite3
import pandas as pd

conn = sqlite3.connect('data/market_data.db')

df = pd.read_sql("SELECT * FROM features WHERE timeframe='1h' ORDER BY timestamp DESC LIMIT 500", conn)
print(f"Zeilen: {len(df)}, Spalten: {len(df.columns)}")
print()

nulls = df.isnull().sum()
total = len(df)
print("=== Leere / unvollständige Spalten ===")
for col, n in nulls[nulls > 0].items():
    print(f"  {col:<25} {n:>4}/{total} leer ({n/total*100:.0f}%)")

print()
print("=== Vollständige Spalten (keine Nulls) ===")
complete = [c for c in df.columns if nulls[c] == 0]
print(", ".join(complete))

# Welche Features nutzt das ML-Modell?
import pickle, os
model_path = "models/main.pkl"
if os.path.exists(model_path):
    with open(model_path, "rb") as f:
        bundle = pickle.load(f)
    feature_names = bundle.get("features", [])
    print(f"\n=== ML-Modell Features ({len(feature_names)}) ===")
    for i, f in enumerate(feature_names):
        null_count = df[f].isnull().sum() if f in df.columns else "NICHT IN DB"
        print(f"  [{i:2d}] {f:<25} Nulls: {null_count}")

conn.close()
