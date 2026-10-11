"""Signaux swing (2 à 10 jours), partagés par le bot et swing_backtest.py : un seul code, donc ce qui est testé
est exactement ce que le bot signale.

Trois setups classiques, FIXÉS À L'AVANCE (pas cherchés dans les données) :
  pullback     : repli dans une tendance haussière (RSI 2 jours < 10, clôture sous la moyenne 5 jours)
  breakout     : cassure, plus haut 20 jours avec volume 1,5x la normale, en tendance haussière
  capitulation : chute brutale (3 jours <= -2,5 écarts-types), rebond possible

Signal à la clôture de J, entrée à l'ouverture de J+1. Sorties : stop, sortie « objectif » (clôture au-dessus
de la moyenne 5 jours, mean-reversion seulement) ou délai maximal.
"""
import numpy as np
import pandas as pd

STRATS = {
    "pullback": "repli dans une tendance haussière (RSI 2 j < 10, sous sa moyenne 5 j)",
    "breakout": "cassure : plus haut 20 j avec volume 1,5x la normale, en tendance haussière",
    "capitulation": "chute brutale (3 j <= -2,5 écarts-types) : rebond possible",
}
TARGET_EXIT = {"pullback": True, "breakout": False, "capitulation": True}
TREND_BASE = {"pullback": True, "breakout": True, "capitulation": False}   # baseline : jours en tendance ou tous
STOP = {"etf": 0.03, "grande": 0.05, "pari": 0.10}
COST_RT = {"etf": 0.0010, "grande": 0.0020, "pari": 0.0080}   # écart achat/vente aller-retour
FEE = 1.0                                                     # € par ordre (Trade Republic)
SIZE = 1500.0                                                 # mise moyenne en €, pour les frais fixes


def cost_rt(cat):
    return COST_RT[cat] + 2 * FEE / SIZE


def rsi_n(c, n):
    d = c.diff()
    up, dn = d.clip(lower=0).rolling(n).mean(), (-d.clip(upper=0)).rolling(n).mean()
    return (100 - 100 / (1 + up / dn)).where(dn != 0, 100.0)


def signals(df):
    """DataFrame de booléens (une colonne par setup) calculés à la clôture de chaque jour."""
    c = df.Close.astype(float)
    sma200, sma5 = c.rolling(200).mean(), c.rolling(5).mean()
    trend = c > sma200
    sig20 = c.pct_change().rolling(20).std()
    out = pd.DataFrame(index=df.index)
    out["pullback"] = trend & (rsi_n(c, 2) < 10) & (c < sma5)
    brk = trend & (c >= c.rolling(20).max())
    if "Volume" in df:
        v = df.Volume.astype(float)
        brk = brk & (v > 1.5 * v.rolling(20).mean())
    out["breakout"] = brk
    out["capitulation"] = (c / c.shift(3) - 1) <= -2.5 * sig20 * np.sqrt(3)
    out["trend"] = trend
    out["valid"] = sma200.notna() & sig20.notna()
    return out.fillna(False).astype(bool)


def arrays(df):
    return (df.Open.values.astype(float), df.Low.values.astype(float), df.Close.values.astype(float),
            df.Close.rolling(5).mean().values.astype(float))


def trade(arr, i, hold, stop, target):
    """Trade déclenché par un signal à la clôture de i. Retourne (rendement brut, séances, motif, indice de sortie)
    ou None si l'historique s'arrête avant la fin du trade."""
    O, L, C, S5 = arr
    e, n = i + 1, len(O)
    if e >= n:
        return None
    entry = O[e]
    stop_px = entry * (1 - stop)
    for k in range(hold):
        j = e + k
        if j >= n:
            return None
        if L[j] <= stop_px:
            px = stop_px if k == 0 else min(O[j], stop_px)         # ouverture sous le stop : on sort à l'ouverture
            return px / entry - 1, k + 1, "stop", j
        if target and C[j] > S5[j]:
            if j + 1 >= n:
                return None
            return O[j + 1] / entry - 1, k + 1, "objectif", j + 1
    x = e + hold
    if x >= n:
        return None
    return O[x] / entry - 1, hold, "délai", x
