"""Tendances de fond (2-3 mois et plus) : état des tendances, changements, stop suiveur des positions « tendance »
et backtest « garder ou suivre la tendance ».

Principe honnête : suivre la tendance sert surtout à LIMITER LES GROSSES CHUTES. Sur 10 ans de marché globalement
haussier, simplement garder gagne souvent davantage. Le mode `backtest` chiffre ce compromis par catégorie
(rendement par an, baisse maximale, temps investi), frais et écarts inclus : c'est à toi de juger s'il vaut le coup.

Trois règles testées (fixées à l'avance, pas ajustées sur les données) :
  sma200      acheter quand la clôture repasse au-dessus de sa moyenne 200 j, vendre quand elle passe 3 % en dessous
  cross       détenir tant que la moyenne 50 j est au-dessus de la moyenne 200 j
  chandelier  acheter sur un plus haut de clôture sur 100 j (au-dessus de la moyenne 200 j), vendre si la clôture passe
              sous (plus haut depuis l'achat − k × ATR) : c'est le stop suiveur des positions « tendance »
Signal à la clôture, exécution à l'ouverture suivante.
"""
import numpy as np
import pandas as pd

from . import setups as S
from .fmt import b, esc, pct
from .indicators import compute

K_ATR = {"etf": 3.0, "grande": 3.5, "pari": 4.5}     # distance du stop suiveur, en ATR (14 j)
EXIT_BAND = 0.03                                      # règle sma200 : sortie 3 % sous la moyenne (limite les allers-retours)
BREAKOUT_DAYS = 100
LABEL = {"up": ("🟢", "haussière"), "pullback": ("🟡", "repli dans la tendance"),
         "down": ("🔴", "baissière"), "young": ("⚪", "trop récente pour juger")}

RULES = {
    "sma200": {"nom": "Au-dessus de la moyenne 200 j",
               "regle": "acheter quand la clôture repasse au-dessus de sa moyenne 200 j, vendre quand elle passe 3 % en dessous"},
    "cross": {"nom": "Moyennes 50 / 200 j",
              "regle": "détenir tant que la moyenne 50 j est au-dessus de la moyenne 200 j"},
    "chandelier": {"nom": "Cassure + stop suiveur",
                   "regle": "acheter sur un plus haut de clôture sur 100 j au-dessus de la moyenne 200 j, "
                            "vendre si la clôture passe sous (plus haut − k × ATR)"},
}
CAT_LABEL = {"etf": "ETF / or", "grande": "grandes valeurs", "pari": "paris", "all": "toutes catégories"}


# ------------------------------------------------------------------ état de la tendance
def states(ind):
    """État à chaque clôture : up / pullback / down / young (moyenne 200 j pas encore calculable)."""
    c, s50, s200 = ind.Close.astype(float), ind.sma50, ind.sma200
    out = pd.Series("pullback", index=ind.index, dtype=object)
    out[(c > s200) & (c > s50) & (s50 > s200)] = "up"
    out[c < s200] = "down"
    out[s200.isna() | s50.isna()] = "young"
    return out


def confirmed(st):
    """État confirmé par 2 clôtures de suite (évite de réagir à un seul jour de bruit)."""
    v = st.values
    for i in range(len(v) - 1, 0, -1):
        if v[i] == v[i - 1]:
            return v[i]
    return v[-1]


def assess(ind):
    """Résumé de la tendance de fond d'une valeur à la dernière clôture."""
    st = states(ind)
    c = ind.Close.astype(float)
    n = len(c)
    return {
        "state": confirmed(st), "raw": st.iloc[-1], "bars": n,
        "ret1": float(c.iloc[-1] / c.iloc[-2] - 1) if n > 1 else 0.0,
        "ret63": float(c.iloc[-1] / c.iloc[-64] - 1) if n > 63 else None,
        "dd": float(c.iloc[-1] / c.tail(252).max() - 1),
        "since_start": float(c.iloc[-1] / c.iloc[0] - 1),
    }


def _p0(x):
    """Pourcentage entier, sans « −0 % » ni « +0 % » parasites."""
    return "0 %" if abs(x) < 0.005 else pct(x, 0)


def line(w, a, jour="1 j"):
    ico, lab = LABEL[a["state"]]
    parts = [f"{ico} {esc(w['nom'])} · {lab}"]
    if a["state"] == "young":
        parts.append(f"{a['bars']} séances d'historique")
    parts.append(f"{jour} {pct(a['ret1'])}")
    if a["ret63"] is not None:
        parts.append(f"3 mois {_p0(a['ret63'])}")
    elif a["state"] == "young":
        parts.append(f"depuis le début {_p0(a['since_start'])}")
    parts.append("au plus haut" if a["dd"] > -0.005 else f"{pct(a['dd'], 0)} du plus haut")
    return " · ".join(parts)


def section(snap, entries, titre="📈 Tendance de fond (2-3 mois et plus)", jour="1 j", today=None):
    """Une ligne par valeur : tendance, variation du jour, 3 mois, distance au plus haut. Filtre de risque, pas une prévision.
    Si `today` est donné, une valeur sans séance ce jour-là (férié) est étiquetée « séance préc. »."""
    lines = [b(titre) + "\n<i>filtre de risque, pas une prévision</i>"]
    for w in entries:
        s = snap.get(w["ticker"])
        if s is None:
            lines.append(f"⚪ {esc(w['nom'])} · pas de données récentes")
            continue
        j = jour if (today is None or s["ind"].index[-1].date() == today) else "séance préc."
        lines.append(line(w, assess(s["ind"]), j))
    return "\n".join(lines)


def detect_change(state, ticker, a, today):
    """(ancien, nouveau) si l'état confirmé a changé depuis la dernière observation, sinon None. La 1re observation est silencieuse."""
    store = state.setdefault("trend", {})
    prev, new = store.get(ticker), a["state"]
    if prev is None:
        store[ticker] = {"state": new, "since": str(today)}
        return None
    if prev["state"] == new:
        return None
    store[ticker] = {"state": new, "since": str(today)}
    return prev["state"], new


def change_text(w, old, new):
    """(niveau, texte) : niveau 'alert' pour un retournement baissier, 'info' sinon."""
    nom = b(w["nom"])
    if old == "young":                                         # ex. SpaceX : 200 séances atteintes, la moyenne 200 j existe enfin
        ico, lab = LABEL[new]
        return "info", f"⚪ {nom} a maintenant assez d'historique (200 séances) pour juger sa tendance de fond : {ico} {lab}."
    if new == "down":
        return "alert", (f"📉 {nom} passe sous sa moyenne 200 j (confirmé par 2 clôtures) : la tendance de fond se retourne "
                         f"à la baisse. Si tu le détiens pour la tendance, c'est le signal à regarder.")
    if new == "up":
        return "info", f"📈 {nom} repasse en tendance haussière (au-dessus de ses moyennes 50 et 200 j)."
    if new == "pullback" and old == "up":
        return "info", f"🟡 {nom} repasse sous sa moyenne 50 j : simple repli dans la tendance haussière pour l'instant, à surveiller."
    if new == "pullback":
        return "info", f"🟡 {nom} repasse au-dessus de sa moyenne 200 j sans tendance nette : amélioration à confirmer."
    return "info", f"{nom} : la tendance de fond change ({LABEL[old][1]} → {LABEL[new][1]})."


# ------------------------------------------------------------------ positions « tendance » : stop suiveur
def k_for(cfg, cat):
    return ((cfg or {}).get("tendance") or {}).get("k_atr", {}).get(cat, K_ATR[cat])


def initial_stop(entry, atr, k):
    """Stop de départ : prix d'entrée − k × ATR (3 % du prix si l'ATR est inconnu)."""
    if atr is None or not np.isfinite(atr) or atr <= 0:
        atr = entry * 0.03
    return entry - k * atr


def update_trailing(pos, ind):
    """Stop « chandelier » : (plus haut de clôture depuis l'entrée) − k × ATR. Ne descend jamais.
    Retourne True si le stop a monté."""
    since = ind.Close[ind.index >= pd.Timestamp(pos["date"])]
    hc = max(pos.get("peak", pos["entry"]), float(since.max()) if len(since) else pos["entry"])
    pos["peak"] = hc
    atr = float(ind.atr.iloc[-1])
    if not np.isfinite(atr):
        return False
    cand = hc - pos["k"] * atr
    if cand > pos["stop"]:
        pos["stop"] = cand
        return True
    return False


# ------------------------------------------------------------------ backtest
def _seg(curve, close, idx, i0):
    years = (idx[-1] - idx[i0]).days / 365.25
    if years < 1 or curve[i0] <= 0 or close[i0] <= 0:
        return None
    seg, bh = curve[i0:] / curve[i0], close[i0:] / close[i0]
    return {"cagr": float(seg[-1] ** (1 / years) - 1), "bh_cagr": float(bh[-1] ** (1 / years) - 1),
            "dd": float((seg / np.maximum.accumulate(seg) - 1).min()),
            "bh_dd": float((bh / np.maximum.accumulate(bh) - 1).min()), "years": years}


def simulate(ind, rule, cat):
    """Simule la règle sur tout l'historique (un seul titre, 100 % investi quand « long »). Frais et écarts inclus."""
    O, C = ind.Open.values.astype(float), ind.Close.values.astype(float)
    s50, s200 = ind.sma50.values, ind.sma200.values
    atr = ind.atr.values
    hi = ind.Close.rolling(BREAKOUT_DAYS).max().values
    n = len(C)
    valid = np.isfinite(s200) & np.isfinite(atr) & np.isfinite(hi)
    first = int(np.argmax(valid)) if valid.any() else None
    if first is None or n - first < 260:
        return None
    side = S.cost_rt(cat) / 2
    k = K_ATR[cat]
    eq, curve = 1.0, np.ones(n)
    held = pend_in = pend_out = False
    trades = days_in = 0
    hc = stop = 0.0
    for i in range(first + 1, n):
        if pend_out:                                               # sortie à l'ouverture
            eq *= (O[i] / C[i - 1]) * (1 - side)
            held = pend_out = False
            trades += 1
        elif pend_in:                                              # entrée à l'ouverture
            pend_in, held = False, True
            eq *= (1 - side) * (C[i] / O[i])
            hc, stop = C[i], C[i] - k * atr[i]
            days_in += 1
        elif held:
            eq *= C[i] / C[i - 1]
            days_in += 1
            hc = max(hc, C[i])
            stop = max(stop, hc - k * atr[i])
        curve[i] = eq
        if not valid[i]:
            continue
        if held:                                                   # décision à la clôture de i
            if rule == "sma200":
                pend_out = C[i] < s200[i] * (1 - EXIT_BAND)
            elif rule == "cross":
                pend_out = s50[i] < s200[i]
            else:
                pend_out = C[i] < stop
        elif not pend_in:
            if rule == "sma200":
                pend_in = C[i] > s200[i]
            elif rule == "cross":
                pend_in = s50[i] > s200[i]
            else:
                pend_in = (C[i] >= hi[i]) and (C[i] > s200[i])
    if held:
        trades += 1                                                # position encore ouverte : comptée
    idx = ind.index
    full = _seg(curve, C, idx, first)
    if full is None:
        return None
    rec_i = int(np.searchsorted(idx, idx[-1] - pd.DateOffset(years=3)))
    recent = _seg(curve, C, idx, max(rec_i, first)) if idx[-1] - idx[first] > pd.Timedelta(days=3 * 365 + 200) else None
    return {**full, "invested": days_in / max(1, n - first - 1), "trades_per_year": trades / full["years"], "recent": recent}


def _med(xs):
    return float(np.median(xs)) if len(xs) else None


def aggregate(results):
    """Médianes sur les valeurs d'une catégorie (une valeur = un résultat)."""
    if not results:
        return None
    out = {"n": len(results)}
    for k in ("cagr", "bh_cagr", "dd", "bh_dd", "invested", "trades_per_year"):
        out[k] = _med([r[k] for r in results])
    out["share_less_dd"] = float(np.mean([abs(r["dd"]) < abs(r["bh_dd"]) for r in results]))
    out["share_beats"] = float(np.mean([r["cagr"] > r["bh_cagr"] for r in results]))
    rec = [r["recent"] for r in results if r["recent"]]
    out["recent"] = ({"n": len(rec), **{k: _med([r[k] for r in rec]) for k in ("cagr", "bh_cagr", "dd", "bh_dd")}}
                     if rec else None)
    out["verdict"] = verdict(out)
    return out


def verdict(a):
    """('ok'|'mitige'|'non', texte) : la règle limite-t-elle les chutes, et à quel prix ?"""
    if not a["bh_dd"]:
        return "non", "indéterminé"
    gain = 1 - abs(a["dd"]) / abs(a["bh_dd"])
    keep = a["cagr"] / a["bh_cagr"] if a["bh_cagr"] and a["bh_cagr"] > 0 else None
    if gain >= 0.25 and (keep is None or keep >= 0.6):
        return "ok", "limite les chutes sans trop coûter"
    if gain >= 0.25:
        return "mitige", "limite les chutes mais rapporte nettement moins que garder"
    return "non", "ne protège pas assez des chutes"


def run_backtest(cfg, now=None, period="10y"):
    import datetime as dt
    from . import data
    now = now or dt.datetime.now(dt.timezone.utc)
    entries = {}
    for w in list(cfg["watchlist"]) + list(cfg.get("univers", [])) + list(cfg.get("calibration", [])):
        entries.setdefault(w["ticker"], w["cat"])
    raw = data.daily(list(entries), period=period)
    pool = {r: {c: [] for c in ("etf", "grande", "pari", "all")} for r in RULES}
    per_ticker, used = {}, []
    for t, cat in entries.items():
        df = raw.get(t)
        if df is None or len(df) < 500:
            continue
        ind = compute(data.completed(df, t, now))
        used.append(t)
        for rule in RULES:
            r = simulate(ind, rule, cat)
            if r is None:
                continue
            pool[rule][cat].append(r)
            pool[rule]["all"].append(r)
            per_ticker.setdefault(t, {})[rule] = {k: round(r[k], 4) for k in ("cagr", "bh_cagr", "dd", "bh_dd", "invested")}
    rules = {}
    for rule, d in pool.items():
        rules[rule] = {c: a for c, lst in d.items() if (a := aggregate(lst))}
    return {"generated": str(now.date()), "period": period, "n_tickers": len(used), "rules": rules, "tickers": per_ticker}


def report_md(st):
    out = [f"# Backtest tendance — {st['generated']}",
           f"{st['n_tickers']} valeurs, {st['period']} d'historique, signal à la clôture, exécution à l'ouverture suivante, "
           "frais et écarts inclus. Chaque ligne = médiane des valeurs de la catégorie, comparée à « garder » (acheter et ne rien faire).", "",
           "**Limites** : valeurs choisies avec le recul (biais de survie : garder est flatté) ; période surtout haussière ; "
           "les résultats passés ne garantissent rien ; la fiscalité n'est pas modélisée.", ""]
    for rule, d in st["rules"].items():
        out += [f"## {RULES[rule]['nom']}", f"_{RULES[rule]['regle']}_", "",
                "| Catégorie | Valeurs | Rend./an règle | Rend./an garder | Baisse max règle | Baisse max garder | Investi | Trades/an | "
                "Moins de baisse (part des valeurs) | 3 dernières années (règle vs garder, baisse max) | Verdict |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for c in ("etf", "grande", "pari", "all"):
            a = d.get(c)
            if not a:
                continue
            rec = a["recent"]
            rec_s = (f"{pct(rec['cagr'], 0)} vs {pct(rec['bh_cagr'], 0)}, {pct(rec['dd'], 0)} vs {pct(rec['bh_dd'], 0)}"
                     if rec else "n/a")
            out.append(f"| {CAT_LABEL[c]} | {a['n']} | {pct(a['cagr'], 1)} | {pct(a['bh_cagr'], 1)} | {pct(a['dd'], 0)} | "
                       f"{pct(a['bh_dd'], 0)} | {a['invested'] * 100:.0f} % | {a['trades_per_year']:.1f} | "
                       f"{a['share_less_dd'] * 100:.0f} % | {rec_s} | {a['verdict'][1]} |")
        out.append("")
    return "\n".join(out)


def telegram_summary(st):
    ico = {"ok": "✅", "mitige": "🟡", "non": "❌"}
    lines = [f"📈 <b>Backtest tendance</b> · {st['n_tickers']} valeurs, {st['period']}, frais inclus", "",
             "<i>Règle de tendance vs « garder » (médiane par catégorie)</i>"]
    for rule, d in st["rules"].items():
        lines.append("")
        lines.append(b(RULES[rule]["nom"]))
        for c in ("etf", "grande", "pari"):
            a = d.get(c)
            if not a:
                continue
            lines.append(f"{ico[a['verdict'][0]]} {CAT_LABEL[c]} : {pct(a['cagr'], 0)}/an contre {pct(a['bh_cagr'], 0)} en gardant · "
                         f"baisse max {pct(a['dd'], 0)} contre {pct(a['bh_dd'], 0)} · investi {a['invested'] * 100:.0f} % du temps")
    lines += ["", "Lecture : ✅ limite les chutes sans trop coûter · 🟡 limite les chutes mais rapporte moins · ❌ ne protège pas assez.",
              "En marché haussier, garder gagne souvent plus : la tendance sert surtout à éviter les grosses chutes. "
              "Valeurs choisies avec le recul (biais de survie)."]
    return "\n".join(lines)
