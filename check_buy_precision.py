"""Praezision an der Handelsschwelle messen – die Metrik die fuer Profit zaehlt.

Trainiert auf den ersten 80% (zeitlich), testet auf den letzten 20%.
  Long-Seite : Wenn P(up)   >= Schwelle, wie oft steigt der Kurs wirklich um >0.6%?
  Short-Seite: Wenn P(down) >= Schwelle, wie oft faellt der Kurs wirklich um >0.6%?
Nur wenn die Praezision deutlich ueber der Basisrate liegt, hat der Bot
einen echten Edge nach Gebuehren.
"""
import logging

import numpy as np

from model import _build_pipeline, _pos_weight
from model_data import FEATURE_COLS, build_dataset

logging.basicConfig(level=logging.WARNING)


def evaluate_side(name: str, X_tr, y_tr, X_te, y_te) -> None:
    print(f"\n{'='*55}\n{name}\n{'='*55}")
    print(f"Basisrate im Test: {y_te.mean()*100:.1f}%")

    clf = _build_pipeline(n_estimators=300, scale_pos_weight=_pos_weight(y_tr))
    clf.fit(X_tr, y_tr)
    proba = clf.predict_proba(X_te)[:, 1]

    print(f"P-Verteilung: min={proba.min():.2f} median={np.median(proba):.2f} "
          f"max={proba.max():.2f}")
    print(f"{'Schwelle':>9} {'Signale':>8} {'Praezision':>11} {'vs. Basis':>10}")
    for thr in [0.50, 0.55, 0.60, 0.62, 0.65, 0.70, 0.75]:
        mask = proba >= thr
        n = int(mask.sum())
        if n == 0:
            print(f"{thr:>9.2f} {0:>8} {'-':>11} {'-':>10}")
            continue
        prec = float(y_te[mask].mean())
        lift = prec - float(y_te.mean())
        print(f"{thr:>9.2f} {n:>8} {prec*100:>10.1f}% {lift*100:>+9.1f}%")


if __name__ == "__main__":
    df = build_dataset()
    df = df[df["target"].notna()]
    available = [c for c in FEATURE_COLS if c in df.columns]

    X      = df[available].values.astype(float)
    y_up   = df["target"].values.astype(int)
    y_down = df["target_down"].values.astype(int)

    split = int(len(X) * 0.8)
    print(f"Train: {split} Zeilen | Test: {len(X) - split} Zeilen")

    evaluate_side("LONG-Seite: P(Kurs steigt >0.6% in 3h)",
                  X[:split], y_up[:split], X[split:], y_up[split:])
    evaluate_side("SHORT-Seite: P(Kurs faellt >0.6% in 3h)",
                  X[:split], y_down[:split], X[split:], y_down[split:])
