"""News-Scraper: RSS-Feeds holen und Sentiment via Ollama analysieren."""
import logging
import time
from email.utils import parsedate_to_datetime

import feedparser

from db import get_connection, init_db
from sentiment import analyze

log = logging.getLogger(__name__)

RSS_FEEDS: dict[str, str] = {
    # Tier 1 – große etablierte Krypto-Medien
    "CoinDesk":          "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph":     "https://cointelegraph.com/rss",
    "Decrypt":           "https://decrypt.co/feed",
    "Bitcoin Magazine":  "https://bitcoinmagazine.com/feed",
    "The Block":         "https://www.theblock.co/rss.xml",
    "Blockworks":        "https://blockworks.co/feed",
    # Tier 2 – solide Krypto-News
    "CryptoSlate":       "https://cryptoslate.com/feed/",
    "Bitcoinist":        "https://bitcoinist.com/feed/",
    "BeInCrypto":        "https://beincrypto.com/feed/",
    "NewsBTC":           "https://www.newsbtc.com/feed/",
    "AMBCrypto":         "https://ambcrypto.com/feed/",
    "CryptoNews":        "https://cryptonews.com/news/feed/",
    "CoinGape":          "https://coingape.com/feed/",
    "CryptoPotato":      "https://cryptopotato.com/feed/",
    "CoinJournal":       "https://coinjournal.net/feed/",
    "UToday":            "https://u.today/rss",
    "Crypto Briefing":   "https://cryptobriefing.com/feed/",
    "The Defiant":       "https://thedefiant.io/feed",
    "DailyCoin":         "https://dailycoin.com/feed/",
}

REDDIT_FEEDS: dict[str, str] = {
    "Reddit-CryptoCurrency": "https://www.reddit.com/r/CryptoCurrency/hot/.rss",
    "Reddit-Bitcoin":        "https://www.reddit.com/r/Bitcoin/hot/.rss",
    "Reddit-CryptoMarkets":  "https://www.reddit.com/r/CryptoMarkets/hot/.rss",
}

MAX_PER_FEED      = 10   # neueste Artikel pro Quelle
MAX_REDDIT_FEED   = 25   # Reddit hat mehr Posts, mehr = mehr Signal
OLLAMA_DELAY      = 1.5  # Sekunden zwischen Ollama-Anfragen
MAX_NEW_PER_CYCLE = 60   # Obergrenze pro Durchlauf – der News-Zyklus blockiert
                         # den Scheduler (inkl. Stop-Loss-Checks); nach laengerem
                         # Stillstand wuerde er sonst stundenlang haengen


def _parse_published(entry) -> int:
    """Veroeffentlichungszeitpunkt als Unix-Millisekunden parsen."""
    try:
        dt = parsedate_to_datetime(entry.get("published", ""))
        return int(dt.timestamp() * 1000)
    except Exception:
        return int(time.time() * 1000)


def fetch_headlines(source: str, url: str, max_items: int = MAX_PER_FEED) -> list[dict]:
    """RSS-Feed laden und Schlagzeilen als Liste zurueckgeben."""
    try:
        feed = feedparser.parse(url)
        result = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            if not title:
                continue
            result.append({
                "source":    source,
                "title":     title,
                "url":       entry.get("link", ""),
                "published": _parse_published(entry),
            })
        log.info("%d Headlines geladen von %s", len(result), source)
        return result
    except Exception as exc:
        log.warning("RSS-Fehler (%s): %s", source, exc)
        return []


def scrape_and_analyze() -> int:
    """Alle Feeds scrapen, Sentiment analysieren, neue Artikel in DB speichern.

    Gibt Anzahl neu gespeicherter Artikel zurueck.
    """
    fetched_at = int(time.time() * 1000)
    new_count = 0

    all_feeds = list(RSS_FEEDS.items()) + list(REDDIT_FEEDS.items())

    with get_connection() as conn:
        for source, url in all_feeds:
            if new_count >= MAX_NEW_PER_CYCLE:
                log.info("Limit von %d neuen Artikeln erreicht – Rest im naechsten Zyklus",
                         MAX_NEW_PER_CYCLE)
                break
            max_items = MAX_REDDIT_FEED if source.startswith("Reddit") else MAX_PER_FEED
            for article in fetch_headlines(source, url, max_items):
                if new_count >= MAX_NEW_PER_CYCLE:
                    break
                # Duplikat-Check per URL
                if conn.execute(
                    "SELECT 1 FROM news_sentiment WHERE url = ?", (article["url"],)
                ).fetchone():
                    continue

                log.info("Analysiere: %s", article["title"][:70])
                result = analyze(article["title"])

                conn.execute(
                    """INSERT OR IGNORE INTO news_sentiment
                       (source, title, url, published, fetched_at, sentiment, score, summary)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        article["source"], article["title"], article["url"],
                        article["published"], fetched_at,
                        result["sentiment"], result["score"], result["reason"],
                    ),
                )
                conn.commit()
                new_count += 1
                time.sleep(OLLAMA_DELAY)

    log.info("News-Scraper abgeschlossen: %d neue Artikel", new_count)
    return new_count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    scrape_and_analyze()
