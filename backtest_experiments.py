"""Parameter-Experimente: Konfigurationen gegen die Baseline messen.

Referenz mit ALTEN Features (2026-06-11, Zeitraum 2024-05..2026-06, BTC B&H -13.1%):
  Baseline (Live-Config)            -42.9%  WR 47.6%  PF 0.80
  D: Trailing-only + 0.68/0.70      -15.7%  WR 59.7%  PF 0.85   <- beste alte Variante

Dieser Lauf nutzt die NEUEN Features (mom_24/72, Zeit-Saison, BTC-Cross-Asset).
Jeder run_backtest trainiert intern frisch auf dem Train-Split -> misst direkt
den Effekt der neuen Features (+ optional Vola-Sizing).
"""
import logging

from backtest import run_backtest

logging.basicConfig(level=logging.WARNING)

VARIANTS = {
    "N1: Live-Config (SL 0.15)":            dict(),
    "N2: Trailing-only 0.68/0.70":          dict(use_fixed_stop=False),
    "N3: Trailing-only + Vola-Sizing":      dict(use_fixed_stop=False, vol_target=0.015),
}

print(f"{'Variante':<36} {'Return':>8} {'WinRate':>8} {'PF':>6} {'Sharpe':>7} "
      f"{'MaxDD':>7} {'Trades':>7}")
print("-" * 86)
print(f"{'[alt] D: Trailing 0.68/0.70':<36} {'-15.7%':>8} {'59.7%':>8} {'0.85':>6} "
      f"{'-0.77':>7} {'-19.4%':>7} {'655':>7}")
print("-" * 86)

for name, params in VARIANTS.items():
    res = run_backtest(save=False, **params)
    if "error" in res:
        print(f"{name:<36} FEHLER: {res['error']}")
        continue
    m = res["metrics"]
    pf = f"{m['profit_factor']}" if m["profit_factor"] is not None else "inf"
    print(f"{name:<36} {m['total_return']:>7.1f}% {m['win_rate']:>7.1f}% {pf:>6} "
          f"{m['sharpe']:>7.2f} {m['max_drawdown']:>6.1f}% {m['n_trades']:>7}")
