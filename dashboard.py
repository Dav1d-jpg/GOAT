"""Streamlit-Dashboard: GOAT Trading Terminal.

Look: minimalistisch-futuristisch – cremefarbener Hintergrund, Kennzahlen als
LED-Punktmatrix-Anzeigen (Flip-Dot-Board-Optik), ruhige Farben, nachttauglich.

Tabs: Portfolio, Backtest, Markt, Chart, News, Indikatoren, Signale
"""
import subprocess
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from streamlit_autorefresh import st_autorefresh

from db import DB_PATH, get_connection, init_db

TZ = "Europe/Berlin"

# Watchdog-Steuerung von der Startseite aus.
# - WATCHDOG_TASK: die Windows-Aufgabe, die alle 5 Min Scheduler/Dashboard prueft.
#   Sie zu DEAKTIVIEREN stoppt sowohl den Neustart ALS AUCH das PowerShell-Fenster,
#   das sonst alle 5 Min kurz aufblitzt (Task laeuft "nur interaktiv").
# - STOP_FLAG: Zusatz-Bremse. Solange die Datei existiert, macht ein evtl. schon
#   gestarteter Watchdog-Lauf nichts (siehe watchdog.ps1). Guertel + Hosentraeger.
WATCHDOG_TASK = "GOAT Watchdog"
STOP_FLAG = Path(__file__).resolve().parent / "STOP_BOT.txt"


def _run_ps(cmd: str) -> subprocess.CompletedProcess:
    """PowerShell-Einzeiler ausfuehren (kein Fenster, kurzer Timeout)."""
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=15,
    )


def watchdog_task_state() -> "str | None":
    """'Ready' / 'Running' / 'Disabled' – oder None, wenn die Aufgabe fehlt."""
    try:
        r = _run_ps(
            f"(Get-ScheduledTask -TaskName '{WATCHDOG_TASK}' -ErrorAction Stop).State"
        )
        return r.stdout.strip() or None
    except Exception:
        return None


def autoupdate_enabled() -> bool:
    """True = Watchdog aktiv (Aufgabe nicht deaktiviert und keine STOP_BOT.txt)."""
    state = watchdog_task_state()
    if state is None:                     # Aufgabe nicht gefunden -> nur Datei zaehlt
        return not STOP_FLAG.exists()
    return state != "Disabled" and not STOP_FLAG.exists()


def set_autoupdate() -> None:
    """on_change-Callback: Aufgabe aktivieren/deaktivieren + STOP_BOT.txt setzen."""
    if st.session_state.autoupdate_toggle:
        _run_ps(f"Enable-ScheduledTask -TaskName '{WATCHDOG_TASK}'")
        STOP_FLAG.unlink(missing_ok=True)
    else:
        _run_ps(f"Disable-ScheduledTask -TaskName '{WATCHDOG_TASK}'")
        STOP_FLAG.write_text(
            "Auto-Restart pausiert ueber Dashboard am "
            f"{datetime.now():%Y-%m-%d %H:%M:%S}\n",
            encoding="utf-8",
        )

# ── Palette: Tinte auf Papier, gedecktes Gruen/Rot ───────────────────────────
INK    = "#3a372f"   # Haupt-Tinte
MUTE   = "#8b8576"   # Beschriftungen
GREEN  = "#2f9e68"
RED    = "#d4584e"
BLUE   = "#4a72b8"
VIOLET = "#8d6cb8"
AMBER  = "#c2922f"
PAPER  = "#f1ecdf"   # Seiten-Hintergrund
CARD   = "#faf6ea"   # Karten
PLOTBG = "#ece6d6"   # Chart-Flaeche


def to_local(series: "pd.Series") -> "pd.Series":
    """Unix-ms → lokale Zeit (Europe/Berlin)."""
    return pd.to_datetime(series, unit="ms", utc=True).dt.tz_convert(TZ).dt.tz_localize(None)


st.set_page_config(
    page_title="GOAT — Terminal",
    page_icon="◍",
    layout="wide",
)

# ── Globales Styling ──────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }

.stApp { background: #f1ecdf; }
/* feines Punktraster wie auf technischem Papier */
.stApp::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background-image: radial-gradient(rgba(58,55,47,0.07) 1px, transparent 1.4px);
  background-size: 28px 28px;
}

h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; letter-spacing: .02em; color: #3a372f; }
[data-testid="stCaptionContainer"], small { color: #8b8576 !important; }

[data-testid="stMetric"] {
  background: #faf6ea;
  border: 1px solid rgba(58,55,47,0.14);
  border-radius: 14px; padding: 14px 16px;
  box-shadow: 0 1px 0 rgba(58,55,47,0.05);
  transition: transform .18s ease, box-shadow .18s ease;
}
[data-testid="stMetric"]:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(58,55,47,0.10);
}
[data-testid="stMetricValue"] {
  font-family: 'IBM Plex Mono', monospace; font-size: 1.35rem; color: #3a372f;
}
[data-testid="stMetricLabel"] {
  font-family: 'IBM Plex Mono', monospace; color: #8b8576;
  text-transform: uppercase; letter-spacing: .18em; font-size: .68rem;
}

.stTabs [data-baseweb="tab-list"] {
  gap: 2px; background: transparent;
  border-bottom: 1px solid rgba(58,55,47,0.18); padding-bottom: 0;
}
.stTabs [data-baseweb="tab"] {
  font-family: 'IBM Plex Mono', monospace; font-size: .8rem;
  letter-spacing: .14em; text-transform: uppercase; color: #8b8576;
  border-radius: 8px 8px 0 0; padding: 6px 14px;
}
.stTabs [aria-selected="true"] {
  color: #3a372f !important; background: rgba(58,55,47,0.05);
  box-shadow: inset 0 -2px 0 #3a372f;
}

.stButton > button {
  background: #faf6ea; border: 1px solid rgba(58,55,47,0.30); color: #3a372f;
  font-family: 'IBM Plex Mono', monospace; font-size: .74rem;
  letter-spacing: .14em; text-transform: uppercase; border-radius: 10px;
  transition: all .18s ease;
}
.stButton > button:hover {
  border-color: #3a372f; box-shadow: 0 4px 14px rgba(58,55,47,0.16);
  transform: translateY(-1px); color: #3a372f;
}

[data-testid="stDataFrame"] {
  border: 1px solid rgba(58,55,47,0.14); border-radius: 12px;
  background: #faf6ea;
}
[data-testid="stExpander"] {
  background: #faf6ea; border: 1px solid rgba(58,55,47,0.12); border-radius: 12px;
}
hr { border-color: rgba(58,55,47,0.12) !important; }

/* ── Kopfzeile ── */
.goat-head {
  display: flex; justify-content: space-between; align-items: center;
  flex-wrap: wrap; gap: 14px; padding: 6px 2px 16px 2px;
  border-bottom: 1px solid rgba(58,55,47,0.2); margin-bottom: 10px;
}
.goat-word { font-family: 'Space Grotesk'; font-weight: 700; font-size: 1.5rem;
  letter-spacing: .06em; color: #3a372f; }
.goat-word span { font-weight: 400; color: #8b8576; font-size: 1.0rem; letter-spacing: .2em; }
.head-right { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.pill {
  font-family: 'IBM Plex Mono', monospace; font-size: .68rem; font-weight: 600;
  letter-spacing: .16em; text-transform: uppercase;
  padding: 5px 12px; border-radius: 999px; border: 1px solid;
}
.pill-live { color: #2f9e68; border-color: rgba(47,158,104,0.45); }
.pill-off  { color: #d4584e; border-color: rgba(212,88,78,0.45); }
.pill-bull { color: #2f9e68; border-color: rgba(47,158,104,0.45); }
.pill-bear { color: #d4584e; border-color: rgba(212,88,78,0.45); }
.pill-side { color: #c2922f; border-color: rgba(194,146,47,0.5); }
.pill-mute { color: #8b8576; border-color: rgba(139,133,118,0.4); }
.livedot {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  margin-right: 7px; background: #2f9e68; box-shadow: 0 0 6px rgba(47,158,104,0.8);
  animation: breathe 2.4s ease-in-out infinite; vertical-align: 1px;
}
@keyframes breathe { 0%,100% { opacity: 1; } 50% { opacity: .3; } }

/* ── LED-Punktmatrix ── */
.led-board {
  display: flex; flex-wrap: wrap; gap: 26px 40px; align-items: flex-end;
  justify-content: space-between;
  background: #faf6ea; border: 1px solid rgba(58,55,47,0.14);
  border-radius: 18px; padding: 24px 28px;
  box-shadow: 0 1px 0 rgba(58,55,47,0.05);
}
.led-cell  { display: flex; flex-direction: column; }
.led-label {
  font-family: 'IBM Plex Mono', monospace; font-size: .66rem;
  letter-spacing: .24em; text-transform: uppercase; color: #8b8576;
  margin-bottom: 12px;
}
.led-sub {
  font-family: 'IBM Plex Mono', monospace; font-size: .78rem;
  letter-spacing: .06em; margin-top: 12px;
}
.led-num  { display: inline-flex; gap: 9px; align-items: flex-end; }
.led-char { display: inline-grid; }
.ld {
  display: block; width: 100%; height: 100%; border-radius: 50%;
  background: rgba(58,55,47,0.07);
  transition: background .4s ease, box-shadow .4s ease;
}
</style>
""", unsafe_allow_html=True)


# ── LED-Punktmatrix-Renderer ─────────────────────────────────────────────────
# 5x7-Punktschrift; inaktive Punkte bleiben als Schatten sichtbar (Flip-Dot-Optik)

DOT_FONT: dict[str, list[str]] = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    ".": ["00000", "00000", "00000", "00000", "00000", "00110", "00110"],
    ",": ["00000", "00000", "00000", "00000", "00110", "00110", "01100"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "-": ["00000", "00000", "00000", "01110", "00000", "00000", "00000"],
    "%": ["11000", "11001", "00010", "00100", "01000", "10011", "00011"],
    "€": ["00111", "01000", "11111", "01000", "11111", "01000", "00111"],
    ":": ["00000", "00110", "00110", "00000", "00110", "00110", "00000"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def led(text: str, color: str = INK, dot: int = 6, gap: int = 2) -> str:
    """Text als LED-Punktmatrix-HTML rendern (Ziffern, . , + - % € :)."""
    chars = []
    for ch in str(text):
        pattern = DOT_FONT.get(ch, DOT_FONT[" "])
        dots = []
        for row in pattern:
            for bit in row:
                if bit == "1":
                    dots.append(
                        f'<span class="ld" style="background:{color};'
                        f'box-shadow:0 0 {dot + 2}px {color}55"></span>'
                    )
                else:
                    dots.append('<span class="ld"></span>')
        chars.append(
            f'<span class="led-char" style="grid-template-columns:repeat(5,{dot}px);'
            f'grid-auto-rows:{dot}px;gap:{gap}px">{"".join(dots)}</span>'
        )
    return f'<span class="led-num">{"".join(chars)}</span>'


def led_cell(label: str, text: str, color: str = INK, dot: int = 5,
             sub: str | None = None, sub_color: str = MUTE) -> str:
    sub_html = f'<div class="led-sub" style="color:{sub_color}">{sub}</div>' if sub else ""
    return (f'<div class="led-cell"><div class="led-label">{label}</div>'
            f'{led(text, color=color, dot=dot)}{sub_html}</div>')


def style_fig(fig: go.Figure, height: int = 400, title: str | None = None) -> go.Figure:
    """Einheitliches Papier-Layout fuer alle Plotly-Charts."""
    fig.update_layout(
        template="plotly_white",
        height=height,
        title=title,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=PLOTBG,
        font=dict(family="IBM Plex Mono, monospace", size=12.5, color="#4a4639"),
        margin=dict(l=10, r=10, t=42 if title else 12, b=10),
        hoverlabel=dict(bgcolor="#fbf8ef", bordercolor="rgba(58,55,47,0.35)",
                        font=dict(family="IBM Plex Mono, monospace", color=INK)),
        legend=dict(orientation="h", y=1.06, bgcolor="rgba(0,0,0,0)"),
    )
    fig.update_xaxes(gridcolor="rgba(58,55,47,0.10)", zerolinecolor="rgba(58,55,47,0.18)",
                     showspikes=True, spikecolor="rgba(58,55,47,0.45)",
                     spikethickness=1, spikedash="dot")
    fig.update_yaxes(gridcolor="rgba(58,55,47,0.10)", zerolinecolor="rgba(58,55,47,0.18)")
    return fig


# ── System-Status fuer die Kopfzeile ─────────────────────────────────────────

def _table_exists(name: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone())


def load_system_status() -> dict:
    now_ms = int(datetime.now().timestamp() * 1000)
    out = {"live": False, "regime": "–", "fg": None}
    with sqlite3.connect(DB_PATH) as conn:
        try:
            row = conn.execute("SELECT MAX(timestamp) FROM live_prices").fetchone()
            out["live"] = bool(row and row[0] and now_ms - row[0] < 180_000)
        except Exception:
            pass
        try:
            row = conn.execute(
                "SELECT regime, fear_greed FROM market_regime ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                out["regime"], out["fg"] = row[0], row[1]
        except Exception:
            pass
    return out


status = load_system_status()
live_pill = ('<span class="pill pill-live"><span class="livedot"></span>live</span>'
             if status["live"] else '<span class="pill pill-off">offline</span>')
regime_cls  = {"BULL": "pill-bull", "BEAR": "pill-bear"}.get(status["regime"], "pill-side")
regime_pill = f'<span class="pill {regime_cls}">{status["regime"]}</span>'
fg_pill     = (f'<span class="pill pill-mute">F&amp;G {status["fg"]}</span>'
               if status["fg"] is not None else "")
clock_html  = led(datetime.now().strftime("%H:%M"), color=INK, dot=3, gap=1)

st.markdown(f"""
<div class="goat-head">
  <div class="goat-word">GOAT <span>/ trading terminal</span></div>
  <div class="head-right">{live_pill}{regime_pill}{fg_pill}{clock_html}</div>
</div>
""", unsafe_allow_html=True)

# Nur den aktiven Tab alle 10 Sekunden aktualisieren
st_autorefresh(interval=10_000, key="live_refresh")

init_db()


# ── Daten-Loader ──────────────────────────────────────────────────────────────

def load_latest_tickers() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """
            SELECT t.symbol, t.bid, t.ask, t.last, t.volume_24h, t.change_pct, t.timestamp
            FROM tickers t
            INNER JOIN (
                SELECT symbol, MAX(timestamp) AS max_ts FROM tickers GROUP BY symbol
            ) latest ON t.symbol = latest.symbol AND t.timestamp = latest.max_ts
            ORDER BY t.volume_24h DESC
            """,
            conn,
        )


def load_ohlcv(symbol: str, limit: int = 200, timeframe: str = "1h") -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            conn,
            params=(symbol, timeframe, limit),
        )
    df["datetime"] = to_local(df["timestamp"])
    return df.sort_values("datetime")


def load_news(limit: int = 50) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """
            SELECT source, title, url, published, sentiment, score, summary
            FROM news_sentiment
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            conn,
            params=(limit,),
        )


def load_sentiment_summary() -> pd.DataFrame:
    """Sentiment-Verteilung der letzten 24h pro Sentiment-Typ."""
    cutoff = int((datetime.now().timestamp() - 86400) * 1000)
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """
            SELECT sentiment, COUNT(*) as count, AVG(score) as avg_score
            FROM news_sentiment
            WHERE fetched_at > ?
            GROUP BY sentiment
            """,
            conn,
            params=(cutoff,),
        )


def get_available_symbols() -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol").fetchall()
    return [r[0] for r in rows]


def format_change(val: float | None) -> str:
    if val is None:
        return "–"
    arrow = "▲" if val >= 0 else "▼"
    return f"{arrow} {abs(val):.2f}%"


def sentiment_emoji(s: str) -> str:
    return {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(s, "⚪")


def load_paper_balance() -> pd.DataFrame:
    if not _table_exists("paper_balance"):
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT timestamp, cash_eur, invested_eur, total_value FROM paper_balance ORDER BY timestamp",
            conn,
        )
    df["datetime"] = to_local(df["timestamp"])
    return df


def load_paper_trades() -> pd.DataFrame:
    if not _table_exists("paper_trades"):
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT timestamp, symbol, action, price, amount, value_eur,
                      pl_eur, reason, prob_up, portfolio_value
               FROM paper_trades ORDER BY timestamp""",
            conn,
        )


def load_paper_portfolio() -> pd.DataFrame:
    if not _table_exists("paper_portfolio"):
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            "SELECT symbol, amount, avg_buy_price, bought_at FROM paper_portfolio",
            conn,
        )


def load_paper_shorts() -> pd.DataFrame:
    if not _table_exists("paper_shorts"):
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            "SELECT symbol, amount, entry_price, margin_eur, opened_at FROM paper_shorts",
            conn,
        )


def load_latest_prices() -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT t.symbol, t.last FROM tickers t
               INNER JOIN (
                   SELECT symbol, MAX(timestamp) AS max_ts FROM tickers GROUP BY symbol
               ) l ON t.symbol = l.symbol AND t.timestamp = l.max_ts"""
        ).fetchall()
    return {r[0]: r[1] for r in rows}


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_main, tab_bt, tab_markt, tab_chart, tab_news, tab_feat, tab_sig = st.tabs(
    ["Portfolio", "Backtest", "Markt", "Chart", "News", "Indikatoren", "Signale"]
)


# ══ Tab 1: Portfolio (Paper Trading) ══════════════════════════════════════════

with tab_main:
    from paper_trader import INITIAL_CAPITAL, init_paper_trading
    init_paper_trading()

    # ── Auto-Restart (Watchdog) an/aus ────────────────────────────────────────
    # Session-State einmalig aus dem Dateizustand seeden, danach steuert der
    # Toggle die Datei (nicht umgekehrt) – vermeidet die value/key-Warnung.
    if "autoupdate_toggle" not in st.session_state:
        st.session_state.autoupdate_toggle = autoupdate_enabled()

    col_au, col_au_info = st.columns([1.4, 4])
    with col_au:
        st.toggle(
            "Auto-Restart (Watchdog)",
            key="autoupdate_toggle",
            on_change=set_autoupdate,
            help="Aus = die Watchdog-Aufgabe wird deaktiviert. Dann startet nichts "
                 "mehr neu UND das PowerShell-Fenster blitzt nicht mehr alle 5 Min "
                 "auf (kein 'raustabben' mehr). Laufende Prozesse bleiben laufen. "
                 "Wieder anschalten, damit der Bot nach Absturz/Reboot zurueckkommt.",
        )
    with col_au_info:
        if st.session_state.autoupdate_toggle:
            st.caption("🟢 Watchdog aktiv – Bot wird bei Bedarf automatisch neugestartet.")
        else:
            st.caption("⏸️ Watchdog pausiert – kein Neustart, kein 5-Min-Fenster-Flash.")

    st.divider()

    balance_df   = load_paper_balance()
    trades_df    = load_paper_trades()
    portfolio_df = load_paper_portfolio()
    prices       = load_latest_prices()

    def add_capital(amount: float) -> None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT cash_eur, invested_eur FROM paper_balance ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            cash_now     = float(row[0]) if row else INITIAL_CAPITAL
            invested_now = float(row[1]) if row else 0.0
            new_cash     = cash_now + amount
            ts           = int(datetime.now().timestamp() * 1000) + 1
            conn.execute(
                "INSERT OR REPLACE INTO paper_balance VALUES (?,?,?,?)",
                (ts, new_cash, invested_now, new_cash + invested_now),
            )
            conn.commit()

    if not balance_df.empty:
        latest    = balance_df.iloc[-1]
        total_val = latest["total_value"]
        cash      = latest["cash_eur"]
        invested  = latest["invested_eur"]
        pl_total  = total_val - INITIAL_CAPITAL
        pl_pct    = (pl_total / INITIAL_CAPITAL) * 100
        pl_color  = GREEN if pl_total >= 0 else RED

        sells = trades_df[trades_df["action"].isin(["SELL", "COVER"])] if not trades_df.empty else pd.DataFrame()
        pl_vals  = sells["pl_eur"].dropna() if not sells.empty else pd.Series(dtype=float)
        wins     = int((pl_vals > 0).sum())
        losses   = int((pl_vals <= 0).sum())
        win_rate = wins / len(pl_vals) * 100 if len(pl_vals) > 0 else 0.0
        wr_color = GREEN if win_rate >= 50 else (AMBER if win_rate >= 40 else RED)

        # ── LED-Anzeigetafel ──────────────────────────────────────────────────
        board = (
            '<div class="led-board">'
            + led_cell("Portfolio", f"{total_val:,.2f} €", color=pl_color, dot=7,
                       sub=f"{pl_total:+,.2f} € ({pl_pct:+.1f}%) seit Start", sub_color=pl_color)
            + led_cell("Cash", f"{cash:,.2f} €", color=INK, dot=4)
            + led_cell("Investiert", f"{invested:,.2f} €", color=INK, dot=4)
            + led_cell("Trades", f"{len(pl_vals)}", color=INK, dot=4,
                       sub=f"{wins} W / {losses} L")
            + led_cell("Win-Rate", f"{win_rate:.0f}%", color=wr_color, dot=4)
            + "</div>"
        )
        st.markdown(board, unsafe_allow_html=True)
        st.write("")

        col_btn, col_rest = st.columns([1, 5])
        with col_btn:
            if st.button("+100 € einzahlen"):
                add_capital(100.0)
                st.success("+100 € hinzugefügt!")

        if len(pl_vals) > 0:
            avg_win     = pl_vals[pl_vals > 0].mean() if wins > 0 else 0
            avg_loss    = pl_vals[pl_vals <= 0].mean() if losses > 0 else 0
            best_trade  = pl_vals.max()
            worst_trade = pl_vals.min()
            if len(balance_df) > 1:
                peak     = balance_df["total_value"].cummax()
                drawdown = ((balance_df["total_value"] - peak) / peak * 100).min()
            else:
                drawdown = 0.0

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Ø Gewinn",      f"{avg_win:+.2f} €")
            m2.metric("Ø Verlust",     f"{avg_loss:+.2f} €")
            m3.metric("Bester Trade",  f"{best_trade:+.2f} €")
            m4.metric("Schlechtester", f"{worst_trade:+.2f} €")
            m5.metric("Max Drawdown",  f"{drawdown:.1f}%", delta_color="inverse")
    else:
        if st.button("+100 € einzahlen"):
            add_capital(100.0)
            st.success("+100 € hinzugefügt!")

    st.divider()

    # ── Portfolio-Wert + Trade-Marker ─────────────────────────────────────────
    st.subheader("Verlauf")

    if balance_df.empty or len(balance_df) < 2:
        st.info("Noch keine Daten. Der erste Trade wird beim nächsten Scheduler-Zyklus ausgeführt.")
    else:
        line_color = GREEN if balance_df["total_value"].iloc[-1] >= INITIAL_CAPITAL else RED
        fig_port = go.Figure()
        fig_port.add_trace(go.Scatter(
            x=balance_df["datetime"], y=balance_df["total_value"],
            name="Portfolio-Wert",
            line=dict(color=line_color, width=2),
            fill="tozeroy", fillcolor="rgba(58,55,47,0.04)",
        ))
        fig_port.add_hline(
            y=INITIAL_CAPITAL,
            line_dash="dot", line_color="rgba(58,55,47,0.45)",
            annotation_text=f"Start {INITIAL_CAPITAL:.0f} €",
            annotation_position="bottom right",
        )

        if not trades_df.empty:
            trades_df["datetime"] = to_local(trades_df["timestamp"])
            buys  = trades_df[trades_df["action"].isin(["BUY", "SHORT"])]
            sells = trades_df[trades_df["action"].isin(["SELL", "COVER"])]

            if not buys.empty:
                buy_colors = [VIOLET if a == "SHORT" else BLUE for a in buys["action"]]
                fig_port.add_trace(go.Scatter(
                    x=buys["datetime"], y=buys["portfolio_value"],
                    mode="markers", name="Einstieg",
                    marker=dict(color=buy_colors, size=11, symbol="circle",
                                line=dict(color=CARD, width=1.5)),
                    customdata=buys[["symbol", "price", "value_eur", "reason", "prob_up", "action"]].values,
                    hovertemplate=(
                        "<b>%{customdata[5]}</b><br>"
                        "Symbol: %{customdata[0]}<br>"
                        "Preis: %{customdata[1]:,.4f} €<br>"
                        "Einsatz: %{customdata[2]:,.2f} €<br>"
                        "Grund: %{customdata[3]}<br>"
                        "Signal: %{customdata[4]:.0%}<br>"
                        "Portfolio: %{y:,.2f} €<extra></extra>"
                    ),
                ))

            if not sells.empty:
                sell_colors = [GREEN if (row["pl_eur"] or 0) >= 0 else RED
                               for _, row in sells.iterrows()]
                fig_port.add_trace(go.Scatter(
                    x=sells["datetime"], y=sells["portfolio_value"],
                    mode="markers", name="Ausstieg",
                    marker=dict(color=sell_colors, size=11, symbol="diamond",
                                line=dict(color=CARD, width=1.5)),
                    customdata=sells[["symbol", "price", "value_eur", "pl_eur", "reason", "prob_up", "action"]].values,
                    hovertemplate=(
                        "<b>%{customdata[6]}</b><br>"
                        "Symbol: %{customdata[0]}<br>"
                        "Preis: %{customdata[1]:,.4f} €<br>"
                        "Erlös: %{customdata[2]:,.2f} €<br>"
                        "P/L: %{customdata[3]:+,.2f} €<br>"
                        "Grund: %{customdata[4]}<br>"
                        "Portfolio: %{y:,.2f} €<extra></extra>"
                    ),
                ))

        style_fig(fig_port, height=430)
        fig_port.update_layout(hovermode="x unified",
                               xaxis_title="Zeit", yaxis_title="Wert (€)")
        st.plotly_chart(fig_port, width="stretch")

    st.divider()

    # ── Offene Positionen + Shorts nebeneinander ─────────────────────────────
    shorts_df = load_paper_shorts()
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Positionen · Long")
        if portfolio_df.empty:
            st.caption("Keine offenen Long-Positionen.")
        else:
            rows = []
            for _, pos in portfolio_df.iterrows():
                cur_price = prices.get(pos["symbol"], pos["avg_buy_price"])
                cur_val   = pos["amount"] * cur_price
                buy_val   = pos["amount"] * pos["avg_buy_price"]
                pl        = cur_val - buy_val
                pl_pct_v  = (pl / buy_val) * 100 if buy_val > 0 else 0
                rows.append({
                    "Symbol":     pos["symbol"],
                    "Einstieg":   f"{pos['avg_buy_price']:,.4f} €",
                    "Akt. Preis": f"{cur_price:,.4f} €",
                    "Wert":       f"{cur_val:,.2f} €",
                    "P/L":        f"{pl:+.2f} € ({pl_pct_v:+.1f}%)",
                })
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    with col_r:
        st.subheader("Positionen · Short")
        if shorts_df.empty:
            st.caption("Keine offenen Shorts.")
        else:
            srows = []
            for _, pos in shorts_df.iterrows():
                cur_price = prices.get(pos["symbol"], pos["entry_price"])
                pl        = pos["amount"] * (pos["entry_price"] - cur_price)
                pl_pct_v  = (pl / pos["margin_eur"]) * 100 if pos["margin_eur"] > 0 else 0
                srows.append({
                    "Symbol":     pos["symbol"],
                    "Einstieg":   f"{pos['entry_price']:,.4f} €",
                    "Akt. Preis": f"{cur_price:,.4f} €",
                    "Margin":     f"{pos['margin_eur']:,.2f} €",
                    "P/L":        f"{pl:+.2f} € ({pl_pct_v:+.1f}%)",
                })
            st.dataframe(pd.DataFrame(srows), width="stretch", hide_index=True)

    st.write("")

    # ── Trade-Historie ────────────────────────────────────────────────────────
    with st.expander(f"Trade-Historie ({len(trades_df)} Einträge)"):
        if trades_df.empty:
            st.caption("Noch keine Trades.")
        else:
            hist = trades_df.copy()
            hist["datetime"] = to_local(hist["timestamp"]).dt.strftime("%d.%m %H:%M")
            hist["Aktion"]   = hist["action"].map({"BUY": "🔵 Kauf", "SELL": "⬜ Verkauf",
                                                   "SHORT": "🟣 Short", "COVER": "⬜ Cover"})
            hist.loc[(hist["action"] == "SELL") & (hist["pl_eur"] >= 0), "Aktion"] = "🟢 Verkauf"
            hist.loc[(hist["action"] == "SELL") & (hist["pl_eur"] <  0), "Aktion"] = "🔴 Verkauf"
            hist.loc[(hist["action"] == "COVER") & (hist["pl_eur"] >= 0), "Aktion"] = "🟢 Cover"
            hist.loc[(hist["action"] == "COVER") & (hist["pl_eur"] <  0), "Aktion"] = "🔴 Cover"
            hist["Preis"]  = hist["price"].apply(lambda x: f"{x:,.4f} €")
            hist["Wert"]   = hist["value_eur"].apply(lambda x: f"{x:,.2f} €")
            hist["P/L"]    = hist["pl_eur"].apply(lambda x: f"{x:+.2f} €" if pd.notna(x) else "–")
            hist["Signal"] = hist["prob_up"].apply(lambda x: f"{x*100:.0f}%" if pd.notna(x) else "–")
            st.dataframe(
                hist[["datetime", "Aktion", "symbol", "Preis", "Wert", "P/L", "reason", "Signal"]]
                .rename(columns={"datetime": "Zeit", "symbol": "Symbol", "reason": "Grund"})
                .iloc[::-1],
                width="stretch", hide_index=True,
            )


# ══ Tab 2: Backtest ═══════════════════════════════════════════════════════════

with tab_bt:
    from backtest import load_backtest_result, run_backtest

    st.subheader("Strategie-Backtest")
    st.caption(
        "Simuliert die exakten Bot-Regeln auf den historischen Daten – "
        "trainiert nur auf dem Anfang der Historie, gehandelt wird auf dem "
        "ungesehenen Rest (Out-of-Sample, kein Look-Ahead)."
    )

    pc1, pc2, pc3, pc4, pc5 = st.columns([1.2, 1, 1, 1, 1.2])
    with pc1:
        bt_train = st.slider("Train-Anteil", 0.5, 0.9, 0.7, 0.05, key="bt_train",
                             help="Anteil der Historie fuers Training; der Rest wird gehandelt")
    with pc2:
        bt_buy = st.slider("Long ab P(up)", 0.50, 0.80, 0.68, 0.01, key="bt_buy")
    with pc3:
        bt_short = st.slider("Short ab P(down)", 0.50, 0.80, 0.70, 0.01, key="bt_short")
    with pc4:
        bt_shorts_on = st.toggle("Shorts aktiv", value=True, key="bt_shorts")
    with pc5:
        st.write("")
        run_clicked = st.button("Backtest starten", key="bt_run")

    if run_clicked:
        with st.spinner("Backtest läuft – Modelle trainieren + Simulation (dauert 2-5 Minuten) …"):
            res = run_backtest(
                train_frac=bt_train, buy_threshold=bt_buy,
                short_threshold=bt_short, enable_shorts=bt_shorts_on,
            )
        if "error" in res:
            st.error(res["error"])
        else:
            st.success(f"Backtest fertig in {res['runtime_s']}s.")

    bt = load_backtest_result()
    if bt is None or "metrics" not in bt:
        st.info("Noch kein Backtest-Ergebnis. Parameter wählen und **Backtest starten** – "
                "oder im Terminal: `python backtest.py`")
    else:
        m, p = bt["metrics"], bt["params"]
        st.caption(
            f"Letzter Lauf: {bt['computed_at']}  |  Zeitraum {bt['period']['from']} → "
            f"{bt['period']['to']}  |  Train {p['train_frac']:.0%}, Long ≥ {p['buy_threshold']:.2f}, "
            f"Short ≥ {p['short_threshold']:.2f}, Shorts {'an' if p['enable_shorts'] else 'aus'}"
        )

        ret_color = GREEN if m["total_return"] >= 0 else RED
        wr_color  = GREEN if m["win_rate"] >= 50 else (AMBER if m["win_rate"] >= 40 else RED)
        edge      = m["total_return"] - m["bh_btc_return"]
        board = (
            '<div class="led-board">'
            + led_cell("Endwert", f"{m['final_value']:,.2f} €", color=ret_color, dot=6,
                       sub=f"{m['total_return']:+.2f}% Gesamt-Return", sub_color=ret_color)
            + led_cell("vs. BTC Buy&Hold", f"{edge:+.1f}%",
                       color=GREEN if edge >= 0 else RED, dot=4,
                       sub=f"BTC selbst: {m['bh_btc_return']:+.1f}%")
            + led_cell("Win-Rate", f"{m['win_rate']:.0f}%", color=wr_color, dot=4,
                       sub=f"{m['n_trades']} Trades")
            + led_cell("Max Drawdown", f"{m['max_drawdown']:.1f}%",
                       color=RED if m["max_drawdown"] < -20 else AMBER, dot=4)
            + "</div>"
        )
        st.markdown(board, unsafe_allow_html=True)
        st.write("")

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Profit-Faktor",
                  f"{m['profit_factor']}" if m["profit_factor"] is not None else "∞",
                  help="Summe Gewinne / Summe Verluste – über 1.0 = profitabel")
        k2.metric("Sharpe", f"{m['sharpe']:.2f}",
                  help="Risikoadjustierte Rendite (annualisiert)")
        k3.metric("Ø Gewinn", f"{m['avg_win']:+.2f} €")
        k4.metric("Ø Verlust", f"{m['avg_loss']:+.2f} €")

        st.divider()

        # ── Equity-Kurve + Drawdown ───────────────────────────────────────────
        eq = pd.DataFrame(bt["equity"], columns=["ts", "value"])
        eq["datetime"] = to_local(eq["ts"])
        peak = eq["value"].cummax()
        eq["dd"] = (eq["value"] - peak) / peak * 100

        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(
            x=eq["datetime"], y=eq["value"], name="Strategie",
            line=dict(color=ret_color, width=2),
            fill="tozeroy", fillcolor="rgba(58,55,47,0.04)",
        ))
        fig_eq.add_hline(y=p["start_capital"], line_dash="dot",
                         line_color="rgba(58,55,47,0.45)",
                         annotation_text=f"Start {p['start_capital']:.0f} €")
        style_fig(fig_eq, height=380, title="Equity-Kurve (Out-of-Sample)")
        fig_eq.update_layout(hovermode="x unified", yaxis_title="Wert (€)")
        st.plotly_chart(fig_eq, width="stretch")

        fig_dd = go.Figure(go.Scatter(
            x=eq["datetime"], y=eq["dd"], name="Drawdown",
            line=dict(color=RED, width=1.2),
            fill="tozeroy", fillcolor="rgba(212,88,78,0.12)",
        ))
        style_fig(fig_dd, height=190, title="Drawdown (%)")
        st.plotly_chart(fig_dd, width="stretch")

        st.divider()

        # ── Aufschluesselungen ───────────────────────────────────────────────
        ca, cb = st.columns(2)
        with ca:
            st.markdown("##### P/L nach Exit-Grund")
            rs = bt.get("reason_stats", {})
            if rs:
                rdf = pd.DataFrame([
                    {"Grund": k, "Trades": v["n"], "P/L": v["pl"]} for k, v in rs.items()
                ]).sort_values("P/L")
                fig_rs = go.Figure(go.Bar(
                    x=rdf["P/L"], y=rdf["Grund"], orientation="h",
                    marker_color=[GREEN if v >= 0 else RED for v in rdf["P/L"]],
                    text=[f"{v:+.0f} € ({n})" for v, n in zip(rdf["P/L"], rdf["Trades"])],
                    textposition="outside",
                ))
                style_fig(fig_rs, height=320)
                fig_rs.update_layout(xaxis_title="P/L (€)  –  (Anzahl Trades)")
                st.plotly_chart(fig_rs, width="stretch")

        with cb:
            st.markdown("##### Long vs. Short & Regime")
            ss = bt.get("side_stats", {})
            for side, v in ss.items():
                wr = v["wins"] / v["n"] * 100 if v["n"] else 0
                icon = "🔵" if side == "LONG" else "🟣"
                st.metric(f"{icon} {side}", f"{v['pl']:+.2f} €",
                          delta=f"{v['n']} Trades | Win-Rate {wr:.0f}%")
            rh = bt.get("regime_hours", {})
            if rh:
                fig_rh = go.Figure(go.Pie(
                    labels=list(rh.keys()), values=list(rh.values()), hole=0.6,
                    marker_colors=[{"BULL": GREEN, "BEAR": RED}.get(k, AMBER) for k in rh],
                ))
                style_fig(fig_rh, height=250)
                fig_rh.update_layout(showlegend=True, title="Markt-Regime (Stunden)")
                st.plotly_chart(fig_rh, width="stretch")

        # ── Trade-Liste ──────────────────────────────────────────────────────
        with st.expander(f"Trades im Detail (letzte {min(len(bt.get('trades', [])), 500)})"):
            tdf = pd.DataFrame(bt.get("trades", []))
            if not tdf.empty:
                tdf["Zeit"]  = to_local(tdf["ts"]).dt.strftime("%d.%m.%y %H:%M")
                tdf["Seite"] = tdf["side"].map({"LONG": "🔵 Long", "SHORT": "🟣 Short"})
                tdf["Entry"] = tdf["entry"].apply(lambda x: f"{x:,.4f} €")
                tdf["Exit"]  = tdf["exit"].apply(lambda x: f"{x:,.4f} €")
                tdf["P/L"]   = tdf["pl"].apply(lambda x: f"{x:+.2f} €")
                tdf["Dauer"] = tdf["held_h"].astype(int).astype(str) + "h"
                st.dataframe(
                    tdf[["Zeit", "Seite", "symbol", "Entry", "Exit", "P/L", "Dauer", "reason"]]
                    .rename(columns={"symbol": "Symbol", "reason": "Grund"})
                    .iloc[::-1],
                    width="stretch", hide_index=True,
                )


# ══ Tab 3: Markt ══════════════════════════════════════════════════════════════

with tab_markt:
    tickers = load_latest_tickers()

    if tickers.empty:
        st.info("Noch keine Daten – starte `python scheduler.py`.")
    else:
        last_update = datetime.fromtimestamp(tickers["timestamp"].max() / 1000)
        st.caption(f"Letztes Update: {last_update.strftime('%d.%m.%Y %H:%M:%S')}")

        display = tickers[["symbol", "last", "bid", "ask", "change_pct", "volume_24h"]].copy()
        display.columns = ["Symbol", "Preis (€)", "Bid (€)", "Ask (€)", "24h Änderung", "Volumen 24h (€)"]
        display["24h Änderung"] = display["24h Änderung"].apply(format_change)
        for col in ["Preis (€)", "Bid (€)", "Ask (€)"]:
            display[col] = display[col].apply(lambda x: f"{x:,.4f}" if x else "–")
        display["Volumen 24h (€)"] = display["Volumen 24h (€)"].apply(
            lambda x: f"{x:,.0f}" if x else "–"
        )
        st.dataframe(display, width="stretch", hide_index=True)

        st.divider()
        st.subheader("24h Volumen")
        vol_df = (
            tickers[["symbol", "volume_24h"]]
            .dropna()
            .query("symbol not in ['USDC/EUR', 'USDT/EUR']")
            .sort_values("volume_24h")
        )
        fig_vol = go.Figure(go.Bar(
            x=vol_df["volume_24h"], y=vol_df["symbol"],
            orientation="h",
            marker=dict(color=vol_df["volume_24h"],
                        colorscale=[[0, "#d8d0bc"], [1, INK]], showscale=False),
        ))
        style_fig(fig_vol, height=420)
        fig_vol.update_layout(xaxis_title="Volumen in € (24h)")
        st.plotly_chart(fig_vol, width="stretch")


# ══ Tab 4: Chart ══════════════════════════════════════════════════════════════

with tab_chart:
    symbols = get_available_symbols()

    if not symbols:
        st.info("Noch keine OHLCV-Daten vorhanden.")
    else:
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            selected = st.selectbox(
                "Symbol", symbols,
                index=symbols.index("BTC/EUR") if "BTC/EUR" in symbols else 0,
            )
        with col2:
            timeframe = st.selectbox("Timeframe", ["5m", "1h"], index=1)
        with col3:
            candle_count = st.slider("Kerzen", min_value=24, max_value=200, value=100, step=24)

        df = load_ohlcv(selected, limit=candle_count, timeframe=timeframe)

        if df.empty:
            st.warning(f"Keine Daten für {selected} ({timeframe}).")
        else:
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=df["datetime"], open=df["open"], high=df["high"],
                low=df["low"], close=df["close"], name=selected,
                increasing_line_color=GREEN, decreasing_line_color=RED,
                increasing_fillcolor="rgba(47,158,104,0.6)",
                decreasing_fillcolor="rgba(212,88,78,0.6)",
            ))
            fig.add_trace(go.Bar(
                x=df["datetime"], y=df["volume"], name="Volumen",
                marker_color="rgba(58,55,47,0.18)", yaxis="y2",
            ))
            style_fig(fig, height=550, title=f"{selected} – {timeframe} Kerzen")
            fig.update_layout(
                xaxis_title="Zeit", yaxis_title="Preis (€)",
                yaxis2=dict(title="Volumen", overlaying="y", side="right", showgrid=False),
                xaxis_rangeslider_visible=False,
            )
            st.plotly_chart(fig, width="stretch")


# ══ Tab 5: News & Sentiment ═══════════════════════════════════════════════════

with tab_news:
    news_df    = load_news(limit=50)
    summary_df = load_sentiment_summary()

    if news_df.empty:
        st.info("Noch keine News – der Scheduler analysiert alle 15 Minuten neue Artikel.")
    else:
        st.subheader("Markt-Stimmung (letzte 24h)")

        if not summary_df.empty:
            cols = st.columns(3)
            sentiment_order = ["positive", "neutral", "negative"]
            for i, sent in enumerate(sentiment_order):
                row   = summary_df[summary_df["sentiment"] == sent]
                count = int(row["count"].values[0]) if not row.empty else 0
                avg   = float(row["avg_score"].values[0]) if not row.empty else 0.5
                with cols[i]:
                    st.metric(
                        label=f"{sentiment_emoji(sent)} {sent.capitalize()}",
                        value=f"{count} Artikel",
                        delta=f"Ø Score: {avg:.2f}",
                    )

            fig_pie = go.Figure(go.Pie(
                labels=summary_df["sentiment"].str.capitalize(),
                values=summary_df["count"],
                marker_colors=[GREEN, "#b9b29f", RED],
                hole=0.6,
            ))
            style_fig(fig_pie, height=290)
            fig_pie.update_layout(showlegend=True)
            st.plotly_chart(fig_pie, width="stretch")

        st.divider()
        st.subheader("Neueste Artikel")

        for _, row in news_df.iterrows():
            emoji = sentiment_emoji(row["sentiment"])
            score = f"{row['score']:.2f}" if row["score"] else "–"
            pub   = datetime.fromtimestamp(row["published"] / 1000).strftime("%d.%m %H:%M") if row["published"] else "–"

            with st.expander(f"{emoji} [{row['source']}] {row['title'][:80]}"):
                col_a, col_b = st.columns([1, 3])
                with col_a:
                    st.write(f"**Sentiment:** {row['sentiment']}")
                    st.write(f"**Score:** {score}")
                    st.write(f"**Datum:** {pub}")
                with col_b:
                    st.write(f"**Begründung:** {row['summary'] or '–'}")
                    if row["url"]:
                        st.markdown(f"[Artikel öffnen]({row['url']})")


# ══ Tab 6: Indikatoren ════════════════════════════════════════════════════════

def load_latest_features(timeframe: str = "1h") -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """
            SELECT f.symbol, f.rsi_14, f.macd_hist, f.bb_pct_b,
                   f.ema_9, f.ema_21, f.vol_ratio, f.ret_1, f.ret_6, f.close
            FROM features f
            INNER JOIN (
                SELECT symbol, MAX(timestamp) AS max_ts
                FROM features WHERE timeframe = ?
                GROUP BY symbol
            ) latest ON f.symbol = latest.symbol AND f.timestamp = latest.max_ts
            WHERE f.timeframe = ?
            ORDER BY f.symbol
            """,
            conn,
            params=(timeframe, timeframe),
        )


def load_feature_history(symbol: str, timeframe: str = "1h", limit: int = 200) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT timestamp, close, rsi_14, macd, macd_signal, macd_hist,
                   bb_upper, bb_middle, bb_lower, ema_9, ema_21
            FROM features
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            conn,
            params=(symbol, timeframe, limit),
        )
    df["datetime"] = to_local(df["timestamp"])
    return df.sort_values("datetime")


with tab_feat:
    tf_feat = st.selectbox("Timeframe", ["1h", "5m"], key="feat_tf")
    feat_df = load_latest_features(tf_feat)

    if feat_df.empty:
        st.info("Noch keine Feature-Daten – `python features.py` ausführen.")
    else:
        st.subheader("Aktuelle Indikatoren")

        def signal(row: pd.Series) -> str:
            score = 0
            if row["rsi_14"] < 35:   score += 1
            if row["rsi_14"] > 65:   score -= 1
            if row["macd_hist"] > 0: score += 1
            if row["macd_hist"] < 0: score -= 1
            if row["bb_pct_b"] < 0.2: score += 1
            if row["bb_pct_b"] > 0.8: score -= 1
            if score >= 2:  return "🟢 Kaufsignal"
            if score <= -2: return "🔴 Verkaufssignal"
            return "⚪ Neutral"

        display = feat_df.copy()
        display["Signal"]    = display.apply(signal, axis=1)
        display["RSI (14)"]  = display["rsi_14"].round(1)
        display["MACD Hist"] = display["macd_hist"].apply(lambda x: f"{x:+.4f}")
        display["BB %B"]     = display["bb_pct_b"].round(2)
        display["Ret 1K"]    = display["ret_1"].apply(lambda x: f"{x*100:+.2f}%" if pd.notna(x) else "–")
        display["Ret 6K"]    = display["ret_6"].apply(lambda x: f"{x*100:+.2f}%" if pd.notna(x) else "–")
        display["Vol Ratio"] = display["vol_ratio"].round(2)

        st.dataframe(
            display[["symbol", "Signal", "RSI (14)", "MACD Hist", "BB %B", "Ret 1K", "Ret 6K", "Vol Ratio"]],
            width="stretch", hide_index=True,
        )

        st.divider()

        st.subheader("Detail")
        symbols_feat = sorted(feat_df["symbol"].tolist())
        sel_feat = st.selectbox(
            "Symbol", symbols_feat,
            index=symbols_feat.index("BTC/EUR") if "BTC/EUR" in symbols_feat else 0,
            key="feat_sym",
        )
        hist = load_feature_history(sel_feat, tf_feat, limit=150)

        if not hist.empty:
            fig_price = go.Figure()
            fig_price.add_trace(go.Scatter(x=hist["datetime"], y=hist["close"], name="Kurs", line=dict(color=INK, width=2)))
            fig_price.add_trace(go.Scatter(x=hist["datetime"], y=hist["ema_9"],  name="EMA 9",  line=dict(color=GREEN, dash="dot")))
            fig_price.add_trace(go.Scatter(x=hist["datetime"], y=hist["ema_21"], name="EMA 21", line=dict(color=RED, dash="dot")))
            fig_price.add_traces([
                go.Scatter(x=hist["datetime"], y=hist["bb_upper"],  name="BB oben",  line=dict(color="rgba(74,114,184,0.45)")),
                go.Scatter(x=hist["datetime"], y=hist["bb_middle"], name="BB mitte", line=dict(color="rgba(74,114,184,0.25)")),
                go.Scatter(x=hist["datetime"], y=hist["bb_lower"],  name="BB unten", line=dict(color="rgba(74,114,184,0.45)"),
                           fill="tonexty", fillcolor="rgba(74,114,184,0.06)"),
            ])
            style_fig(fig_price, height=350, title=f"{sel_feat} – Kurs + Bollinger Bands + EMA")
            fig_price.update_layout(xaxis_rangeslider_visible=False)
            st.plotly_chart(fig_price, width="stretch")

            fig_rsi = go.Figure()
            fig_rsi.add_trace(go.Scatter(x=hist["datetime"], y=hist["rsi_14"], name="RSI 14", line=dict(color=AMBER, width=2)))
            fig_rsi.add_hline(y=70, line_dash="dash", line_color=RED,   annotation_text="Überkauft")
            fig_rsi.add_hline(y=30, line_dash="dash", line_color=GREEN, annotation_text="Überverkauft")
            style_fig(fig_rsi, height=220, title="RSI (14)")
            fig_rsi.update_layout(yaxis=dict(range=[0, 100]), showlegend=False)
            st.plotly_chart(fig_rsi, width="stretch")

            colors = [GREEN if v >= 0 else RED for v in hist["macd_hist"]]
            fig_macd = go.Figure()
            fig_macd.add_trace(go.Bar(x=hist["datetime"], y=hist["macd_hist"], name="Histogramm", marker_color=colors))
            fig_macd.add_trace(go.Scatter(x=hist["datetime"], y=hist["macd"],        name="MACD",   line=dict(color=INK)))
            fig_macd.add_trace(go.Scatter(x=hist["datetime"], y=hist["macd_signal"], name="Signal", line=dict(color=VIOLET)))
            style_fig(fig_macd, height=220, title="MACD")
            st.plotly_chart(fig_macd, width="stretch")


# ══ Tab 7: ML-Signale ═════════════════════════════════════════════════════════

def load_signals() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals'"
        ).fetchone()
        if not has:
            return pd.DataFrame()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(signals)")]
        down_col = "s.prob_down" if "prob_down" in cols else "NULL AS prob_down"
        return pd.read_sql_query(
            f"""SELECT s.symbol, s.signal, s.confidence, s.prob_up, {down_col}, s.close, s.timestamp
               FROM signals s
               INNER JOIN (
                   SELECT symbol, MAX(timestamp) AS max_ts FROM signals GROUP BY symbol
               ) latest ON s.symbol = latest.symbol AND s.timestamp = latest.max_ts
               ORDER BY s.prob_up DESC""",
            conn,
        )


def load_signal_history(symbol: str) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals'"
        ).fetchone()
        if not has:
            return pd.DataFrame()
        df = pd.read_sql_query(
            """SELECT timestamp, signal, prob_up, confidence, close
               FROM signals WHERE symbol = ? ORDER BY timestamp""",
            conn, params=(symbol,),
        )
    df["datetime"] = to_local(df["timestamp"])
    return df


with tab_sig:
    sig_df = load_signals()

    if sig_df.empty:
        st.info("Noch keine ML-Signale. Führe zuerst aus:\n```\nvenv\\Scripts\\activate\npython model.py\n```")
    else:
        ts_sig = datetime.fromtimestamp(sig_df["timestamp"].max() / 1000)
        st.caption(f"Signale generiert: {ts_sig.strftime('%d.%m.%Y %H:%M:%S')}")

        buy_df  = sig_df[sig_df["signal"] == "BUY"].head(3)
        sell_df = sig_df[sig_df["signal"] == "SELL"].tail(3)

        st.subheader("Stärkste Signale")
        col_b, col_s = st.columns(2)

        with col_b:
            st.markdown("##### 🟢 Kaufsignale (höchste P(up))")
            for _, r in buy_df.iterrows():
                st.metric(
                    label=r["symbol"],
                    value=f"{r['close']:,.4f} €",
                    delta=f"P(up) {r['prob_up']*100:.0f}%  |  Konfidenz {r['confidence']*100:.0f}%",
                )

        with col_s:
            st.markdown("##### 🔴 Verkaufssignale (niedrigste P(up))")
            for _, r in sell_df.sort_values("prob_up").iterrows():
                st.metric(
                    label=r["symbol"],
                    value=f"{r['close']:,.4f} €",
                    delta=f"P(up) {r['prob_up']*100:.0f}%  |  Konfidenz {r['confidence']*100:.0f}%",
                    delta_color="inverse",
                )

        st.divider()

        st.subheader("Alle Symbole")
        disp = sig_df.copy()
        disp["Signal"]    = disp["signal"].map({"BUY": "🟢 BUY", "SELL": "🔴 SELL"})
        disp["P(up)"]     = (disp["prob_up"] * 100).round(1).astype(str) + "%"
        disp["P(down)"]   = disp["prob_down"].apply(
            lambda x: f"{x*100:.1f}%" if pd.notna(x) else "–")
        disp["Konfidenz"] = (disp["confidence"] * 100).round(1).astype(str) + "%"
        disp["Kurs (€)"]  = disp["close"].apply(lambda x: f"{x:,.4f}")
        st.dataframe(
            disp[["symbol", "Signal", "P(up)", "P(down)", "Konfidenz", "Kurs (€)"]],
            width="stretch", hide_index=True,
        )

        st.divider()

        st.subheader("P(up) Übersicht")
        fig_bar = go.Figure(go.Bar(
            x=(sig_df["prob_up"] * 100).round(1),
            y=sig_df["symbol"],
            orientation="h",
            marker_color=[GREEN if p >= 0.5 else RED for p in sig_df["prob_up"]],
            text=(sig_df["prob_up"] * 100).round(1).astype(str) + "%",
            textposition="outside",
        ))
        fig_bar.add_vline(x=50, line_dash="dash", line_color=INK, opacity=0.4)
        style_fig(fig_bar, height=520)
        fig_bar.update_layout(
            xaxis_title="Wahrscheinlichkeit Preisanstieg (%)",
            xaxis=dict(range=[0, 100]),
        )
        st.plotly_chart(fig_bar, width="stretch")

        if len(sig_df) > 0:
            st.divider()
            st.subheader("Signal-Verlauf")
            sel_sig = st.selectbox("Symbol", sig_df["symbol"].tolist(), key="sig_sym")
            hist_sig = load_signal_history(sel_sig)
            if len(hist_sig) > 1:
                fig_hist = go.Figure()
                fig_hist.add_trace(go.Scatter(
                    x=hist_sig["datetime"],
                    y=hist_sig["prob_up"] * 100,
                    name="P(up) %",
                    fill="tozeroy",
                    line=dict(color=INK, width=2),
                    fillcolor="rgba(58,55,47,0.05)",
                ))
                fig_hist.add_hline(y=50, line_dash="dash",
                                   line_color=INK, opacity=0.4,
                                   annotation_text="50% – Neutral")
                style_fig(fig_hist, height=280, title=f"{sel_sig} – P(up) Verlauf")
                fig_hist.update_layout(yaxis=dict(range=[0, 100], title="P(up) %"))
                st.plotly_chart(fig_hist, width="stretch")
