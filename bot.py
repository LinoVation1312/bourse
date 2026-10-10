"""Assistant de trading par notifications Telegram (aucun ordre n'est passé automatiquement).

Lancé par GitHub Actions 2 fois par jour (matin / soir) ou à la main.
Commandes Telegram (traitées à chaque exécution) :
  /achat TICKER PRIX_SOUS_JACENT LEVIER MISE_EUR   ex: /achat NVDA 134.5 2 600
  /vente TICKER PRIX_SOUS_JACENT [PNL_REEL_EUR]    ex: /vente NVDA 150
  /statut   /bilan   /aide
"""
import os
import json
import datetime as dt

import requests
import pandas as pd
import yfinance as yf

from core import load, add_indicators, tech_buy, stop_mult

TOKEN = os.environ["TG_TOKEN"]
CHAT = str(os.environ["TG_CHAT"])
FH = os.environ.get("FINNHUB_KEY")
EVENT = os.environ.get("GITHUB_EVENT_NAME", "manual")
MANUAL = EVENT in ("workflow_dispatch", "manual")
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()
MORNING = NOW.hour < 12 and not MANUAL

cfg = json.load(open("config.json"))
state = json.load(open("state.json"))
for k, v in {"offset": 0, "positions": {}, "history": [], "realized": 0.0,
             "peak_equity": None, "pause_until": None, "alerts": {}}.items():
    state.setdefault(k, v)
WL = {w["ticker"]: w for w in cfg["watchlist"]}
errors = []


# ---------------------------------------------------------------- Telegram
def notify(text):
    """Envoie par morceaux < 3800 caractères, coupés aux sauts de paragraphe."""
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > 3800:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    chunks.append(cur)
    for c in chunks:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT, "text": c[:4000]}, timeout=20)


# ---------------------------------------------------------------- Argent
def equity():
    return cfg["capital_initial"] + state["realized"]


def invested():
    return sum(p["mise"] for p in state["positions"].values())


def cash():
    return equity() - invested()


def reserve():
    return equity() * cfg["cash_reserve_pct"]


def is_eur(t):
    return WL.get(t, {}).get("devise", "EUR" if "." in t else "USD") == "EUR"


def frais_ar(mise, t):
    """Frais estimés d'un aller-retour : courtage + spread (+ change si devise étrangère)."""
    change = 0 if is_eur(t) else 2 * cfg["change_pct"]
    return 2 * cfg["frais_ordre"] + mise * (2 * cfg["spread_pct"] + change)


def veille_flag(t):
    """Drapeau posé par la veille (événement critique, suspension, données douteuses) sur les 2 derniers jours."""
    try:
        f = json.load(open("veille_state.json")).get("flags", {}).get(t)
        if f and dt.date.fromisoformat(f["date"]) >= TODAY - dt.timedelta(days=2):
            return f
    except Exception:
        pass
    return None


def paused():
    p = state["pause_until"]
    return bool(p) and TODAY <= dt.date.fromisoformat(p)


# ---------------------------------------------------------------- Données
def prep(t, period="2y"):
    """Indicateurs sur barres journalières TERMINÉES uniquement."""
    df = load(t, period)
    if df.index[-1].date() == TODAY and NOW.hour < 21:
        df = df.iloc[:-1]
    return add_indicators(df)


_news_cache = {}
NEG_WORDS = ["offering", "dilution", "downgrade", "probe", "lawsuit", "short report",
             "export ban", "investigation", "sec charges", "recall", "fraud"]


def bad_news(t):
    """Garde-fou grossier par mots-clés (Finnhub, actions US uniquement). Ne déclenche jamais un achat."""
    if not FH or "." in t:
        return []
    if t in _news_cache:
        return _news_cache[t]
    try:
        d1 = TODAY
        d0 = d1 - dt.timedelta(days=3)
        r = requests.get("https://finnhub.io/api/v1/company-news",
                         params={"symbol": t, "from": d0, "to": d1, "token": FH}, timeout=20).json()
        hits = [x["headline"] for x in r[:20] if any(w in x["headline"].lower() for w in NEG_WORDS)]
    except Exception:
        hits = []
    _news_cache[t] = hits
    return hits


def earnings_in_days(t):
    """Jours avant les prochains résultats, ou None si inconnu (à vérifier à la main)."""
    try:
        cal = yf.Ticker(t).calendar
        d = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if not d:
            return None
        d0 = d[0] if isinstance(d, (list, tuple)) else d
        days = (pd.Timestamp(d0).date() - TODAY).days
        return days if days >= 0 else None
    except Exception:
        return None


def conviction(t, df, bench):
    """Score 0-100 : tendance (35) + force relative (25) + croissance (25) + volatilité (15)."""
    l = df.iloc[-1]
    s = 0
    if l.Close > l.ema20 > l.ema50:
        s += 25
    elif l.Close > l.ema50:
        s += 10
    pente = df.ema50.iloc[-1] / df.ema50.iloc[-21] - 1
    s += 10 if pente > 0.03 else 5 if pente > 0 else 0
    if l.ret63 > bench["ret63"]:
        s += 12
    if l.ret126 > bench["ret126"]:
        s += 13
    try:  # données fondamentales parfois absentes : pas de points, jamais inventés
        info = yf.Ticker(t).info
        g, m = info.get("revenueGrowth"), info.get("profitMargins")
        if g is not None:
            s += 15 if g > 0.25 else 10 if g > 0.10 else 5 if g > 0 else 0
        if m is not None:
            s += 10 if m > 0.15 else 5 if m > 0 else 0
    except Exception:
        pass
    atr_pct = l.atr / l.Close
    s += 15 if atr_pct < 0.03 else 8 if atr_pct < 0.05 else 0
    return s


# ---------------------------------------------------------------- Commandes
def read_commands():
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                         params={"offset": state["offset"] + 1}, timeout=20).json()
    except Exception as e:
        errors.append(f"Telegram illisible : {e}")
        return
    for u in r.get("result", []):
        state["offset"] = u["update_id"]
        msg = u.get("message", {})
        if str(msg.get("chat", {}).get("id")) != CHAT:
            continue  # on ignore tout autre expéditeur
        p = msg.get("text", "").split()
        if not p:
            continue
        cmd = p[0].split("@")[0].lower()
        try:
            if cmd == "/achat":
                cmd_achat(p)
            elif cmd == "/vente":
                cmd_vente(p)
            elif cmd == "/statut":
                notify(summary())
            elif cmd == "/journal":
                notify(journal_veille())
            elif cmd == "/bilan":
                notify(bilan())
            else:
                notify("Commandes :\n/achat TICKER PRIX LEVIER MISE_EUR\n/vente TICKER PRIX [PNL_EUR]\n/statut\n/bilan\n/journal")
        except Exception as e:
            notify(f"⚠️ Commande non comprise ({e}). Exemple : /achat NVDA 134.5 2 600")


def cmd_achat(p):
    t, px, lev, mise = p[1].upper(), float(p[2]), float(p[3]), float(p[4])
    stop_init = None
    try:
        l = prep(t).iloc[-1]
        stop_init = float(px - stop_mult(lev) * l.atr)
    except Exception:
        pass
    state["positions"][t] = {"entry": px, "lev": lev, "mise": mise, "date": str(TODAY),
                             "peak": px, "stop": stop_init}
    s = f"{stop_init:.2f}" if stop_init else "non calculé"
    notify(f"📝 Position enregistrée : {t} @ {px} (x{lev}, {mise:.0f} €)\n"
           f"Stop initial sous-jacent : {s}. Pose-le dans l'app si le produit le permet.\n"
           f"Cash restant : {cash():.0f} €")


def cmd_vente(p):
    t, px = p[1].upper(), float(p[2])
    pos = state["positions"].pop(t, None)
    if not pos:
        notify(f"Aucune position {t} enregistrée.")
        return
    frais = frais_ar(pos["mise"], t)
    pnl = float(p[3]) if len(p) > 3 else pos["mise"] * pos["lev"] * (px / pos["entry"] - 1) - frais
    state["realized"] += pnl
    state["history"].append({"t": t, "lev": pos["lev"], "mise": pos["mise"], "entry": pos["entry"],
                             "exit": px, "pnl": round(pnl, 2), "frais": round(frais, 2),
                             "open": pos["date"], "close": str(TODAY)})
    eq = equity()
    state["peak_equity"] = max(state["peak_equity"] or cfg["capital_initial"], eq)
    txt = f"📝 {t} clôturée : {pnl:+.1f} € net (frais estimés {frais:.1f} €)\nPoche : {eq:.0f} €"
    if eq <= state["peak_equity"] * (1 - cfg["coupe_circuit_pct"]):
        until = TODAY + dt.timedelta(days=cfg["pause_jours"])
        state["pause_until"] = str(until)
        txt += (f"\n⛔ COUPE-CIRCUIT : la poche a perdu {cfg['coupe_circuit_pct']:.0%} depuis son plus haut. "
                f"Plus de signaux d'achat jusqu'au {until}. Relis ton journal avant de reprendre.")
    notify(txt)


def journal_veille():
    try:
        j = json.load(open("veille_state.json")).get("journal", [])
    except Exception:
        j = []
    j = [x for x in j if x.get("kind") != "news-info"][-10:]
    if not j:
        return "Journal de veille vide."
    lines = []
    for x in j:
        suivi = f" → {x['ret_suivi']:+.1%} depuis" if x.get("ret_suivi") is not None else ""
        lines.append(f"P{x['prio']} {x['d']} {x['t']} [{x['kind']}] {x['txt'][:70]}{suivi}")
    return "📓 10 dernières alertes de veille :\n" + "\n".join(lines)


def bilan():
    h = state["history"]
    if not h:
        return "Aucun trade clôturé pour l'instant."
    wins = [x for x in h if x["pnl"] > 0]
    tot = sum(x["pnl"] for x in h)
    fr = sum(x["frais"] for x in h)
    return (f"📒 Bilan : {len(h)} trades, {len(wins) / len(h):.0%} gagnants\n"
            f"Net total : {tot:+.1f} € | moyenne {tot / len(h):+.1f} €/trade\n"
            f"Frais payés (estimés) : {fr:.1f} €\nPoche : {equity():.0f} €")


# ---------------------------------------------------------------- Signaux
def sell_signal(t, pos):
    df = prep(t)
    l = df.iloc[-1]
    bar = str(df.index[-1].date())
    since = df[df.index >= pd.Timestamp(pos["date"])]
    pos["peak"] = float(max(pos["peak"], since.Close.max() if len(since) else pos["peak"]))
    trail = pos["peak"] - stop_mult(pos["lev"]) * l.atr
    old_stop = pos.get("stop") or 0
    pos["stop"] = float(max(old_stop, trail))          # le stop ne descend jamais
    why = []
    if l.Close < pos["stop"]:
        why.append(f"stop suiveur touché ({pos['stop']:.2f})")
    if l.Close < l.ema50:
        why.append("cassure de l'EMA50")
    elif l.Close < l.ema20 and l.rsi < 45:
        why.append("cassure de l'EMA20 avec RSI faible")
    age = (TODAY - dt.date.fromisoformat(pos["date"])).days
    limite = cfg["jours_max_levier"] if pos["lev"] > 1 else cfg["jours_max_1x"]
    if age > limite:
        why.append(f"durée max dépassée ({age} j) : le levier s'use avec le temps")
    nb = bad_news(t)
    if nb:
        why.append(f"news négative : {nb[0][:90]}")
    e = earnings_in_days(t)
    if e is not None and e <= 2:
        why.append(f"résultats dans {e} j (risque de gap)")
    if not why:
        return None, bar
    perf = max(-1.0, (l.Close / pos["entry"] - 1) * pos["lev"])
    return (f"🔴 SIGNAL VENTE {t} @ {l.Close:.2f} (≈ {perf:+.0%} sur le produit)\n- "
            + "\n- ".join(why) + f"\nUne fois vendu : /vente {t} {l.Close:.2f}"), bar


def scan_buys(bench):
    cands = []
    for item in cfg["watchlist"]:
        t = item["ticker"]
        if t in state["positions"]:
            continue
        try:
            df = prep(t)
        except Exception as e:
            errors.append(f"{t} : {e}")
            continue
        l = df.iloc[-1]
        if not tech_buy(l, bench["ret63"], item.get("atr_pct_max", cfg["atr_pct_max"])):
            continue
        nb, e = bad_news(t), earnings_in_days(t)
        if nb:
            cands.append((-1, t, item, df, f"⚪ {t} : signal technique BLOQUÉ (news : {nb[0][:80]})"))
            continue
        if e is not None and e <= 5:
            cands.append((-1, t, item, df, f"⚪ {t} : signal technique BLOQUÉ (résultats dans {e} j)"))
            continue
        fl = veille_flag(t)
        if fl and (fl["level"] == "critical" or (fl["level"] == "data" and fl["date"] == str(TODAY))):
            cands.append((-1, t, item, df, f"⚪ {t} : signal technique BLOQUÉ par la veille ({fl['why']})"))
            continue
        score = conviction(t, df, bench)
        if score >= cfg["score_min"]:
            cands.append((score, t, item, df, None))
    cands.sort(key=lambda c: -c[0])

    msgs = []
    slots = cfg["max_positions"] - len(state["positions"])
    dispo = cash() - reserve()
    for score, t, item, df, blocked in cands:
        bar = str(df.index[-1].date())
        key = f"{t}:buy"
        if not MANUAL and state["alerts"].get(key) == bar:
            continue
        state["alerts"][key] = bar
        if blocked:
            msgs.append(blocked)
            continue
        l = df.iloc[-1]
        lev_cap = min(cfg["max_lev"], item.get("max_lev", cfg["max_lev"]))
        lev = 2 if (score >= cfg["score_levier_2x"] and lev_cap >= 2) else 1
        m = stop_mult(lev)
        stop = float(l.Close - m * l.atr)
        stop_pct = (l.Close - stop) / l.Close
        risque = equity() * cfg["risque_par_trade"]
        mise = min(risque / (lev * stop_pct), equity() * cfg["position_max_pct"], dispo)
        head = f"{t} ({item['nom']}) score {score}/100"
        if slots <= 0:
            msgs.append(f"⚪ {head} : signal, mais {cfg['max_positions']} positions déjà ouvertes.")
            continue
        if mise < cfg["position_min_eur"]:
            msgs.append(f"⚪ {head} : signal, mais mise possible ({mise:.0f} €) trop petite pour amortir les frais.")
            continue
        frais = frais_ar(mise, t)
        fr_pct = frais / mise
        gain_vise = 2 * risque
        if fr_pct > cfg["frais_max_pct_position"] or gain_vise < cfg["ratio_gain_frais_min"] * frais:
            msgs.append(f"⚪ {head} : signal ignoré, frais trop lourds ({frais:.1f} €, {fr_pct:.1%}).")
            continue
        msgs.append(
            f"🟢 SIGNAL ACHAT {head}\n"
            f"Cours {l.Close:.2f} | levier suggéré x{lev}\n"
            f"Stop sous-jacent : {stop:.2f} (≈ -{lev * stop_pct:.0%} sur le produit)\n"
            f"Mise : {mise:.0f} € (risque ≈ {risque:.0f} €) | frais A/R ≈ {frais:.1f} € ({fr_pct:.1%})\n"
            f"Gain visé ≈ +{gain_vise:.0f} € (2x le risque)\n"
            f"Si tu achètes : /achat {t} {l.Close:.2f} {lev} {mise:.0f}"
            + (f"\n⚠️ La veille signale un événement à analyser avant d'entrer : {fl['why']}"
               if (fl := veille_flag(t)) and fl["level"] == "important" else ""))
        slots -= 1
        dispo -= mise
    return msgs


def summary():
    lines = [f"📊 Poche : {equity():.0f} € | cash {cash():.0f} € | investi {invested():.0f} € "
             f"(réserve visée {reserve():.0f} €)"]
    if paused():
        lines.append(f"⛔ Pause active jusqu'au {state['pause_until']} (coupe-circuit)")
    for t, p in state["positions"].items():
        try:
            px = float(prep(t).iloc[-1].Close)
            pu = px / p["entry"] - 1
            st = f"{p['stop']:.2f}" if p.get("stop") else "?"
            lines.append(f"• {t} x{p['lev']} : {pu:+.1%} sous-jacent ≈ {max(-1.0, pu * p['lev']):+.1%} produit | stop {st}")
        except Exception as e:
            lines.append(f"• {t} : données indisponibles ({e})")
    if not state["positions"]:
        lines.append("Aucune position ouverte.")
    return "\n".join(lines)


# ---------------------------------------------------------------- Principal
def main():
    read_commands()
    msgs = []

    for t, pos in list(state["positions"].items()):
        try:
            m, bar = sell_signal(t, pos)
            key = f"{t}:sell"
            if m and (MANUAL or state["alerts"].get(key) != bar):
                state["alerts"][key] = bar
                msgs.append(m)
        except Exception as e:
            errors.append(f"{t} : {e}")

    if paused():
        msgs.append(f"⏸ Pause jusqu'au {state['pause_until']} : pas de nouveaux signaux d'achat.")
    else:
        try:
            b = prep(cfg["benchmark"]).iloc[-1]
            bench = {"ret63": float(b.ret63), "ret126": float(b.ret126)}
            msgs += scan_buys(bench)
        except Exception as e:
            errors.append(f"benchmark {cfg['benchmark']} : {e}")

    if errors:
        msgs.append("⚠️ Données indisponibles (réessaie plus tard) :\n" + "\n".join(errors))

    out = []
    if MORNING or MANUAL:
        out.append(summary())
    out += msgs
    if MANUAL and not msgs:
        out.append("✅ Aucun signal.")
    if out:
        notify("\n\n".join(out))


if __name__ == "__main__":
    try:
        main()
    finally:
        json.dump(state, open("state.json", "w"), indent=1)
