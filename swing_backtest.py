"""Backtest des setups swing (2-10 jours), avec frais réels.

  python swing_backtest.py           # données Yahoo, écrit swing_report.md
  python swing_backtest.py --send    # idem + envoi Telegram
  python swing_backtest.py --demo    # données synthétiques (test de la mécanique seulement)

Méthode : 3 setups fixés à l'avance x 3 durées (3, 5, 10 séances) = 9 tests seulement, donc pas de chasse aux
paramètres. Chaque setup est comparé à des entrées « sans signal » avec exactement les mêmes sorties (stop, objectif,
délai) et les mêmes frais. L'écart est testé mois par mois (les valeurs bougent ensemble : un t calculé trade par trade
serait trop optimiste), avec un seuil relevé (t >= 2,5) parce qu'on fait 9 tests.
"""
import os
import sys
import math
import datetime as dt

import numpy as np
import pandas as pd

import swing as sw
from backtest import PANEL, PERSO, START

HOLDS = (3, 5, 10)
T_MIN, N_MIN = 2.5, 300
RNG = np.random.default_rng(7)


def load(t):
    import yfinance as yf
    df = yf.download(t, start=START, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "Low", "Close", "Volume"]].dropna(subset=["Open", "Low", "Close"])
    return df if len(df) > 500 else None


def demo(t, n=3800):
    r = RNG.normal(0.0003, 0.015, n)
    c = 100 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]] * np.exp(RNG.normal(0, 0.004, n))
    low = np.minimum(o, c) * (1 - np.abs(RNG.normal(0, 0.006, n)))
    return pd.DataFrame({"Open": o, "Low": low, "Close": c, "Volume": RNG.integers(1e5, 1e6, n).astype(float)},
                        index=pd.bdate_range("2008-01-01", periods=n))


def simulate(dfs, cats, strat, hold):
    """Trades du setup (non chevauchants par valeur) et trades de référence (mêmes sorties, jours sans signal)."""
    target, trend_base = sw.TARGET_EXIT[strat], sw.TREND_BASE[strat]
    rows_s, rows_b = [], []
    for t, df in dfs.items():
        cat = cats[t]
        sig, arr, c = sw.signals(df), sw.arrays(df), sw.cost_rt(cats[t])
        stop = sw.STOP[cat]
        dates = df.index

        def run(mask, stride, store):
            idx = np.where(mask.values)[0]
            nxt = 0
            for i in idx[::stride] if stride > 1 else idx:
                if i < nxt:
                    continue
                r = sw.trade(arr, int(i), hold, stop, target)
                if r is None:
                    continue
                store.append((t, dates[i + 1], r[0] - c, r[1], r[2]))
                if stride == 1:
                    nxt = r[3]
        run(sig[strat] & sig.valid, 1, rows_s)
        base = (sig.trend if trend_base else sig.valid) & sig.valid & ~sig[strat]
        run(base, 3, rows_b)
    cols = ["ticker", "date", "net", "days", "why"]
    return pd.DataFrame(rows_s, columns=cols), pd.DataFrame(rows_b, columns=cols)


def evaluate(s, b):
    if len(s) < 30 or len(b) < 100:
        return None
    pf_den = -s.net[s.net < 0].sum()
    ms = s.groupby(s.date.dt.to_period("M")).net.agg(["mean", "size"])
    mb = b.groupby(b.date.dt.to_period("M")).net.mean()
    d = (ms[ms["size"] >= 3]["mean"] - mb).dropna()
    t = d.mean() / (d.std() / math.sqrt(len(d))) if len(d) > 12 and d.std() > 0 else np.nan
    mid = s.date.median()
    def edge(sa, ba):
        return sa.net.mean() - ba.net.mean() if len(sa) > 20 and len(ba) > 20 else np.nan
    h1, h2 = edge(s[s.date <= mid], b[b.date <= mid]), edge(s[s.date > mid], b[b.date > mid])
    ys = s.groupby(s.date.dt.year).net.mean() - b.groupby(b.date.dt.year).net.mean()
    cnt = s.groupby(s.date.dt.year).size()
    ys = ys[cnt.reindex(ys.index) >= 5].dropna()
    years = max((s.date.max() - s.date.min()).days / 365.25, 1)
    return {"n": len(s), "par_an": len(s) / years, "win": (s.net > 0).mean(), "avg": s.net.mean(),
            "med": s.net.median(), "pf": s.net[s.net > 0].sum() / pf_den if pf_den else np.nan,
            "stop": (s.why == "stop").mean(), "base": b.net.mean(), "edge": s.net.mean() - b.net.mean(),
            "t": t, "h1": h1, "h2": h2, "yrs": (ys > 0).mean() if len(ys) else np.nan, "nyrs": len(ys)}


def passed(e):
    return (e is not None and e["n"] >= N_MIN and e["avg"] > 0 and e["edge"] > 0 and e["t"] >= T_MIN
            and e["h1"] > 0 and e["h2"] > 0 and e["yrs"] >= 0.6)


def pc(x, d=2):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f} %".replace(".", ",")


def nm(x):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.2f}".replace(".", ",")


def table(rows, header):
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
                     + ["| " + " | ".join(map(str, r)) + " |" for r in rows])


def main():
    args = sys.argv[1:]
    is_demo = "--demo" in args
    dfs, cats = {}, {}
    for t, cat in PANEL + PERSO:
        if t in dfs:
            continue
        try:
            df = demo(t) if is_demo else load(t)
        except Exception as e:
            print("échec", t, e)
            df = None
        if df is not None:
            dfs[t], cats[t] = df, cat
    if len(dfs) < 10:
        sys.exit("Pas assez de données (Yahoo injoignable ?).")

    L = [f"# Backtest swing — {dt.date.today()}"]
    if is_demo:
        L.append("\n> **DONNÉES SYNTHÉTIQUES : test de la mécanique, aucun résultat n'a de valeur.**")
    L.append(f"\n{len(dfs)} valeurs, depuis {START}. Signal à la clôture de J, achat à l'ouverture de J+1. "
             f"Frais : 1 € par ordre sur {sw.SIZE:.0f} € (soit {sw.FEE * 2 / sw.SIZE * 100:.2f} % l'aller-retour) + écart achat/vente "
             "(ETF 0,10 %, grandes 0,20 %, paris 0,80 %). Stops : ETF 3 %, grandes 5 %, paris 10 % (sortie à l'ouverture si le "
             "cours ouvre sous le stop). **Avant impôt** (flat tax 30 % sur les gains en compte-titres). "
             "9 tests seulement (3 setups x 3 durées), fixés à l'avance.\n")
    results, trades_all = {}, {}
    for strat in sw.STRATS:
        for hold in HOLDS:
            s, b = simulate(dfs, cats, strat, hold)
            results[(strat, hold)] = evaluate(s, b)
            trades_all[(strat, hold)] = s
    rows = []
    for (strat, hold), e in results.items():
        if e is None:
            rows.append([strat, hold] + ["trop peu de trades"] + [""] * 11)
            continue
        rows.append([strat, hold, e["n"], f"{e['par_an']:.0f}", pc(e["win"], 0), pc(e["avg"]), pc(e["med"]), nm(e["pf"]),
                     pc(e["stop"], 0), pc(e["base"]), pc(e["edge"]), nm(e["t"]), f"{pc(e['h1'])} / {pc(e['h2'])}",
                     f"{pc(e['yrs'], 0)} ({e['nyrs']} a.)", "✅" if passed(e) else "❌"])
    L.append("## Résultats nets de frais, par setup et durée maximale (séances)\n")
    L.append(table(rows, ["Setup", "Durée", "Trades", "/an", "Gagnants", "Gain moyen", "Médiane", "Profit factor",
                          "Part de stops", "Réf. sans signal", "Avantage", "t (mensuel)", "Avantage 1re / 2e moitié",
                          "Années positives", "Validé"]))
    L.append("\n*Avantage = gain moyen du setup − gain moyen d'entrées sans signal avec les mêmes sorties. "
             f"Validé = au moins {N_MIN} trades, gain net > 0, avantage > 0 avec t ≥ {T_MIN}, positif dans les deux moitiés de la période et "
             "dans au moins 60 % des années.*\n")
    ok = [(k, e) for k, e in results.items() if passed(e)]
    L.append("## Verdict\n")
    if not ok:
        L.append("**Aucun setup n'est validé.** Sur ces données, les signaux court terme testés ne battent pas des entrées "
                 "sans signal une fois les frais payés. Le bot ne proposera donc pas d'entrées swing ; il continue de "
                 "t'alerter sur les gros mouvements et de suivre tes positions (stop, délai).")
        cfg_snip = '"swing": {"strategies": {}}'
    else:
        best = {}
        for (s_, h_), e in ok:
            if s_ not in best or e["t"] > best[s_][1]["t"]:
                best[s_] = (h_, e)
        L.append("Validés : " + ", ".join(f"**{s_}** ({h_} séances, gain net moyen {pc(e['avg'])}, avantage {pc(e['edge'])}, t = {nm(e['t'])})"
                                         for s_, (h_, e) in best.items()) + ".\n")
        cfg_snip = '"swing": {"strategies": {' + ", ".join(f'"{s_}": {h_}' for s_, (h_, e) in best.items()) + "}}"
        L.append("Un setup validé n'est pas une promesse : le gain moyen par trade est faible, des séries de pertes sont normales "
                 "(voir ci-dessous) et la période testée comprend des marchés très haussiers.")
    L.append(f"\nÀ copier dans `config.json` (remplace la ligne `\"swing\"`) :\n\n```\n{cfg_snip}\n```\n")

    # série de pertes et pire mois pour le meilleur setup (ou le moins mauvais)
    key = max(results, key=lambda k: -1e9 if results[k] is None else results[k]["edge"])
    s = trades_all[key].sort_values("date")
    run = mx = 0
    for v in s.net:
        run = run + 1 if v < 0 else 0
        mx = max(mx, run)
    L.append(f"## Ce que ça fait vivre ({key[0]}, {key[1]} séances)\n")
    L.append(f"- Plus longue série de trades perdants : **{mx}**.\n- Pire trade : {pc(s.net.min())} ; meilleur : {pc(s.net.max())}.\n"
             f"- Pire mois (moyenne des trades) : {pc(s.groupby(s.date.dt.to_period('M')).net.mean().min())}.\n")
    L.append("*Limites : bougies journalières seulement (l'ordre exact des hauts/bas dans la journée est inconnu : "
             "si le stop et l'objectif sont touchés le même jour, c'est le stop qui est supposé) ; pas de glissement en cas de "
             "gap violent au-delà du modèle ; valeurs actuelles (léger biais de survie) ; les ETF PEA ont peu d'historique. "
             "Pas un conseil en investissement.*")
    report = "\n".join(L)
    open("swing_report.md", "w").write(report)
    print(report)
    if "--send" in args:
        import requests
        tok, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
        if tok and chat:
            txt = ("📊 Backtest swing terminé\n\n" + ("Validé : " + ", ".join(f"{k[0]} ({k[1]} séances)" for k, _ in ok)
                   if ok else "Aucun setup validé : pas d'entrées swing proposées.") + "\n\nRapport en pièce jointe.")
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": txt}, timeout=20)
            requests.post(f"https://api.telegram.org/bot{tok}/sendDocument", data={"chat_id": chat},
                          files={"document": open("swing_report.md", "rb")}, timeout=60)


if __name__ == "__main__":
    main()
