# Crypto Trading Bot

Lern-Projekt: ein Krypto-Trading-Bot mit ML, Sentiment-Analyse (lokales LLM)
und Whale-Tracking. Ziel ist primaer Lernen + ein sauberes, lauffaehiges System.
Echtes Geld erst NACH erfolgreichem Paper-Trading.

## Tech-Stack
- Python 3.14, virtuelle Umgebung (venv)
- ollama  – lokales LLM, Modell `gemma3:4b`, API auf http://localhost:11434
- ccxt    – einheitliche Anbindung an Krypto-Boersen (Kraken)
- pandas / numpy   – Datenverarbeitung
- scikit-learn     – ML (Random Forest, Walk-Forward-Validierung)
- torch + transformers – RoBERTa Sentiment (cardiffnlp/twitter-roberta-base-sentiment-latest)
- sqlite3          – lokale Datenbank (data/market_data.db)
- requests / beautifulsoup4 / feedparser – Web Scraping / RSS
- streamlit + plotly – Dashboard (localhost:8501 + Tailscale 100.105.138.6:8501)
- datasets         – HuggingFace Dataset Import

## Befehle
- `venv\Scripts\activate`                  – venv aktivieren
- `python scheduler.py`                    – Daten-Sammler + Trading starten (laeuft dauerhaft)
- `streamlit run dashboard.py`             – Dashboard starten
- `python model.py`                        – ML-Modell neu trainieren (manuell)
- `python check_status.py`                 – Bot-Status pruefen
- `python final_health_check.py`           – Vollstaendiger System-Check
- `python reset_paper_trading.py`          – Paper-Trading zuruecksetzen (Trades, Portfolio, Shorts, Cooldowns, Balance)
- `start_bot.bat`                          – Scheduler + Dashboard auf einmal starten (sichtbare Fenster)
- `python check_buy_precision.py`          – Long/Short-Edge an der Schwelle messen (Holdout)
- `python backtest.py`                     – Strategie-Backtest (Out-of-Sample, ~75s)
- `python backtest_experiments.py`         – Parameter-Varianten vergleichen
- `ollama list`                            – pruefen ob gemma3:4b da ist

## Watchdog (seit 2026-06-11)
- Windows Task Scheduler Job "GOAT Watchdog" laeuft alle 5 Minuten und startet
  Scheduler + Dashboard (versteckt) neu, falls sie nicht laufen (watchdog.ps1)
- Logs der versteckten Prozesse: scheduler.log / dashboard.log (werden bei Neustart ueberschrieben)
- **Bot absichtlich stoppen**: `STOP_BOT.txt` im Projektordner anlegen, dann Prozesse beenden
- **Bot wieder freigeben**: STOP_BOT.txt loeschen (Watchdog startet binnen 5 Min neu)
- **Neustart nach Code-Aenderung**: Prozesse beenden – Watchdog startet sie mit neuem Code
- Task entfernen: `schtasks /Delete /F /TN "GOAT Watchdog"`

## Aktueller Stand (Stand 2026-06-04)

### Fertig und lauffaehig
- [x] Daten-Schicht: ccxt Kraken, 1min Live-Preise, 5min + 1h OHLCV, 25 EUR-Paare
- [x] Sentiment: Ollama/gemma3:4b + 19 RSS-Feeds (CoinDesk, Cointelegraph, Blockworks, etc.)
- [x] Whale-Tracker: Blockchair On-Chain + BTC/ETH Wallets (Etherscan API)
- [x] Fear & Greed Index: alternative.me API, historisch + live
- [x] Funding Rates: Binance Futures API, historisch + live
- [x] ML-Modell: Random Forest, 40 Features, Walk-Forward-Validierung
- [x] RoBERTa: cardiffnlp/twitter-roberta-base-sentiment-latest installiert (torch CPU)
- [x] Paper-Trading: 1000 EUR Start, saubere Trading-Logik (siehe unten)
- [x] Dashboard: 6 Tabs, Auto-Refresh, mobiler Zugriff via Tailscale
- [x] Telegram: Kauf/Verkauf sofort, Portfolio alle 2h, /portfolio Command
- [x] Market Regime Detection: BULL/BEAR/SIDEWAYS (EMA, Fear&Greed, BTC-Dominanz)
- [x] Trailing Stop-Loss: 3% unter Hoechststand

## Aktuelle Trading-Konfiguration (paper_trader.py)

```python
MIN_CONFIDENCE_BUY  = 0.68   # Backtest 2026-06-11: 0.62->0.68 (weniger, bessere Trades)
SELL_THRESHOLD      = 0.40   # Verkauf bei ML < 40%
STOP_LOSS_PCT       = 0.30   # Reine Katastrophen-Notbremse. Backtest 2026-06-12:
                             # JEDER fixe Stop schadet Return UND Drawdown
                             # (0.15/0.20/0.25 alle schlechter als gar kein Stop).
                             # Exit-Management: Trailing-Stop + ML-Signal.
TAKE_PROFIT_PCT     = 0.08   # +8% Take-Profit
TRAILING_STOP_PCT   = 0.03   # 3% Trailing Stop
MAX_POSITIONS       = 5      # max 5 gleichzeitige Positionen
TOP_BUYS_PER_CYCLE  = 1      # 1 Kauf pro Stunde
POSITION_SIZE_PCT   = 0.06   # 6% des Kapitals pro Trade
MIN_HOLD_HOURS      = 3      # mind. 3h halten bevor Panic-Sell greift
PANIC_COOLDOWN_H    = 2      # 2h Pause nach Panic-Sell
STOPLOSS_COOLDOWN_H = 24     # 24h Sperre pro Symbol nach Stop-Loss/Trailing-Stop
ML_LOSS_COOLDOWN_H  = 6      # 6h Sperre nach ML_SIGNAL-Verlust
MAX_SIGNAL_AGE_MS   = 3h     # veraltete Signale werden ignoriert (Frozen-Symbol-Schutz)
MAX_PRICE_AGE_MS    = 3h     # veraltete Preise = Symbol wird nicht gehandelt
BLACKLIST = {ZEC, USDC, USDT, EURC, PAXG}  # kein Edge / Stablecoins / Gold-Token

# Regime-abgestufte Kaeufe (seit 2026-06-11, statt Alles-oder-Nichts):
#   BULL     -> kaufen ab P(up) 0.62, Positionsgroesse 6%
#   SIDEWAYS -> kaufen ab P(up) 0.68, Positionsgroesse 3%
#   BEAR     -> keine Longs, stattdessen Paper-Shorts (siehe unten)
SIDEWAYS_MIN_CONFIDENCE = 0.68
SIDEWAYS_SIZE_PCT       = 0.03

# Paper-Shorts (seit 2026-06-11, NUR simuliert; seit 2026-06-18 REGIME-UNABHAENGIG):
ENABLE_SHORTS         = True
# Shorts laufen jetzt in ALLEN Regimes, gesteuert vom Modell (prob_down>=0.70),
# NICHT mehr nur in BEAR. Grund: Regime-Detektor (langsamer BTC-EMA) hinkt nach,
# labelte kippende Maerkte als BULL -> blockierte Shorts -> Bot handelte tagelang
# nicht (18.06.). Der prob_down-Filter ist selbst-schuetzend (echter Bulle = Modell
# nicht bearish = keine Shorts). Backtest robust ueber 4 Splits: nie schlechter,
# 2x +7pp, Drawdown stets <= vorher. Neue Live-Config: +1.7% / PF 1.07 / DD -9.8%.
SHORT_MIN_CONFIDENCE  = 0.70   # Backtest 2026-06-11: 0.65->0.70
SHORT_SIZE_PCT        = 0.05   # Margin = 5% des Cash
MAX_SHORTS            = 4       # Backtest 2026-06-12: 2->4 besser auf allen Metriken
                               # (Return -0.0->+3.0%, Sharpe +0.05->+0.20). Nicht 5-6
                               # wegen baerenlastigem Test + Squeeze-Korrelation.
SHORT_STOP_LOSS_PCT   = 0.04   # Cover wenn Kurs +4% UEBER Einstieg
SHORT_TAKE_PROFIT_PCT = 0.06   # Cover wenn Kurs -6% UNTER Einstieg
# Begruendung: Edge gemessen (check_buy_precision.py) – P(down)>=0.65 trifft
# 41.5% vs. 27.1% Basisrate auf Holdout. Tabellen: paper_shorts, Trades als
# action='SHORT'/'COVER' in paper_trades. Zweites XGB-Modell (model_down im pkl).
```

**WICHTIG**: Nach JEDER Aenderung an paper_trader.py / scheduler.py muss der
Scheduler-Prozess NEU GESTARTET werden – Python laedt geaenderte Module nicht nach!
(Im Juni liefen Config-Aenderungen tagelang nie, weil der alte Prozess weiterlief.)

### Wichtige Logik-Entscheidungen
- **FAST_NEWS_BULLISH Kaeufe: DEAKTIVIERT** – verursachte Buy-High/Sell-Low Whipsaw
- **Panic-Sell nur nach 3h Haltezeit** – verhindert sofortigen Verkauf bei frischen Positionen
- **Kein Whale-Boost** – 53% Schwelle war zu nah an Zufallsniveau, entfernt
- **Nur ML_SIGNAL Kaeufe** im stundlichen Zyklus – sauberer, weniger Trades aber besser
- **Stop-Loss Cooldown 12h** – nach Stop-Loss/Trailing-Stop dasselbe Symbol 12h nicht kaufen
  Grund: Bot kaufte gestoppte fallende Coins sofort wieder → NEAR 3x gestoppt an einem Tag

## Bekannte Bugs (behoben)

### Kritisch behoben
1. **`symbol` nicht an `_build_feature_vector` uebergeben** (model.py)
   - Whale-Features bekamen immer leeren String statt echten Symbol-Namen
   - Folge: Whale-Features immer NaN → Signale clusterten bei ~50% → Bot handelte 41h nicht
   - Fix: `symbol` Parameter zu `_build_feature_vector` hinzugefuegt

2. **14 Features NaN bei Live-Vorhersage** (model.py)
   - `_latest_feature_row` lud Binary-Features (rsi_oversold, golden_cross etc.) nicht
   - `fear_greed` und `funding_rate` fehlten im value_map
   - Fix: Alle 40 Features werden jetzt korrekt befuellt

3. **FAST_NEWS_PANIC mit 1 Artikel moeglich** (paper_trader.py)
   - Ein einziger negativer Artikel (SUI Bug-News) loeste Massenverkauf aus
   - Fix: Mindestens 5 Artikel + Score < 0.12 fuer PANIC

4. **Doppelte `run_extra_features()` Definition** (extra_features.py)
   - Fix: Duplikat entfernt

5. **RSI-Bedingung bei Panic-Sell falsch** (paper_trader.py)
   - `rsi > 25` statt `rsi > 30` – logisch invertiert
   - Fix: Korrigiert

### Kleinere Bugs behoben
- Division by Zero bei avg_buy_price (paper_trader.py) – Guard hinzugefuegt
- Division by Zero bei ATR-Berechnung (market_regime.py) – Guard hinzugefuegt
- Sentiment-Fallback 0.5 statt NaN bei leerem Dataset (model_data.py) – auf NaN geaendert
- Trade-Zaehler zaehlte BUY+SELL statt nur abgeschlossene Trades (dashboard.py, scheduler.py)
- Whale-Query nutzte falsches Symbol als Fallback (model.py)

### Fixes vom 2026-06-03/04
6. **`funding_rate` Feature immer NaN** (model.py:222)
   - `funding_rates.get(row.get("symbol",""), np.nan)` – row hat keinen "symbol"-Key
   - Fix: `funding_rates.get(symbol, np.nan)` – direkt den uebergebenen Parameter nutzen

7. **Trailing Stop nicht geloescht beim Verkauf** (paper_trader.py)
   - Alter trailing_stops Eintrag blieb nach Verkauf in DB → beeinflusste Folgekauf
   - Fix: DELETE FROM trailing_stops bei _execute_sell hinzugefuegt

8. **Market Regime nie aktualisiert** (scheduler.py)
   - detect_regime() wurde nie vom Scheduler aufgerufen → Regime 50h alt, BEAR 67% blockierte alle Kaeufe
   - Fix: detect_regime() in den 1h-Loop des Schedulers eingebaut

9. **Stop-Loss Cooldown fehlte** (paper_trader.py) – NEU 2026-06-04
   - Bot kaufte nach Stop-Loss dasselbe Symbol sofort wieder (NEAR 3x an einem Tag: -10 EUR)
   - Fix: stoploss_cooldown Tabelle + 12h Sperre nach STOP_LOSS oder TRAILING_STOP

10. **Walk-Forward zu viele Folds bei grossem Datensatz** (model.py)
    - 607k Zeilen × test_size=50 = 12.148 Folds → haette Stunden gedauert
    - Fix: max_folds=30 Parameter, test_size wird automatisch angepasst

### Fixes vom 2026-06-11 (Komplett-Review)
11. **Zombie-Positionen / eingefrorene Preise** (data_fetcher.py, scheduler.py, paper_trader.py) – KRITISCH
    - Symbol-Universum = Top-20 nach Volumen, rotiert stuendlich. Coins die rausfallen
      bekamen KEINE neuen Daten mehr → Preis eingefroren → Stop-Loss konnte NIE ausloesen
    - Folge: LINK 5x zum identischen Phantom-Preis 6.3757 gekauft/verkauft (nur Gebuehren
      verbrannt), ALGO/ICP als Zombie-Positionen tagelang gehalten
    - Fix 3-fach: (a) Scheduler beobachtet Portfolio-Symbole IMMER mit (get_portfolio_symbols),
      (b) _load_signals ignoriert Signale aelter 3h, (c) _get_latest_price liefert None bei
      Preisen aelter 3h → kein Handel auf eingefrorenen Daten
12. **symbol_id falsch bei Live-Vorhersage** (model.py) – KRITISCH
    - Training: symbol_id alphabetisch (ADA=0, ALGO=1, …); Inferenz: Reihenfolge nach
      Zeitstempel (ETH=0, BTC=1, …) → JEDE Live-Vorhersage bekam falsche symbol_id
    - Fix: Training-Mapping wird im pkl gespeichert und bei Inferenz verwendet
13. **Unfertige Kerzen eingefroren** (data_fetcher.py)
    - INSERT OR IGNORE speicherte die laufende (halbfertige) 1h-Kerze und
      aktualisierte sie nie → korrupte Schlusskurse in OHLCV + Features
    - Fix: INSERT OR REPLACE – naechster Fetch ueberschreibt mit finalen Werten
14. **Walk-Forward-Validierung wertlos** (model.py)
    - train_size=300 bei 610k Zeilen: 300 Zeilen Training gegen 17k Zeilen Test
    - Fix: train_size=min(100k, n/2) → endlich aussagekraeftige Validierung
15. **Target ignorierte Gebuehren** (model_data.py) – Haupt-Hebel fuer Profitabilitaet
    - Target war "steigt um irgendein Epsilon" → Signale systematisch unprofitabel
      nach 0.52% Round-Trip-Gebuehren
    - Fix: MIN_TARGET_MOVE=0.006 – Target ist jetzt "steigt um >0.6% in 3h";
      scale_pos_weight balanciert die Klassen (27.6% UP-Basisrate)
    - Gemessen (check_buy_precision.py, Holdout letzte 20%): bei Schwelle 0.62
      Trefferquote 41.2% vs. 26.8% Basisrate (+14.3pp Lift)
16. **Target-NaN am Datenende wurde 0** (model_data.py)
    - NaN > x ergibt False → letzte 3 Zeilen je Symbol bekamen falsche 0-Targets
    - Fix: Zeilen ohne Zukunftskurs explizit auf NA
17. **Stablecoins handelbar** (model_data.py, paper_trader.py)
    - USDC/USDT/EURC/PAXG waren teils im Universum → reine Gebuehren-Verbrennung
    - Fix: ueberall ausgeschlossen (Blacklist + _get_symbols)
18. **News-Zyklus konnte Scheduler stundenlang blockieren** (news_scraper.py)
    - Nach Stillstand hunderte neue Artikel × ~5s Ollama = Stop-Loss-Checks blockiert
    - Fix: MAX_NEW_PER_CYCLE=60, Rest im naechsten Zyklus; News-Intervall auf 15 Min

### Erweiterungen vom 2026-06-11 (Abend)
19. **Watchdog** – Task Scheduler Job "GOAT Watchdog" (alle 5 Min, watchdog.ps1,
    Kill-Switch via STOP_BOT.txt). Bot ueberlebt jetzt Reboots und Crashes.
20. **Regime-Abstufung statt Komplett-Sperre** (paper_trader.py)
    - Vorher: nur BULL kauft -> in BEAR/SIDEWAYS wochenlang NULL Trades,
      Win-Rate-Validierung unmoeglich
    - Jetzt: SIDEWAYS kauft mit strengerer Schwelle (0.68) und halber Groesse (3%)
21. **Paper-Shorts im BEAR-Regime** (model_data.py, model.py, paper_trader.py, dashboard.py)
    - Zweites XGB-Modell auf target_down (faellt >0.6% in 3h), prob_down in signals-Tabelle
    - Shorts nur bei P(down)>=0.65, max 2, Margin 5%, SL +4% / TP -6%,
      Live-Squeeze-Schutz im 1-Min-Loop, gleiche Cooldowns wie Longs
    - Win-Rate/Trades zaehlen SELL+COVER (scheduler.py, dashboard.py)
    - Short-Mathematik mit In-Memory-DB-Tests verifiziert
22. **Backtester** (backtest.py) – Out-of-Sample-Simulation der exakten Bot-Regeln
    - Chronologischer Split (Train 70% / Test 30%), Stops gegen High/Low,
      konservativ (Stop vor TP in derselben Kerze), Gebuehren wie live
    - Ergebnis nach data/backtest_result.json, Anzeige im Dashboard-Tab "Backtest"
    - BEFUND (2026-06-11, Zeitraum 2024-05 bis 2026-06, BTC B&H -13.1%):
        Baseline (alte Live-Config)        -42.9%  WR 47.6%  PF 0.80  1914 Trades
        A: nur Trailing (kein fixer SL)    -30.3%  WR 55.2%  PF 0.83
        B: weiter SL 8%                    -37.9%  WR 53.6%  PF 0.82
        C: Schwellen 0.70/0.70             -17.7%  WR 46.9%  PF 0.82
        D: Trailing-only + 0.68/0.70       -15.7%  WR 59.7%  PF 0.85  655 Trades
        E: SL 8% + 0.68/0.70 + ShortSL 6%  -25.5%  WR 57.1%  PF 0.78
    - KONSEQUENZ: Variante D in Live-Config uebernommen (BUY 0.68, SHORT 0.70,
      SL 0.15 als reiner Katastrophen-Schutz). ABER: ALLE Varianten negativ ->
      der reine OHLCV-Edge reicht nicht. Groesster Hebel: Live-Sentiment sammeln
      und nach 4-6 Wochen retrainen, dann erneut backtesten.
    - WICHTIG: Nicht weiter Parameter fischen bis der Backtest gruen ist –
      das waere Overfitting auf die Vergangenheit.
23. **Dashboard-Redesign** (dashboard.py, .streamlit/config.toml)
    - Minimalistisch-futuristisch: cremefarbener Hintergrund (#f1ecdf), feines
      Punktraster, Space Grotesk + IBM Plex Mono, gedeckte Farben (nachttauglich)
    - KENNZAHLEN ALS LED-PUNKTMATRIX (5x7-Punktschrift, eigene led()-Funktion in
      dashboard.py): Zahlen aus Punkten die rot/gruen glimmen, inaktive Punkte
      bleiben als Schatten sichtbar (Flip-Dot-Optik); auch Uhr im Header
    - Header: GOAT-Wortmarke + live/offline-Puls, Regime- und F&G-Pille
    - Neuer Tab "Backtest" (Parameter, LED-Board, Equity, Drawdown, Exit-Gruende)
    - Long/Short-Positionen nebeneinander, Trade-Historie im Expander
    - Headless verifiziert via streamlit.testing AppTest (0 Exceptions)

### Feature-Erweiterung vom 2026-06-12 (DURCHBRUCH: erster profitabler Backtest)
24. **9 neue Features** (features.py, model_data.py, model.py) – 40 -> 49 Features
    - Hoehere-Zeitebenen-Trend: mom_24 (1d), mom_72 (3d) Momentum (features.py)
    - Saisonalitaet: hour_sin/cos, dow_sin/cos zyklisch kodiert (features.py)
    - Cross-Asset: btc_ret_1, btc_ret_6 + rel_strength_6 (Outperformance ggü. BTC).
      In build_dataset per merge_asof; bei Inferenz via model._btc_context() –
      IDENTISCHE Definition Train/Inferenz (sonst Drift-Bug wie symbol_id).
    - mom_24, rel_strength_6, dow_sin landen in den Top-12 Feature-Importances.
    - Features per ALTER TABLE migriert + alle ~680k Zeilen neu berechnet.
    - EFFEKT im Backtest (selber Zeitraum, neue vs. alte Features):
        D alt (Trailing 0.68/0.70)         -15.7%  WR 59.7%  PF 0.85  Sharpe -0.77
        N1 neu (SL 0.15)                    -7.5%  WR 60.0%  PF 1.01  Sharpe -0.38
        N2 neu (Trailing-only)              +2.2%  WR 60.2%  PF 1.11  Sharpe +0.16  <- ERSTER PROFIT
        N3 neu (Trailing + Vola-Sizing)     -0.6%  WR 60.2%  PF 1.09  (verworfen)
    - Fixer Stop schadet monoton (0.15->-7.5, 0.20->-5.0, 0.25->-0.9, kein->+2.2)
      UND verschlechtert den Drawdown -> Live STOP_LOSS_PCT auf 0.30 (nur Notbremse).
    - Vola-Sizing NICHT uebernommen (kostet Return+Sharpe). Im Backtester als
      vol_target-Parameter messbar geblieben.
    - +18 Prozentpunkte allein durch die neuen Features. Schlaegt BTC B&H (-13%).

## News-Quellen (19 aktiv, news_scraper.py)
CoinDesk, Cointelegraph, Decrypt, Bitcoin Magazine, The Block, Blockworks,
CryptoSlate, Bitcoinist, BeInCrypto, NewsBTC, AMBCrypto, CryptoNews,
CoinGape, CryptoPotato, CoinJournal, UToday, Crypto Briefing, The Defiant, DailyCoin

## ML-Modell Details
- **Features**: 49 (OHLCV-Indikatoren + Sentiment 24h/6h + Whale + Fear&Greed + Funding Rates
  + seit 2026-06-12: mom_24/mom_72 Hoehere-Zeitebenen-Trend, hour/dow sin/cos Saisonalitaet,
  btc_ret_1/btc_ret_6 + rel_strength_6 Cross-Asset zu BTC). mom_24, rel_strength_6, dow_sin
  landen direkt in den Top-12 nach Feature-Importance.
- **Trainingsdaten**: 609.846 Zeilen 1h-OHLCV seit 2019-09-23 (6,5 Jahre)
  - 21.538 Zeilen von Kraken (live, EUR-Preise, seit Mai 2026)
  - 588.308 Zeilen von HuggingFace zongowo111/v2-crypto-ohlcv-data (USDT-Preise, historisch)
  - Importiert mit: python import_ohlcv_history.py
  - Fuer ML-Features irrelevant ob USDT oder EUR (alle Features sind relativ: RSI, Returns etc.)
- **Target (seit 2026-06-11)**: Kurs steigt um >0.6% in 3h (gebuehren-bewusst,
  MIN_TARGET_MOVE in model_data.py). Basisrate ~27.6% UP – prob_up ist via
  scale_pos_weight rebalanciert, Genauigkeit allein ist NICHT mehr die richtige Metrik!
- **Relevante Metrik**: Praezision an der Kaufschwelle → `python check_buy_precision.py`
  Stand 2026-06-11: 41.2% Trefferquote bei Schwelle 0.62 vs. 26.8% Basisrate
- **Walk-Forward**: 58.4% (±6.5%) ueber 30 Folds (train=100k) – wegen Rebalancing
  nicht mit der alten 51.7%-Zahl vergleichbar
- **Problem**: Historische Daten haben kein Sentiment/Whale (NaN fuer ~95%) → Modell nutzt nur OHLCV
- **Loesung**: Live-Sentiment akkumulieren, nach 4-6 Wochen neu trainieren
- **Retrain**: Manuell mit `python model.py` – niemals auto-retrain (danach Scheduler neu starten!)

## Paper-Trading Ergebnisse (Stand 2026-06-04)
- **Start**: 1000 EUR am 2026-06-02
- **Aktuell**: ~966 EUR (ca. -34 EUR / -3.4%)
- **Win-Rate**: 25% (3 Wins, 9 Losses) – hauptsaechlich wegen Stop-Loss Cooldown Bug
- **Groesste Wins**: HYPE/EUR +7.95 EUR (Take-Profit), ICP/EUR +1.63 EUR
- **Groesste Verluste**: NEAR/EUR 3x gestoppt = -10.35 EUR (Bug behoben)
- **Naechstes Ziel**: Nach Cooldown-Fix min. 4 Wochen weiterlaufen lassen fuer valide Win-Rate

## Telegram Bot
- Token: TELEGRAM_TOKEN in .env
- Chat ID: TELEGRAM_CHAT_ID in .env
- Kauf/Verkauf: sofortige Benachrichtigung
- Portfolio-Update: alle 2 Stunden automatisch
- `/portfolio` Command: aktuellen Stand abrufen

## Naechste Schritte (Prioritaet)

### Sofort umsetzbar (hoher Impact, wenig Aufwand)
1. [x] **POSITION_SIZE_PCT von 0.12 auf 0.06 gesenkt** (paper_trader.py) – 2026-06-04
       Jeder Stop-Loss kostet jetzt ~2.5 EUR statt ~5 EUR.
2. [x] **Kauf nur bei BULL-Regime** – 2026-06-04, am 2026-06-11 ERSETZT durch
       Regime-Abstufung: BULL normal, SIDEWAYS strenger+kleiner, BEAR nur Shorts.
3. [x] **XGBoost statt Random Forest** – xgboost 3.2.0 installiert, model.py angepasst – 2026-06-04
       Naechstes Retrain (`python model.py`) nutzt XGBoost. +2-4% Walk-Forward-Genauigkeit erwartet.

### Wichtig (bald)
4. [ ] **Win-Rate validieren** – min. 4-6 Wochen nach dem Cooldown-Fix (2026-06-04) laufen lassen
       Erst nach 50+ abgeschlossenen Sell-Trades ist die Win-Rate statistisch aussagekraeftig.
5. [ ] **Reddit-Sentiment live** – via PRAW (r/CryptoCurrency + r/Bitcoin) – groesster Signal-Hebel
       praw installieren, Reddit API-Key (kostenlos), sentiment.py erweitern.
6. [x] **Watchdog/Auto-Restart** – Task Scheduler Job "GOAT Watchdog", alle 5 Min – 2026-06-11
7. [ ] **Portfolio-Korrelation** – nicht zu viele korrelierte Positionen gleichzeitig kaufen

### Kritisch (vor echtem Geld)
8. [ ] **Backtesting** – Historische OHLCV-Daten seit 2019 sind jetzt da → Backtesting moeglich
9. [ ] **Win-Rate >55%** ueber min. 4 Wochen Paper-Trading bestaetigt

### Spaeter (echtes Geld)
10. [ ] **Hetzner VPS** (~8 EUR/Monat) – erst wenn Paper-Trading profitabel, Ollama durch RoBERTa ersetzen
11. [ ] **Kraken Trading-Berechtigung** freischalten (Orders erstellen)
12. [ ] **Limit Orders** statt Market Orders – bessere Einstiegspreise
13. [ ] **Kelly Criterion** – intelligentes Position-Sizing je nach Signalstaerke
14. [ ] **Sharpe Ratio Monitoring** – risikoadjustierte Rendite tracken

### Modell verbessern
15. [ ] **ML-Threshold erst bei 60% anheben** – Aktuell max ~56% nach Retrain auf 6,5 Jahren
16. [ ] **LSTM** – erst sinnvoll mit GPU (Hetzner VPS) und guten Sentiment-Daten

## Ziel-Architektur
Daten-Schicht (ccxt + RSS + HuggingFace + Blockchain APIs)
  -> Feature-Schicht (OHLCV-Indikatoren + Sentiment + Fear&Greed + Funding + Whales)
  -> Signal-Schicht (ML-Modell Random Forest, 40 Features)
  -> Strategie- & Risiko-Engine (Stop-Loss, Trailing Stop, Min-Haltezeit, Max-Positionen)
  -> Execution (Paper-Trading jetzt, echte Orders spaeter)
  -> Dashboard + Monitoring (Streamlit, Tailscale, Telegram)

## Konventionen
- Type Hints und kurze Docstrings ueberall
- Module klein und isoliert halten (ein Zweck pro Datei)
- Konfiguration und API-Keys in `.env`, NIE im Code
- Geldbetraege als Decimal behandeln, nicht float, bei echten Orders

## DON'T (wichtig)
- Das LLM trifft NIEMALS Kauf-/Verkaufs-Entscheidungen. Nur Sentiment.
- KEIN echtes Geld, bevor Paper-Trading ueber Wochen validiert ist (>55% Win-Rate).
- API-Keys NIE committen (`.env` in `.gitignore`).
- ML-Modelle immer mit Walk-Forward-Validierung – kein reiner Backtest (Overfitting).
- Modell manuell neu trainieren (`python model.py`) – kein Auto-Retraining.
- KEIN Hetzner VPS solange Ollama benutzt wird – zu langsam ohne GPU, erst RoBERTa einbauen.
- FAST_NEWS_BULLISH Kaeufe nicht wieder aktivieren – fuehren zu Whipsaw-Verlusten.
- Keine HuggingFace-Datasets importieren ohne vorher mit `check_dataset_deep.py` zu pruefen.
