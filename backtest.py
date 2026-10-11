"""Backtest de l'avis du bot (mêmes règles que avis() dans bot.py).

  python backtest.py            # données Yahoo (10-15 ans), écrit report.md + graphiques
  python backtest.py --send     # idem + envoie le résumé et le rapport sur Telegram
  python backtest.py --selfcheck# vérifie que la version vectorisée donne EXACTEMENT les mêmes avis que bot.avis()
  python backtest.py --demo     # données synthétiques (test de la mécanique seulement, résultats sans valeur)

Garde-fous : signal calculé à la clôture de J, exécuté à l'ouverture de J+1 ; frais et écarts achat/vente ;
panier large (valeurs gagnantes ET perdantes) en plus de tes valeurs ; paramètres fixés à l'avance (pas
d'optimisation) ; test contre le hasard ; calibration de la confiance ; test de sensibilité ; test du levier.
"""
import os
import sys
import json
import math
import datetime as dt

import numpy as np
import pandas as pd

CFG = json.load(open("config.json"))
LEV = CFG["levier"]
START = "2007-01-01"
COST = {"etf": 0.0005, "grande": 0.0010, "pari": 0.0040}      # par changement de position (achat OU vente)
FIN_LEV = 0.035                                               # coût annuel du financement du levier x2 (+ frais)
DEFAULT = {"fast": 50, "slow": 200, "buy": 3, "sell": -3}
RNG = np.random.default_rng(42)

PANEL = (
    [(t, "etf") for t in ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "XLF", "XLE", "XLK", "XLU", "XLV", "TLT"]]
    + [(t, "grande") for t in ["AAPL", "AMZN", "GOOGL", "JPM", "XOM", "CVX", "KO", "PFE", "INTC", "IBM", "GE", "F",
                               "T", "VZ", "BA", "C", "WFC", "BAC", "CSCO", "ORCL", "JNJ", "WMT", "DIS", "MCD",
                               "NKE", "MMM", "CAT", "MRK", "PEP", "HD"]]
    + [(t, "pari") for t in ["PLUG", "FCEL", "RIOT", "AMD", "TSLA", "NIO"]]
)
PERSO = [(w["ticker"], w["cat"]) for w in CFG["watchlist"]]


# ------------------------------------------------------------ données
def load(t):
    import yfinance as yf
    df = yf.download(t, start=START, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "Close"]].dropna()
    return df if len(df) > 500 else None


def demo(t, n=3800):
    """Données synthétiques à régimes (haussier / baissier), UNIQUEMENT pour tester le code."""
    r = np.empty(n)
    reg, i = 1, 0
    while i < n:
        L = int(RNG.integers(60, 400))
        mu = 0.0007 if reg else -0.0009
        r[i:i + L] = RNG.normal(mu, 0.012 + 0.006 * RNG.random(), min(L, n - i))
        reg, i = 1 - reg if RNG.random() < 0.5 else reg, i + L
    c = 100 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]] * np.exp(RNG.normal(0, 0.003, n))
    return pd.DataFrame({"Open": o, "Close": c}, index=pd.bdate_range("2008-01-01", periods=n))


# ------------------------------------------------------------ signal (réplique vectorisée de bot.avis)
def signals(df, cat, p=DEFAULT):
    c = df.Close
    sma_f, sma_s = c.rolling(p["fast"]).mean(), c.rolling(p["slow"]).mean()
    mom = (c / c.shift(63) - 1).fillna(0)
    d = c.diff()
    up, dn = d.clip(lower=0).rolling(14).mean(), (-d.clip(upper=0)).rolling(14).mean()
    rsi = (100 - 100 / (1 + up / dn)).where(dn != 0, 100.0)
    vol = c.pct_change().rolling(60).std() * math.sqrt(252)
    valid = sma_s.notna() & rsi.notna() & vol.notna()

    score = pd.Series(np.where(c > sma_f, 1, -1), index=c.index).astype(float)
    score += np.where(sma_s.notna(), np.where(c > sma_s, 1, -1), 0)
    score += np.where(mom > 0.05, 1, np.where(mom < -0.05, -1, 0))
    score += np.where(rsi > 75, -1, np.where(rsi < 25, 1, 0))
    v = pd.Series(np.where(score >= p["buy"], 1, np.where(score <= p["sell"], -1, 0)), index=c.index)
    conf = np.where(v != 0, 50 + 10 * score.abs(), 70 - 10 * score.abs())
    conf = conf - np.where(vol > 0.60, 10, np.where(vol > 0.40, 5, 0))
    conf = pd.Series(np.clip(conf, 35, 85), index=c.index)
    lev = ((v == 1) & (cat != "pari") & (conf >= LEV["confiance_min"]) & (vol <= LEV["vol_max"])
           & (c > sma_s) & (rsi <= 70))
    return pd.DataFrame({"score": score, "v": v, "conf": conf, "lev": lev, "valid": valid})


# ------------------------------------------------------------ simulation d'un titre
def simulate(df, cat, p=DEFAULT):
    """Retours journaliers (ouverture J -> ouverture J+1). Position décidée à la clôture de J-1."""
    sig = signals(df, cat, p)
    r = df.Open.shift(-1) / df.Open - 1
    pos = pd.Series(np.where(sig.v == 1, 1.0, np.where(sig.v == -1, 0.0, np.nan)), index=df.index)
    pos = pos.where(sig.valid).ffill().fillna(0.0)
    held = pos.shift(1)
    mask = sig.valid.shift(1, fill_value=False) & r.notna()
    cost = COST[cat]
    strat = held * r - cost * held.diff().abs().fillna(0)
    expo = pd.Series(np.where((pos == 1) & sig.lev, 2.0, pos), index=df.index).shift(1)
    lev = expo * r - (expo - 1).clip(lower=0) * FIN_LEV / 252 - cost * expo.diff().abs().fillna(0)
    out = pd.DataFrame({"bh": r, "strat": strat, "lev": lev, "held": held, "expo": expo})
    out = out.where(mask)
    return out, sig


# ------------------------------------------------------------ statistiques
def stats(ret):
    ret = ret.dropna()
    if len(ret) < 250:
        return {"cagr": np.nan, "vol": np.nan, "sharpe": np.nan, "maxdd": np.nan, "calmar": np.nan}
    eq = (1 + ret).cumprod()
    cagr = eq.iloc[-1] ** (252 / len(ret)) - 1
    vol = ret.std() * math.sqrt(252)
    dd = (eq / eq.cummax() - 1).min()
    return {"cagr": cagr, "vol": vol, "sharpe": ret.mean() / ret.std() * math.sqrt(252) if ret.std() else np.nan,
            "maxdd": dd, "calmar": cagr / abs(dd) if dd else np.nan}


def portfolio(results, col):
    return pd.concat({k: v[col] for k, v in results.items()}, axis=1).mean(axis=1, skipna=True)


def f(x, kind="pct"):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return {"pct": f"{x * 100:.1f} %", "x": f"{x:.2f}"}[kind].replace(".", ",")


def table(rows, header):
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
                     + ["| " + " | ".join(map(str, r)) + " |" for r in rows])


# ------------------------------------------------------------ test contre le hasard
def permutation(results, n=300):
    """Décale au hasard chaque position dans le temps (même temps investi, même structure) et compare le Sharpe."""
    keys = list(results)
    R = pd.concat({k: results[k]["bh"] for k in keys}, axis=1)
    H = pd.concat({k: results[k]["held"] for k in keys}, axis=1)
    cost = np.array([COST[CATS[k]] for k in keys])
    Rv, Hv = R.values, H.values
    ok = ~np.isnan(Rv) & ~np.isnan(Hv)

    def sharpe(shift):
        P = np.where(ok, Hv, np.nan)
        if shift:
            for j in range(P.shape[1]):
                idx = np.where(ok[:, j])[0]
                if len(idx) > 10:
                    P[idx, j] = np.roll(Hv[idx, j], int(RNG.integers(60, len(idx) - 60)))
        dP = np.abs(np.diff(P, axis=0, prepend=np.nan))
        S = P * Rv - np.nan_to_num(dP) * cost
        S = np.where(ok, S, np.nan)
        port = np.nanmean(S, axis=1)
        port = port[~np.isnan(port)]
        return port.mean() / port.std() * math.sqrt(252)

    real = sharpe(False)
    rand = np.array([sharpe(True) for _ in range(n)])
    return real, rand, float((rand >= real).mean())


# ------------------------------------------------------------ calibration de la confiance
def calibration(sigs, dfs):
    rows = []
    for k, sig in sigs.items():
        o = dfs[k].Open
        fwd = o.shift(-21) / o.shift(-1) - 1                      # 20 séances, entrée à l'ouverture de J+1
        d = pd.DataFrame({"v": sig.v, "conf": sig.conf, "fwd": fwd})[sig.valid].dropna().iloc[::20]
        rows.append(d)
    d = pd.concat(rows)
    d["bucket"] = pd.cut(d.conf, [0, 49, 59, 69, 79, 100], labels=["<50", "50-59", "60-69", "70-79", "80+"])
    return d


# ------------------------------------------------------------ rapport
def main():
    global CATS
    args = sys.argv[1:]
    is_demo = "--demo" in args
    if "--selfcheck" in args:
        return selfcheck(is_demo)

    universe = {}
    for t, cat in PANEL + PERSO:
        if t in universe:
            continue
        df = demo(t) if is_demo else None
        if not is_demo:
            try:
                df = load(t)
            except Exception as e:
                print("échec", t, e)
        if df is not None:
            universe[t] = (df, cat)
    if len(universe) < 10:
        sys.exit("Pas assez de données téléchargées (Yahoo injoignable ?).")
    CATS = {t: c for t, (_, c) in universe.items()}
    dfs = {t: d for t, (d, _) in universe.items()}
    res, sigs = {}, {}
    for t, (df, cat) in universe.items():
        res[t], sigs[t] = simulate(df, cat)

    panel_keys = [t for t, _ in PANEL if t in res]
    perso_keys = [t for t, _ in PERSO if t in res]
    L = [f"# Backtest de l'avis du bot — {dt.date.today()}"]
    if is_demo:
        L.append("\n> **DONNÉES SYNTHÉTIQUES : test de la mécanique, aucun résultat n'a de valeur.**")
    L.append(f"\nPériode : depuis {START} (selon la cotation de chaque valeur). Panier : {len(panel_keys)} valeurs "
             f"(ETF, grandes valeurs gagnantes et perdantes, paris) ; tes valeurs : {len(perso_keys)}. "
             "Signal à la clôture de J, exécution à l'ouverture de J+1, frais : ETF 0,05 %, grandes 0,10 %, "
             "paris 0,40 % par changement. Aucun paramètre n'a été optimisé.\n")

    # 1) résultats poolés
    verdict = {}
    for name, keys in [("Panier large", panel_keys), ("Tes valeurs", perso_keys)]:
        bh, st = portfolio({k: res[k] for k in keys}, "bh"), portfolio({k: res[k] for k in keys}, "strat")
        exp = portfolio({k: res[k] for k in keys}, "held").mean()
        sb, ss = stats(bh), stats(st)
        part = stats(bh * exp)
        L.append(f"## 1{'a' if name.startswith('Panier') else 'b'}. {name} (portefeuille équipondéré)\n")
        L.append(table([
            ["Garder (buy & hold)", f(sb["cagr"]), f(sb["vol"]), f(sb["sharpe"], "x"), f(sb["maxdd"]), f(sb["calmar"], "x"), "100 %"],
            ["Garder, même exposition moyenne", f(part["cagr"]), f(part["vol"]), f(part["sharpe"], "x"), f(part["maxdd"]), f(part["calmar"], "x"), f(exp, "pct")],
            ["Règle du bot", f(ss["cagr"]), f(ss["vol"]), f(ss["sharpe"], "x"), f(ss["maxdd"]), f(ss["calmar"], "x"), f(exp, "pct")],
        ], ["", "Rend. annuel", "Volatilité", "Sharpe", "Pire baisse", "Calmar", "Temps investi"]))
        verdict[name] = (ss, sb)
        L.append("")

    # 2) par valeur (tes valeurs)
    rows = []
    for k in perso_keys:
        s_, b_ = stats(res[k]["strat"]), stats(res[k]["bh"])
        rows.append([k, f(b_["cagr"]), f(b_["maxdd"]), f(s_["cagr"]), f(s_["maxdd"]),
                     f(res[k]["held"].mean(), "pct")])
    L.append("## 2. Tes valeurs une par une\n")
    L.append(table(rows, ["Valeur", "Garder : rend./an", "Garder : pire baisse", "Règle : rend./an", "Règle : pire baisse", "Temps investi"]))
    L.append("\n*Attention : tes valeurs ont été choisies aujourd'hui (biais de sélection). Le panier large est la référence.*\n")

    # 3) hasard
    real, rand, p = permutation({k: res[k] for k in panel_keys})
    L.append("## 3. Test contre le hasard (panier large)\n")
    L.append(f"Sharpe de la règle : **{f(real, 'x')}**. Avec les mêmes périodes d'investissement placées au hasard "
             f"(300 tirages) : médiane {f(float(np.median(rand)), 'x')}, 95e centile {f(float(np.percentile(rand, 95)), 'x')}. "
             f"Probabilité que le hasard fasse aussi bien : **{p * 100:.0f} %**.\n")

    # 4) calibration
    cal = calibration({k: sigs[k] for k in panel_keys}, dfs)
    L.append("## 4. La confiance est-elle fiable ? (rendement à 20 séances après l'avis, panier large)\n")
    base = cal.fwd.mean()
    rows, means = [], {}
    for v, lab in [(1, "ACHETER"), (0, "GARDER"), (-1, "VENDRE")]:
        sub = cal[cal.v == v]
        means[v] = sub.fwd.mean()
        rows.append([lab, "tous", len(sub), f(sub.fwd.mean()), f((sub.fwd > 0).mean())])
        for b, g in sub.groupby("bucket", observed=True):
            if len(g) >= 30:
                rows.append(["", f"confiance {b}", len(g), f(g.fwd.mean()), f((g.fwd > 0).mean())])
    L.append(f"Rendement moyen sans condition : {f(base)}.\n")
    L.append(table(rows, ["Avis", "Tranche", "Observations", "Rend. moyen 20 séances", "Part positive"]))
    buy = cal[cal.v == 1]
    corr = buy.conf.rank().corr(buy.fwd.rank()) if len(buy) > 50 else np.nan
    sell, buyfwd = cal[cal.v == -1].fwd, buy.fwd
    se = math.sqrt(buyfwd.var() / max(len(buyfwd), 1) + sell.var() / max(len(sell), 1)) if len(sell) > 5 else np.nan
    tstat = (buyfwd.mean() - sell.mean()) / se if se else np.nan
    L.append(f"\nCorrélation confiance / rendement parmi les ACHETER : {f(corr, 'x')}. "
             f"Écart ACHETER − VENDRE : {f(buyfwd.mean() - sell.mean())} (t = {f(tstat, 'x')} ; |t| < 2 = non significatif). "
             "Le t est optimiste : les valeurs bougent ensemble, donc les observations ne sont pas indépendantes.\n")
    calib_ok = means[1] > means[0] > means[-1] and not np.isnan(corr) and corr > 0.03

    # 5) sensibilité
    L.append("## 5. Sensibilité aux paramètres (Sharpe du panier large)\n")
    grid, rows = {}, []
    for fa in (30, 50, 80):
        for sl in (150, 200, 250):
            for th in (2, 3):
                p_ = {"fast": fa, "slow": sl, "buy": th, "sell": -th}
                r_ = {k: simulate(dfs[k], CATS[k], p_)[0] for k in panel_keys}
                grid[(fa, sl, th)] = stats(portfolio(r_, "strat"))["sharpe"]
                if th == 3:
                    rows.append([f"{fa} / {sl}" + (" ← bot" if (fa, sl) == (50, 200) else ""),
                                 f(grid[(fa, sl, 2)], "x"), f(grid[(fa, sl, 3)], "x")])
    L.append(table(rows, ["Moyennes rapide / lente", "Seuil ±2", "Seuil ±3"]))
    vals = np.array(list(grid.values()))
    plateau = float(vals.max() - vals.min())
    L.append(f"\nÉcart max-min : {f(plateau, 'x')}. Garder (Sharpe) : {f(stats(portfolio({k: res[k] for k in panel_keys}, 'bh'))['sharpe'], 'x')}. "
             "Un résultat fiable est un plateau (valeurs voisines proches), pas un pic isolé.\n")

    # 6) levier
    lk = [k for k in panel_keys + perso_keys if CATS[k] != "pari"]
    lk = list(dict.fromkeys(lk))
    lv = {k: res[k] for k in lk}
    pb, ps, pl = (portfolio(lv, "bh"), portfolio(lv, "strat"), portfolio(lv, "lev"))
    L.append("## 6. Levier x2 (hors paris) : seulement quand le bot dit « levier possible »\n")
    L.append("Simulation d'un produit x2 à remise à zéro quotidienne (dérive de volatilité incluse), "
             f"financement + frais {FIN_LEV * 100:.1f} %/an, frais de changement d'exposition.\n")
    rows = []
    for lab, ser in [("Garder", pb), ("Règle sans levier", ps), ("Règle avec levier x2 conditionnel", pl)]:
        s_ = stats(ser)
        rows.append([lab, f(s_["cagr"]), f(s_["sharpe"], "x"), f(s_["maxdd"]), f(s_["calmar"], "x")])
    L.append(table(rows, ["", "Rend. annuel", "Sharpe", "Pire baisse", "Calmar"]))
    yr = pd.DataFrame({"Garder": (1 + pb.dropna()).groupby(pb.dropna().index.year).prod() - 1,
                       "Règle": (1 + ps.dropna()).groupby(ps.dropna().index.year).prod() - 1,
                       "Règle+levier": (1 + pl.dropna()).groupby(pl.dropna().index.year).prod() - 1})
    L.append("\nRendement par année :\n")
    L.append(table([[y] + [f(x) for x in r] for y, r in yr.iterrows()], ["Année", "Garder", "Règle", "Règle + levier"]))
    sl_, sp_, sb_ = stats(pl), stats(ps), stats(pb)
    lev_time = float(np.nanmean([(res[k]["expo"] > 1.5).sum() / max(res[k]["expo"].notna().sum(), 1) for k in lk]))
    inv_time = float(np.nanmean([(res[k]["expo"] > 0.5).sum() / max(res[k]["expo"].notna().sum(), 1) for k in lk]))
    L.append(f"\nPart du temps où le levier est actif : {f(lev_time)} (sur {f(inv_time)} du temps investi). "
             "Si ce chiffre est très faible, le levier conditionnel change peu de choses.\n")

    # 7) stabilité
    L.append("## 7. Stabilité dans le temps (Sharpe règle vs garder, panier large)\n")
    pbp, psp = portfolio({k: res[k] for k in panel_keys}, "bh"), portfolio({k: res[k] for k in panel_keys}, "strat")
    h2 = psp.dropna().index[len(psp.dropna()) // 2]
    L.append(table([
        [f"avant {h2.year}", f(stats(psp.loc[:h2])["sharpe"], "x"), f(stats(pbp.loc[:h2])["sharpe"], "x")],
        [f"depuis {h2.year}", f(stats(psp.loc[h2:])["sharpe"], "x"), f(stats(pbp.loc[h2:])["sharpe"], "x")]],
        ["Période", "Règle", "Garder"]))

    # 8) verdict automatique
    ss, sb = verdict["Panier large"]
    risk_ok = ss["sharpe"] > sb["sharpe"] and abs(ss["maxdd"]) < abs(sb["maxdd"])
    hasard_ok = p < 0.10
    lev_ok = (sl_["sharpe"] >= sp_["sharpe"]) and abs(sl_["maxdd"]) <= abs(sb_["maxdd"])
    plateau_ok = plateau < 0.25 and float(vals.min()) > sb["sharpe"]
    L.append("\n## 8. Verdict automatique\n")
    chk = [("La règle améliore le rapport rendement/risque ET réduit la pire baisse (panier large)", risk_ok),
           ("Elle fait mieux que le hasard (p < 10 %)", hasard_ok),
           ("La confiance est calibrée (ACHETER > GARDER > VENDRE, corrélation > 0)", calib_ok),
           ("Résultat stable aux paramètres (plateau, toujours au-dessus de « garder »)", plateau_ok),
           ("Le levier conditionnel n'aggrave ni le Sharpe ni la pire baisse par rapport à garder", lev_ok)]
    for txt, ok in chk:
        L.append(f"- {'✅' if ok else '❌'} {txt}")
    core = risk_ok and hasard_ok and calib_ok
    L.append("\n**Conclusion :** " + (
        "les avis ACHETER/VENDRE tiennent la route sur ces données ; garde-les, avec la confiance affichée."
        if core else
        "les avis ne sont pas démontrés. Présente-les comme une simple lecture de tendance (pas comme une prévision) "
        "et ne te fie pas au pourcentage de confiance.") +
        (" Le levier est " + ("défendable, dans les limites indiquées." if lev_ok else "à ne PAS utiliser sur la base de ces règles.")))
    L.append("\n*Limites : les actualités et alertes ne se backtestent pas ; le panier est composé de valeurs actuelles "
             "(léger biais de survie) ; les ETF PEA ont un historique court ; le passé ne garantit rien. Pas un conseil en investissement.*")
    report = "\n".join(L)
    open("report.md", "w").write(report)
    charts(pb, ps, pl, cal, portfolio({k: res[k] for k in panel_keys}, "bh"), psp)
    print(report)
    if "--send" in args:
        send(report, "\n".join(f"{'✅' if ok else '❌'} {t}" for t, ok in chk), core)


def charts(pb, ps, pl, cal, pbp, psp):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
    for ser, lab in [(pbp, "Garder"), (psp, "Règle du bot")]:
        ax[0].plot((1 + ser.dropna()).cumprod(), label=lab)
    ax[0].set_yscale("log"); ax[0].set_title("Panier large : 1 € investi"); ax[0].legend()
    g = cal[cal.v == 1].groupby("bucket", observed=True).fwd.mean() * 100
    ax[1].bar(g.index.astype(str), g.values); ax[1].axhline(cal.fwd.mean() * 100, color="k", ls="--", lw=1)
    ax[1].set_title("ACHETER : rend. moyen à 20 séances par tranche de confiance (%)")
    fig.tight_layout(); fig.savefig("report.png", dpi=120)


def send(report, resume, core):
    import requests
    tok, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not (tok and chat):
        return
    txt = "📊 Backtest terminé\n\n" + resume + "\n\nConclusion : " + (
        "avis démontrés sur ces données." if core else "avis NON démontrés : lecture de tendance seulement.")
    requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": txt}, timeout=20)
    for fn in ("report.md", "report.png"):
        if os.path.exists(fn):
            requests.post(f"https://api.telegram.org/bot{tok}/sendDocument", data={"chat_id": chat},
                          files={"document": open(fn, "rb")}, timeout=60)


# ------------------------------------------------------------ vérification de fidélité au bot
def selfcheck(is_demo):
    """Compare, à 40 dates tirées au hasard, la version vectorisée à bot.avis() (verdict ET confiance)."""
    os.environ.setdefault("TG_TOKEN", "x"); os.environ.setdefault("TG_CHAT", "0")
    import bot
    bot.earnings_in_days = lambda t: None
    bot.state["positions"] = {}
    bad = tot = 0
    for t, cat in [("A", "etf"), ("B", "grande"), ("C", "pari")] if is_demo else PERSO[:4]:
        df = demo(t) if is_demo else load(t)
        if df is None:
            continue
        sig = signals(df, cat)
        w = {"ticker": t, "nom": t, "cat": cat}
        for i in RNG.integers(260, len(df) - 1, 40):
            a = bot.avis(w, df.iloc[: i + 1])
            tot += 1
            mine = {1: "ACHETER", 0: "GARDER", -1: "VENDRE"}[int(sig.v.iloc[i])]
            if a["verdict"] != mine or a["conf"] != int(sig.conf.iloc[i]) or a["lev_ok"] != bool(sig.lev.iloc[i]):
                bad += 1
                print("ÉCART", t, df.index[i].date(), a["verdict"], a["conf"], a["lev_ok"], "|", mine, sig.conf.iloc[i], sig.lev.iloc[i])
    print(f"selfcheck : {tot - bad}/{tot} identiques")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
