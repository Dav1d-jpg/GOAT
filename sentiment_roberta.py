"""Schneller Sentiment-Scorer via cardiffnlp/twitter-roberta-base-sentiment.

Ersetzt Ollama fuer historische Batch-Analyse.
Live-Scoring im Scheduler laeuft weiterhin ueber Ollama (sentiment.py).
"""
import logging
from functools import lru_cache

log = logging.getLogger(__name__)

MODEL_NAME = "cardiffnlp/twitter-roberta-base-sentiment-latest"


@lru_cache(maxsize=1)
def _load_pipeline():
    from transformers import pipeline
    log.info("Lade RoBERTa-Modell '%s' (einmalig) ...", MODEL_NAME)
    p = pipeline(
        "sentiment-analysis",
        model=MODEL_NAME,
        tokenizer=MODEL_NAME,
        max_length=128,
        truncation=True,
        device=-1,  # CPU
    )
    log.info("RoBERTa geladen.")
    return p


def score_texts(texts: list[str]) -> list[dict]:
    """Sentiment-Scores fuer eine Liste von Texten berechnen.

    Gibt Liste von {"sentiment": str, "score": float} zurueck.
    score: 0.0 (sehr negativ) bis 1.0 (sehr positiv)
    """
    pipe = _load_pipeline()
    results = []
    batch_size = 32

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            outputs = pipe(batch)
            for out in outputs:
                label = out["label"].lower()  # negative / neutral / positive
                conf  = out["score"]          # Konfidenz 0-1

                if label == "positive":
                    score = 0.5 + conf * 0.5      # 0.5 – 1.0
                    sentiment = "positive"
                elif label == "negative":
                    score = 0.5 - conf * 0.5      # 0.0 – 0.5
                    sentiment = "negative"
                else:
                    score = 0.5
                    sentiment = "neutral"

                results.append({"sentiment": sentiment, "score": round(score, 4)})
        except Exception as exc:
            log.warning("RoBERTa Batch-Fehler: %s", exc)
            results.extend([{"sentiment": "neutral", "score": 0.5}] * len(batch))

    return results


def score_single(text: str) -> dict:
    return score_texts([text])[0]
