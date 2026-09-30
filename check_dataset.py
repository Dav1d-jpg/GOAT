"""Schnell-Check eines HuggingFace Datasets bevor wir importieren."""
from datasets import load_dataset
import sys

DATASET = sys.argv[1] if len(sys.argv) > 1 else "ElKulako/cryptobert"

print(f"Lade erste 20 Zeilen von '{DATASET}' ...")
try:
    ds = load_dataset(DATASET, split="train", streaming=True)
    rows = []
    for i, row in enumerate(ds):
        if i >= 20:
            break
        rows.append(row)

    print(f"\nSpalten: {list(rows[0].keys())}")
    print(f"\nErste 5 Zeilen:")
    for r in rows[:5]:
        print(f"  {r}")

    # Prüfungen
    has_text = any(k in rows[0] for k in ['text', 'title', 'content', 'tweet'])
    has_date = any(k in rows[0] for k in ['date', 'datetime', 'timestamp', 'created_at', 'published'])
    has_label = any(k in rows[0] for k in ['label', 'sentiment', 'score'])

    # URL-Duplikate?
    urls = [str(r.get('url', r.get('id', i))) for i, r in enumerate(rows)]
    unique_urls = len(set(urls))

    print(f"\n=== Qualitäts-Check ===")
    print(f"Hat Text:       {'JA' if has_text else 'NEIN'}")
    print(f"Hat Zeitstempel:{'JA' if has_date else 'NEIN'} <- wichtig fuer Training")
    print(f"Hat Labels:     {'JA' if has_label else 'NEIN'} <- kein RoBERTa nötig wenn ja")
    print(f"URL-Einzigartigkeit: {unique_urls}/20")

except Exception as e:
    print(f"Fehler: {e}")
