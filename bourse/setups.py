"""Les setups de swing (2 à 10 séances), partagés par le bot ET le backtest : ce qui est testé est exactement
ce qui est signalé. Les règles sont fixées à l'avance (classiques de la littérature), pas ajustées sur les données.

Signal à la clôture de J, entrée à l'ouverture de J+1. Sorties, dans cet ordre de priorité à chaque séance :
stop (ATR) > objectif (multiple du risque) > retour à la moyenne 5 j > délai maximal.
Si stop et objectif sont touchés dans la même séance, on suppose le stop (hypothèse prudente).
"""
import numpy as np
import pandas as pd

SETUPS = {
    "pullback": {
        "nom": "Repli en tendance haussière",
        "regle": "RSI 2 j < 10 et clôture sous sa moyenne 5 j, dans une tendance haussière (au-dessus des moyennes 50 et 200 j)",
        "hold": 5, "rr": None, "sma5_exit": True, "k": 1.5,
    },
    "breakout": {
        "nom": "Cassure avec volume",
        "regle": "plus haut de clôture sur 20 j avec un volume > 1,5x la normale, dans une tendance haussière",
        "hold": 7, "rr": 2.0, "sma5_exit": False, "k": 2.0,
    },
    "capitulation": {
        "nom": "Rebond après chute brutale",
        "regle": "baisse sur 3 j <= −2,5 écarts-types (volatilité habituelle de la valeur) : rebond possible",
        "hold": 5, "rr": None, "sma5_exit": True, "k": 2.0,
    },
}
# Position suivie sans setup précis (/achat nvidia) : paramètres génériques
GENERIC = {"nom": "Trade swing", "regle": "", "hold": 7, "rr": 2.0, "sma5_exit": False, "k": 2.0}

STOP_BOUNDS = {"etf": (0.015, 0.04), "grande": (0.03, 0.08), "pari": (0.06, 0.15)}   # distance du stop, en %
COST_RT = {"etf": 0.0010, "grande": 0.0020, "pari": 0.0080}    # écart achat/vente, aller-retour
FEE = 1.0            # € par ordre (Trade Republic)
BT_SIZE = 1500.0     # mise moyenne supposée pour les frais fixes dans le backtest


def params(name):
    return SETUPS.get(name, GENERIC)


def stop_pct(cat, atr_pct, k):
    lo, hi = STOP_BOUNDS[cat]
    if atr_pct is None or not np.isfinite(atr_pct):
        return (lo + hi) / 2
    return float(min(hi, max(lo, k * atr_pct)))


def cost_rt(cat, size=BT_SIZE, fee=FEE):
    return COST_RT[cat] + 2 * fee / max(size, 1.0)


def signals(ind):
    """Booléens (une colonne par setup + 'trend'), calculés à la clôture de chaque séance."""
    c = ind.Close.astype(float)
    trend = (c > ind.sma200) & (ind.sma50 > ind.sma200) & (c > ind.sma50)
    sig = ind.sig20.shift(3)       # volatilité habituelle AVANT la chute
    out = pd.DataFrame(index=ind.index)
    out["pullback"] = trend & (ind.rsi2 < 10) & (c < ind.sma5)
    out["breakout"] = trend & (c >= ind.hi20) & (ind.vol_rel > 1.5)
    out["capitulation"] = (c / c.shift(3) - 1) <= -2.5 * sig * np.sqrt(3)
    out["trend"] = trend
    out["valid"] = ind.sma200.notna() & ind.atr_pct.notna() & sig.notna()
    out = out.fillna(False).astype(bool)
    for k in SETUPS:
        out[k] &= out["valid"]
    return out


def arrays(ind):
    return {k: ind[c].values.astype(float) for k, c in
            (("O", "Open"), ("H", "High"), ("L", "Low"), ("C", "Close"), ("S5", "sma5"), ("A", "atr_pct"))}


def simulate(a, i, setup, cat):
    """Trade déclenché par un signal à la clôture de i.
    Retourne (rendement brut, séances, motif, indice de sortie) ou None si l'historique s'arrête avant la fin."""
    p = params(setup)
    O, H, L, C, S5 = a["O"], a["H"], a["L"], a["C"], a["S5"]
    n, e = len(O), i + 1
    if e >= n:
        return None
    sp = stop_pct(cat, a["A"][i], p["k"])
    entry = O[e]
    if not np.isfinite(entry) or entry <= 0:
        return None
    stop_px = entry * (1 - sp)
    tgt = entry * (1 + p["rr"] * sp) if p["rr"] else None
    for k in range(p["hold"]):
        j = e + k
        if j >= n:
            return None
        if L[j] <= stop_px:                                    # ouverture sous le stop : sortie à l'ouverture
            return min(O[j], stop_px) / entry - 1, k + 1, "stop", j
        if tgt is not None and H[j] >= tgt:                    # ouverture au-dessus de l'objectif : à l'ouverture
            return max(O[j], tgt) / entry - 1, k + 1, "objectif", j
        if p["sma5_exit"] and C[j] > S5[j]:
            if j + 1 >= n:
                return None
            return O[j + 1] / entry - 1, k + 1, "moyenne", j + 1
    x = e + p["hold"]
    if x >= n:
        return None
    return O[x] / entry - 1, p["hold"], "délai", x


def trades(ind, setup, cat, sig=None):
    """Tous les trades (non chevauchants) du setup sur l'historique : liste de dicts."""
    sig = signals(ind) if sig is None else sig
    a = arrays(ind)
    idx = np.flatnonzero(sig[setup].values)
    out, busy = [], -1
    for i in idx:
        if i < busy:
            continue
        r = simulate(a, int(i), setup, cat)
        if r is None:
            continue
        ret, days, why, x = r
        busy = x
        out.append({"date": ind.index[i], "ret": ret - cost_rt(cat), "gross": ret, "days": days, "why": why})
    return out


def baseline(ind, setup, cat, sig=None):
    """Rendement moyen net d'une entrée « sans signal » avec les mêmes sorties : toutes les séances éligibles
    (en tendance pour pullback/breakout, toutes pour capitulation). C'est la référence à battre."""
    sig = signals(ind) if sig is None else sig
    a = arrays(ind)
    mask = sig["valid"] & (sig["trend"] if setup != "capitulation" else True)
    rets = []
    for i in np.flatnonzero(mask.values):
        r = simulate(a, int(i), setup, cat)
        if r is not None:
            rets.append(r[0] - cost_rt(cat))
    return rets


def active(ind, sig=None):
    """Setups déclenchés à la dernière clôture."""
    sig = signals(ind) if sig is None else sig
    last = sig.iloc[-1]
    return [k for k in SETUPS if bool(last[k])]


def trade_plan(ind, cat, setup, cfg):
    """Plan chiffré à partir de la dernière clôture : entrée, stop, objectif, délai, mise."""
    p = params(setup)
    last = ind.iloc[-1]
    c = float(last.Close)
    sp = stop_pct(cat, float(last.atr_pct), p["k"])
    capital, risk = cfg["capital_swing"], cfg["risque_par_trade"]
    mise = min(capital * risk / sp, capital * cfg["mise_max_pct"])
    fee = cfg.get("frais_ordre", FEE)
    return {
        "close": c, "stop_pct": sp, "stop": c * (1 - sp),
        "target": c * (1 + p["rr"] * sp) if p["rr"] else None,
        "sma5": float(last.sma5) if p["sma5_exit"] else None,
        "max_entry": c * (1 + 0.5 * float(last.atr_pct)) if np.isfinite(last.atr_pct) else c * 1.01,
        "hold": p["hold"], "mise": mise, "risk_eur": mise * sp,
        "fees_pct": 2 * fee / mise if mise > 0 else 0.0,
        "illiquide": bool(np.isfinite(last.Volume) and last.Volume * c * 1.0 < 300_000 and cat != "etf"),
    }


def deadline(start_date, hold):
    """Date limite de sortie (ouverture de la séance start+hold) en jours ouvrés."""
    return pd.Timestamp(np.busday_offset(np.datetime64(pd.Timestamp(start_date).date()), hold, roll="forward")).date()
