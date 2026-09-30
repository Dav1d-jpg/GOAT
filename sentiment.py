"""Sentiment-Analyse via Ollama (lokales LLM gemma3:4b)."""
import json
import logging
import re

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma3:4b"

log = logging.getLogger(__name__)

_PROMPT = """Analyze the sentiment of this cryptocurrency news headline.
Reply with ONLY valid JSON, no extra text:
{{"sentiment": "positive" or "negative" or "neutral", "score": number between 0.0 and 1.0, "reason": "one short sentence"}}

score means: 1.0 = very positive, 0.5 = neutral, 0.0 = very negative

Headline: {headline}"""


def analyze(headline: str) -> dict:
    """Sentiment-Analyse einer Schlagzeile. Gibt Dict mit sentiment/score/reason zurueck."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": _PROMPT.format(headline=headline), "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return {
                "sentiment": str(data.get("sentiment", "neutral")).lower(),
                "score":     float(data.get("score", 0.5)),
                "reason":    str(data.get("reason", "")),
            }
    except Exception as exc:
        log.warning("Sentiment-Fehler fuer '%s': %s", headline[:50], exc)
    return {"sentiment": "neutral", "score": 0.5, "reason": ""}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tests = [
        "Bitcoin surges to new all-time high as institutional demand grows",
        "Crypto exchange hacked, $200M in funds stolen",
        "Ethereum network upgrade proceeds without issues",
    ]
    for t in tests:
        result = analyze(t)
        print(f"{result['sentiment']:8} ({result['score']:.2f})  {t[:60]}")
