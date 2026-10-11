"""Backtest des setups sur 10 ans, avec les MÊMES règles que le bot (setups.py), frais et écarts inclus.
Écrit stats.json (lu par le bot pour afficher « validé / non validé ») et report.md.

Un setup est « validé » pour une catégorie si TOUT est vrai :
  - au moins 100 trades ;
  - rendement net moyen > 0 après frais ;
  - il bat d'au moins 0,10 point par trade une entrée sans signal avec les mêmes sorties, de façon statistiquement
    significative (t >= 2,5 : les trades simultanés étant corrélés, ce seuil est volontairement strict) ;
  - il est positif sur au moins 60 % des années (années avec >= 5 trades, minimum 3 années) ;
  - il est positif sur les 3 dernières années (si >= 20 trades sur la période).
Ces seuils sont des garde-fous, pas une preuve : un setup validé peut cesser de marcher.
"""
import datetime as dt

import numpy as np
import pandas as pd

from . import data, setups as S
from .config import ROOT
from .fmt import pct
from .indicators import compute

MIN_N, MIN_EDGE, MIN_POS_YEARS, MIN_YEARS, MIN_T = 100, 0.001, 0.6, 3, 2.5


def summarize(trades, base, now):
    n = len(trades)
    if n == 0:
        return None
    r = np.array([t["ret"] for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    by_year = {}
    for t in trades:
        by_year.setdefault(t["date"].year, []).append(t["ret"])
    yrs = {y: float(np.mean(v)) for y, v in by_year.items() if len(v) >= 5}
    pos_share = (sum(v > 0 for v in yrs.values()) / len(yrs)) if yrs else 0.0
    cutoff = pd.Timestamp(now.date()) - pd.DateOffset(years=3)
    rec = np.array([t["ret"] for t in trades if t["date"] >= cutoff])
    base_mean = float(np.mean(base)) if len(base) else 0.0
    mean = float(r.mean())
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    out = {
        "t": (mean - base_mean) / (sd / np.sqrt(n)) if sd > 0 else 0.0,
        "n": n, "win": float((r > 0).mean()), "mean": mean, "median": float(np.median(r)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0, "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "pf": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        "days": float(np.mean([t["days"] for t in trades])), "stop_share": float(np.mean([t["why"] == "stop" for t in trades])),
        "base": base_mean, "base_n": len(base), "edge": mean - base_mean,
        "pos_years": pos_share, "n_years": len(yrs), "n_recent": int(len(rec)),
        "mean_recent": float(rec.mean()) if len(rec) else None, "worst": float(r.min()),
    }
    out["validated"] = bool(
        n >= MIN_N and mean > 0 and out["edge"] >= MIN_EDGE and out["t"] >= MIN_T and len(yrs) >= MIN_YEARS and pos_share >= MIN_POS_YEARS
        and (out["n_recent"] < 20 or out["mean_recent"] > 0))
    return out


def run(cfg, now=None, period="10y"):
    now = now or dt.datetime.now(dt.timezone.utc)
    entries = {}
    for w in list(cfg["watchlist"]) + list(cfg.get("univers", [])) + list(cfg.get("calibration", [])):
        entries.setdefault(w["ticker"], w["cat"])
    raw = data.daily(list(entries), period=period)
    pool = {k: {c: {"t": [], "b": []} for c in ("etf", "grande", "pari", "all")} for k in S.SETUPS}
    per_ticker, used = {}, []
    for t, cat in entries.items():
        df = raw.get(t)
        if df is None or len(df) < 400:
            continue
        ind = compute(data.completed(df, t, now))
        sig = S.signals(ind)
        used.append(t)
        for name in S.SETUPS:
            tr, base = S.trades(ind, name, cat, sig), S.baseline(ind, name, cat, sig)
            for c in (cat, "all"):
                pool[name][c]["t"] += tr
                pool[name][c]["b"] += base
            if tr:
                per_ticker.setdefault(t, {})[name] = {
                    "n": len(tr), "win": float(np.mean([x["ret"] > 0 for x in tr])), "mean": float(np.mean([x["ret"] for x in tr]))}
    setups_stats = {}
    for name in S.SETUPS:
        setups_stats[name] = {}
        for c, d in pool[name].items():
            s = summarize(d["t"], d["b"], now)
            if s:
                setups_stats[name][c] = s
    return {"generated": str(now.date()), "period": period, "n_tickers": len(used), "tickers_used": used,
            "setups": setups_stats, "tickers": per_ticker}


CAT_LABEL = {"etf": "ETF / or", "grande": "grandes valeurs", "pari": "paris", "all": "toutes catégories"}


def report_md(st):
    out = [f"# Backtest swing — {st['generated']}",
           f"{st['n_tickers']} valeurs, {st['period']} d'historique, signal à la clôture, entrée à l'ouverture suivante, "
           f"frais et écarts inclus (voir `setups.py`).", "",
           "**Limites** : les valeurs de ta watchlist et les grandes capitalisations actuelles ont été choisies avec le recul "
           "(biais de survie) ; les résultats passés ne garantissent rien ; la référence « sans signal » utilise les mêmes sorties.", ""]
    for name, d in st["setups"].items():
        out += [f"## {S.SETUPS[name]['nom']}", f"_{S.SETUPS[name]['regle']}_", "",
                "| Catégorie | Trades | Gagnants | Moyen net | Sans signal | Avantage (t) | Années + | 3 dernières années | Stops | Validé |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for c in ("etf", "grande", "pari", "all"):
            s = d.get(c)
            if not s:
                continue
            rec = pct(s["mean_recent"], 2) if s["mean_recent"] is not None else "n/a"
            out.append(f"| {CAT_LABEL[c]} | {s['n']} | {s['win'] * 100:.0f} % | {pct(s['mean'], 2)} | {pct(s['base'], 2)} | "
                       f"{pct(s['edge'], 2)} (t={s['t']:.1f}) | {s['pos_years'] * 100:.0f} % ({s['n_years']} ans) | {rec} (n={s['n_recent']}) | "
                       f"{s['stop_share'] * 100:.0f} % | {'✅' if s['validated'] else '❌'} |")
        out.append("")
    return "\n".join(out)


def telegram_summary(st):
    lines = [f"🧪 <b>Backtest swing</b> · {st['n_tickers']} valeurs, {st['period']}, frais inclus", ""]
    nb_ok = 0
    for name, d in st["setups"].items():
        for c in ("etf", "grande", "pari"):
            s = d.get(c)
            if not s:
                continue
            nb_ok += s["validated"]
            lines.append(f"{'✅' if s['validated'] else '❌'} {S.SETUPS[name]['nom']} · {CAT_LABEL[c]} : {s['n']} trades, "
                         f"{s['win'] * 100:.0f} % gagnants, {pct(s['mean'], 2)}/trade (sans signal {pct(s['base'], 2)})")
    lines += ["", f"{nb_ok} combinaison(s) validée(s). Seules celles-ci apparaissent « 🟢 validé » dans les idées ; "
              "les autres restent signalées avec la mention « non validé ».",
              "Détails : report.md dans les Artifacts du run."]
    return "\n".join(lines)
