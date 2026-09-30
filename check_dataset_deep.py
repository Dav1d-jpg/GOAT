"""Tieferer Check des Datasets bevor wir importieren."""
import csv
import io
from datasets import load_dataset

DATASET = "Gopher-Lab/Crypto_AltSeason_Sentiment_X_Twitter"

print(f"Lade 500 Zeilen von '{DATASET}' ...")
ds = load_dataset(DATASET, split="train", streaming=True)

raw_rows = []
for i, row in enumerate(ds):
    if i >= 500:
        break
    raw_rows.append(list(row.values())[0])

# CSV manuell parsen
parsed = []
for raw in raw_rows:
    try:
        reader = csv.reader(io.StringIO(raw))
        fields = next(reader)
        if len(fields) >= 15:
            parsed.append({
                "id":         fields[0],
                "content":    fields[1],
                "username":   fields[2],
                "created_at": fields[3],
                "score":      fields[14],
            })
    except:
        pass

print(f"Erfolgreich geparst: {len(parsed)}/500")

if parsed:
    scores = [r["score"] for r in parsed]
    unique_scores = set(scores)
    print(f"\nScore-Werte: {unique_scores}")
    print(f"Score-Verteilung: {', '.join(f'{s}={scores.count(s)}' for s in sorted(unique_scores))}")

    import datetime
    dates = []
    for r in parsed:
        try:
            dt = datetime.datetime.fromisoformat(r["created_at"].replace("Z",""))
            dates.append(dt)
        except:
            pass
    if dates:
        print(f"\nZeitraum: {min(dates).strftime('%d.%m.%Y')} bis {max(dates).strftime('%d.%m.%Y')}")

    print(f"\nBeispiel-Tweets:")
    for r in parsed[:3]:
        print(f"  [{r['score']}] {r['content'][:80]}")

    # Unique IDs?
    ids = [r["id"] for r in parsed]
    print(f"\nUnique IDs: {len(set(ids))}/{len(ids)}")
