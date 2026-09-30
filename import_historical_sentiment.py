"""Historische Crypto-News von HuggingFace laden und mit RoBERTa scoren.

Dataset: SahandNZ/cryptonews-articles-with-price-momentum-labels
  ~144k Artikel mit Zeitstempeln von 2013 bis heute.

Laeuft einmalig, dauert je nach CPU ca. 30-60 Minuten.
"""
import logging
import time
from datetime import datetime

from db import get_connection, init_db
from sentiment_roberta import score_texts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATASET_NAME = "SahandNZ/cryptonews-articles-with-price-momentum-labels"
BATCH_SIZE   = 64    # RoBERTa Batch-Groesse
FLUSH_EVERY  = 500   # DB-Schreib-Intervall


def import_historical_sentiment(limit: int = 0) -> int:
    """Artikel von HuggingFace laden, mit RoBERTa scoren, in DB speichern.

    limit=0 bedeutet alle Artikel importieren.
    """
    try:
        from datasets import load_dataset
    except ImportError:
        log.error("datasets fehlt. Installiere mit: pip install datasets")
        return 0

    log.info("Lade Dataset '%s' (vollstaendig) ...", DATASET_NAME)
    try:
        ds = load_dataset(DATASET_NAME, split="train", streaming=False)
    except Exception as exc:
        log.error("Dataset-Ladefehler: %s", exc)
        return 0
    log.info("Dataset geladen: %d Artikel", len(ds))

    existing = set()
    log.info("Starte Import mit RoBERTa-Scoring (ueberschreibt bestehende Eintraege) ...")

    now      = int(time.time() * 1000)
    batch_texts: list[str] = []
    batch_meta:  list[dict] = []
    imported = 0
    skipped  = 0

    def flush(texts, meta):
        if not texts:
            return
        scores = score_texts(texts)
        rows = []
        for m, s in zip(meta, scores):
            rows.append((
                "HuggingFace-RoBERTa", m["title"], m["url"],
                m["published"], now, s["sentiment"], s["score"], None
            ))
        with get_connection() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO news_sentiment
                   (source, title, url, published, fetched_at, sentiment, score, summary)
                   VALUES (?,?,?,?,?,?,?,?)""",
                rows,
            )
            conn.commit()

    for row in ds:
        try:
            text = str(row.get("text") or row.get("title") or "").strip()[:300]
            if not text:
                continue
            url = str(row.get("url") or f"hf_roberta_{imported}")
            if url in existing:
                skipped += 1
                continue

            raw_ts = row.get("datetime") or row.get("date") or row.get("published")
            try:
                if isinstance(raw_ts, str):
                    published = int(datetime.fromisoformat(raw_ts.replace("Z", "")).timestamp() * 1000)
                elif raw_ts is not None:
                    published = int(float(str(raw_ts)) * 1000)
                else:
                    published = now
            except Exception:
                published = now

            batch_texts.append(text)
            batch_meta.append({"title": text, "url": url, "published": published})

            if len(batch_texts) >= BATCH_SIZE:
                flush(batch_texts, batch_meta)
                imported += len(batch_texts)
                batch_texts.clear()
                batch_meta.clear()

            if imported % FLUSH_EVERY == 0 and imported > 0:
                log.info("Fortschritt: %d Artikel importiert, %d uebersprungen", imported, skipped)

            if limit and imported >= limit:
                break

        except Exception as exc:
            log.debug("Zeile uebersprungen: %s", exc)

    # Letzten Batch
    flush(batch_texts, batch_meta)
    imported += len(batch_texts)

    log.info("Fertig: %d neue Artikel importiert, %d Duplikate uebersprungen", imported, skipped)
    return imported


if __name__ == "__main__":
    init_db()
    start = time.time()
    total = import_historical_sentiment()
    elapsed = time.time() - start
    log.info("Laufzeit: %.1f Minuten fuer %d Artikel", elapsed / 60, total)
    log.info("Naechster Schritt: python model.py")
