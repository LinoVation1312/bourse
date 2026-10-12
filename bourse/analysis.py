"""Analyse des valeurs : tendance, setups du jour, plans chiffrés, statistiques historiques, régime de marché."""
import datetime as dt

import numpy as np
import pandas as pd

from . import data, setups as S
from .config import scan_entries, entry_for
from .indicators import compute

MIN_BARS = 60          # 60 (et non 80) : permet de suivre une introduction en bourse récente (SpaceX)
MAX_STALE_DAYS = 5


def snapshot(entries, now, period="2y"):
    """{ticker: {w, ind}} pour les valeurs ayant des données fraîches (séance en cours exclue)."""
    raw = data.daily([w["ticker"] for w in entries], period=period)
    out = {}
    for w in entries:
        df = raw.get(w["ticker"])
        if df is None:
            continue
        df = data.completed(df, w["ticker"], now)
        if df is None or len(df) < MIN_BARS:
            continue
        if (now.date() - df.index[-1].date()).days > MAX_STALE_DAYS:
            continue
        out[w["ticker"]] = {"w": w, "ind": compute(df)}
    return out


def trend(ind):
    """('up'|'mixed'|'down', libellé) : filtre de risque, pas une prévision."""
    r = ind.iloc[-1]
    c = float(r.Close)
    pts = sum([c > r.sma50, c > r.sma200 if np.isfinite(r.sma200) else False,
               r.sma50 > r.sma200 if np.isfinite(r.sma200) else False])
    if pts == 3:
        return "up", "🟢 haussière"
    if pts == 0:
        return "down", "🔴 baissière"
    return "mixed", "⚪ mitigée"


SCOPE = {"etf": "ETF / or", "grande": "grandes valeurs", "pari": "paris spéculatifs"}


def stat_for(stats, setup, cat):
    """Statistiques historiques du setup pour LA catégorie de la valeur (pas de repli sur les autres catégories :
    valider un pari spéculatif avec des résultats de grandes valeurs serait trompeur). None si échantillon < 100."""
    cat_s = ((stats.get("setups") or {}).get(setup) or {}).get(cat)
    if cat_s and cat_s.get("n", 0) >= 100:
        return {**cat_s, "scope": SCOPE[cat]}
    return None


def ticker_stat(stats, ticker, setup):
    return ((stats.get("tickers") or {}).get(ticker) or {}).get(setup)


def next_session(last_bar_date):
    return pd.Timestamp(np.busday_offset(np.datetime64(last_bar_date), 1, roll="forward")).date()


def earnings_in_days(state, ticker, now):
    """Jours avant les prochains résultats (None si inconnu ou ETF). Mis en cache 3 jours."""
    c = state["earn"].get(ticker)
    today = now.date()
    if not c or (today - dt.date.fromisoformat(c["fetched"])).days >= 3:
        d = None
        try:
            import yfinance as yf
            cal = yf.Ticker(ticker).calendar
            v = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if v:
                v0 = v[0] if isinstance(v, (list, tuple)) else v
                d = str(pd.Timestamp(v0).date())
        except Exception:
            pass
        c = {"fetched": str(today), "date": d}
        state["earn"][ticker] = c
    if not c["date"]:
        return None
    n = (dt.date.fromisoformat(c["date"]) - today).days
    return n if n >= 0 else None


def find_ideas(snap, cfg, stats, state, now, exclude=()):
    """Setups déclenchés à la dernière clôture, classés : validés d'abord, puis par espérance historique."""
    ideas = []
    for t, s in snap.items():
        if t in exclude:
            continue
        w, ind = s["w"], s["ind"]
        sig = S.signals(ind)
        for name in S.active(ind, sig):
            st = stat_for(stats, name, w["cat"])
            plan = S.trade_plan(ind, w["cat"], name, cfg)
            ideas.append({"w": w, "setup": name, "plan": plan, "stat": st,
                          "tstat": ticker_stat(stats, t, name),
                          "validated": bool(st and st.get("validated")),
                          "bad": bool(st and not st.get("validated") and st.get("n", 0) >= 100 and st.get("mean", 0) < 0),
                          "bar": ind.index[-1].date(), "rsi2": float(ind.iloc[-1].rsi2),
                          "trend": trend(ind)[0]})
    ideas.sort(key=lambda i: (not i["validated"], i["bad"], -(i["stat"] or {}).get("mean", -1)))
    best = {}                                       # une seule idée par valeur (la meilleure), les autres en « aussi »
    for i in ideas:
        t = i["w"]["ticker"]
        if t in best:
            best[t].setdefault("also", []).append(S.SETUPS[i["setup"]]["nom"])
        else:
            best[t] = i
    top = list(best.values())[: cfg["swing"]["max_idees"]]
    for i in top:                                   # résultats : seulement pour les idées retenues (requêtes lentes)
        if i["w"]["cat"] != "etf":
            i["earn"] = earnings_in_days(state, i["w"]["ticker"], now)
            if i["earn"] is not None and i["earn"] <= int(i["plan"]["hold"] * 1.5) + 1:
                i["validated"] = False
                i["earn_risk"] = True
    return top


def regime(now):
    """Contexte de marché (informatif, non intégré au backtest) : S&P 500, CAC 40, VIX."""
    raw = data.daily(["SPY", "^FCHI", "^VIX"], period="1y")
    out = {}
    for t, label in (("SPY", "S&P 500"), ("^FCHI", "CAC 40")):
        df = raw.get(t)
        if df is None or len(df) < 200:
            continue
        df = data.completed(df, t, now)
        c = df.Close.astype(float)
        out[label] = {"above200": bool(c.iloc[-1] > c.rolling(200).mean().iloc[-1]),
                      "ret5": float(c.iloc[-1] / c.iloc[-6] - 1), "ret1": float(c.iloc[-1] / c.iloc[-2] - 1)}
    vix = raw.get("^VIX")
    if vix is not None and len(vix):
        out["VIX"] = float(vix.Close.iloc[-1])
    return out
