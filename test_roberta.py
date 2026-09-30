from sentiment_roberta import score_texts
tests = [
    "Bitcoin hits new all-time high as institutional buying surges",
    "Crypto exchange hacked, millions stolen",
    "Bitcoin price stable today",
]
results = score_texts(tests)
for text, r in zip(tests, results):
    print(f"  {r['sentiment']:8s} {r['score']:.2f}  {text[:60]}")
print("RoBERTa funktioniert!")
