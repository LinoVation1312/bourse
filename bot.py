"""Assistant Bourse. Aucun ordre n'est passé : il donne un avis (acheter / garder / vendre + confiance + levier), tu décides.

  python bot.py daily   -> récap du matin, à l'ouverture de la bourse de Paris (9h)
  python bot.py veille  -> toutes les 15 min en séance : t'écrit seulement si quelque chose se passe
                           (grosse variation sur la journée, mouvement brutal sur 1 h, actualité sensible)

Commandes Telegram (lues à chaque exécution) :
  /avis nvidia    : l'avis détaillé sur une valeur
  /achat nvidia   : le bot suit ta position (prix de référence = cours Yahoo du moment)
  /vente nvidia   : il arrête de la suivre
  /statut         : l'avis sur toutes tes valeurs
"""
import os
import sys
import json
import math
import hashlib
import datetime as dt
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import yfinance as yf

TOKEN = os.environ["TG_TOKEN"]
CHAT = str(os.environ["TG_CHAT"])
FH = os.environ.get("FINNHUB_KEY")
MODE = sys.argv[1] if len(sys.argv) > 1 else "daily"
MANUAL = os.environ.get("GITHUB_EVENT_NAME", "manual") in ("workflow_dispatch", "manual")
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()
PARIS = NOW.astimezone(ZoneInfo("Europe/Paris"))

cfg = json.load(open("config.json"))
state = json.load(open("state.json"))
for k, v in {"offset": 0, "positions": {}, "sent": {}, "dca_month": "", "daily_sent": "", "last_verdict": {}}.items():
    state.setdefault(k, v)
WL = cfg["watchlist"]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
ICON = {"ACHETER": "🟢", "GARDER": "⚪", "VENDRE": "🔴"}

NEG_KW = ["investigation", "probe", "lawsuit", "sues", "subpoena", "sec charges", "fraud", "indict",
          "antitrust", "bankrupt", "chapter 11", "going concern", "delist", "trading halt", "halted",
          "stock offering", "public offering", "dilut", "private placement", "downgrad", "profit warning",
          "cuts guidance", "lowers guidance", "resigns", "steps down", "cyberattack", "data breach", "recall",
          "plunge", "tumble", "short seller", "short report"]
POS_KW = ["upgrade", "raises guidance", "record revenue", "beats estimates", "buyback", "to acquire",
          "acquisition", "contract win", "fda approval", "surge", "soars"]


# ------------------------------------------------------------ outils
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
                      data={"chat_id": CHAT, "text": c[:4000], "disable_web_page_preview": "true"}, timeout=20)


def pct(x):
    d = 1 if abs(x) < 0.1 else 0
    return f"{x * 100:+.{d}f} %".replace(".", ",")


def px(x):
    return f"{x:.2f}".replace(".", ",")


def once(key, days=0):
    """Vrai si cette alerte n'a pas déjà été envoyée (depuis `days` jours)."""
    last = state["sent"].get(key)
    if last and (TODAY - dt.date.fromisoformat(last)).days <= days:
        return False
    state["sent"][key] = str(TODAY)
    return True


def prune():
    lim = TODAY - dt.timedelta(days=10)
    state["sent"] = {k: v for k, v in state["sent"].items() if dt.date.fromisoformat(v) >= lim}


def tzname(t):
    if t.endswith((".PA", ".AS", ".DE")):
        return "Europe/Paris"
    if t.endswith(".HK"):
        return "Asia/Hong_Kong"
    if t.endswith(".WA"):
        return "Europe/Warsaw"
    return "America/New_York"


def history(t):
    df = yf.download(t, period="1y", interval="1d", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    if len(df) < 60:
        raise ValueError("pas assez de données")
    return df


def finished_bars(df, t):
    """Retire la séance du jour si elle est peut-être encore en cours."""
    if df.index[-1].date() == NOW.astimezone(ZoneInfo(tzname(t))).date():
        df = df.iloc[:-1]
    return df


def resolve(q):
    q = q.lower().strip()
    for w in WL:
        names = {w["ticker"].lower(), w["ticker"].split(".")[0].lower(), *w.get("alias", [])}
        if q in names:
            return w
    return None


def earnings_in_days(t):
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


# ------------------------------------------------------------ l'avis (acheter / garder / vendre)
def rsi14(c):
    d = c.diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    u, n = float(up.iloc[-1]), float(dn.iloc[-1])
    return 100.0 if n == 0 else 100 - 100 / (1 + u / n)


def avis(w, df, last_live=None):
    """Avis à partir de la tendance (moyennes 50/200 j, élan 3 mois, surchauffe RSI).

    Le « niveau de confiance » est un indice maison (accord des signaux, moins une pénalité si la valeur
    est très volatile). Ce n'est PAS une probabilité de gain."""
    c = df.Close.astype(float)
    last = float(c.iloc[-1]) if last_live is None else last_live
    t, cat = w["ticker"], w["cat"]
    sma50 = float(c.tail(50).mean())
    sma200 = float(c.tail(200).mean()) if len(c) >= 200 else None
    mom3 = last / float(c.iloc[-64]) - 1 if len(c) >= 64 else 0.0
    rsi = rsi14(c)
    vol = float(c.pct_change().tail(60).std() * math.sqrt(252))

    score, why = 0, []
    if last > sma50:
        score += 1; why.append("au-dessus de sa moyenne 50 j")
    else:
        score -= 1; why.append("sous sa moyenne 50 j")
    if sma200 is not None:
        if last > sma200:
            score += 1; why.append("tendance longue haussière")
        else:
            score -= 1; why.append("sous sa moyenne 200 j")
    if mom3 > 0.05:
        score += 1; why.append(f"{pct(mom3)} sur 3 mois")
    elif mom3 < -0.05:
        score -= 1; why.append(f"{pct(mom3)} sur 3 mois")
    if rsi > 75:
        score -= 1; why.append("surchauffe (RSI élevé)")
    elif rsi < 25:
        score += 1; why.append("très survendue (rebond possible)")

    verdict = "ACHETER" if score >= 3 else "VENDRE" if score <= -3 else "GARDER"
    conf = 50 + 10 * abs(score) if verdict != "GARDER" else 70 - 10 * abs(score)
    conf -= 10 if vol > 0.60 else 5 if vol > 0.40 else 0

    notes = []
    e = earnings_in_days(t) if cat != "etf" else None
    if e is not None and e <= 5:
        conf -= 10
        notes.append("résultats " + ("aujourd'hui" if e == 0 else f"dans {e} j") + " : cours imprévisible")

    pos = state["positions"].get(t)
    if pos:
        perf, recul = last / pos["ref"] - 1, last / max(pos["peak"], last) - 1
        if perf <= -cfg["positions"]["perte_depuis_achat"][cat]:
            verdict, conf = "VENDRE", max(conf, 70)
            why.insert(0, f"ta position est à {pct(perf)} (seuil de perte atteint)")
        elif recul <= -cfg["positions"]["recul_depuis_sommet"][cat]:
            verdict, conf = "VENDRE", max(conf, 65)
            why.insert(0, f"recul de {pct(recul)} depuis le sommet de ta position : sécuriser le gain")
        notes.append(f"ta position : {pct(perf)} depuis le suivi")

    conf = max(35, min(85, conf))

    lev_ok = (verdict == "ACHETER" and cat != "pari" and conf >= cfg["levier"]["confiance_min"]
              and vol <= cfg["levier"]["vol_max"] and sma200 is not None and last > sma200
              and rsi <= 70 and not (e is not None and e <= 5))
    if lev_ok:
        levier = "possible, prudent (x2 max, stop serré, petite part du capital)"
    elif cat == "pari":
        levier = "non (jamais sur un pari très risqué)"
    elif verdict != "ACHETER":
        levier = "non (pas de signal d'achat net)"
    else:
        levier = "non (confiance ou volatilité insuffisantes)"

    return {"verdict": verdict, "conf": int(conf), "levier": levier, "lev_ok": lev_ok,
            "why": why, "notes": notes, "last": last, "vol": vol}


def ligne_avis(a):
    return f"{ICON[a['verdict']]} {a['verdict']} ({a['conf']} %) · levier : {'oui' if a['lev_ok'] else 'non'}"


# ------------------------------------------------------------ actualités
def news_items(t):
    """[(titre, source, url, timestamp)] : Yahoo pour tout le monde, Finnhub en plus pour les actions US."""
    out = []
    try:
        for n in yf.Ticker(t).news or []:
            c = n.get("content") or n
            ts = c.get("pubDate") or c.get("providerPublishTime")
            if not ts or not c.get("title"):
                continue
            ts = float(ts) if isinstance(ts, (int, float)) else pd.Timestamp(ts).timestamp()
            url = (c.get("canonicalUrl") or {}).get("url") or c.get("link") or ""
            src = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo"
            out.append((c["title"], src, url, ts))
    except Exception:
        pass
    if FH and "." not in t and "=" not in t:
        try:
            r = requests.get("https://finnhub.io/api/v1/company-news",
                             params={"symbol": t, "from": TODAY - dt.timedelta(days=1), "to": TODAY, "token": FH},
                             timeout=20).json()
            out += [(x["headline"], x.get("source", "?"), x.get("url", ""), x.get("datetime", 0)) for x in r
                    if x.get("headline")]
        except Exception:
            pass
    return out


def news_sensibles(t, heures=3):
    """Actualités récentes avec un mot-clé sensible, jamais renvoyées deux fois."""
    lim = NOW.timestamp() - heures * 3600
    res = []
    for title, src, url, ts in sorted(news_items(t), key=lambda x: -x[3]):
        low = title.lower()
        neg, pos = any(k in low for k in NEG_KW), any(k in low for k in POS_KW)
        if ts < lim or not (neg or pos):
            continue
        if once("news:" + hashlib.md5(title.lower().encode()).hexdigest()[:12], 30):
            res.append(("⚠️" if neg else "📰", title[:110], src, url))
    return res[:2]


# ------------------------------------------------------------ commandes Telegram
def read_commands():
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                         params={"offset": state["offset"] + 1}, timeout=20).json()
    except Exception:
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
            if cmd == "/achat" and len(p) >= 2:
                cmd_achat(p[1])
            elif cmd == "/vente" and len(p) >= 2:
                cmd_vente(p[1])
            elif cmd == "/avis" and len(p) >= 2:
                cmd_avis(p[1])
            elif cmd == "/statut":
                notify(statut())
            else:
                notify("Commandes :\n/avis nvidia : mon avis détaillé\n/achat nvidia : je suis ta position\n"
                       "/vente nvidia : j'arrête de la suivre\n/statut : mon avis sur toutes tes valeurs")
        except Exception as e:
            notify(f"Je n'ai pas compris ({e}). Exemple : /avis nvidia")


def cmd_avis(q):
    w = resolve(q)
    if not w:
        notify(f"Je ne connais pas « {q} ».")
        return
    a = avis(w, finished_bars(history(w["ticker"]), w["ticker"]))
    notify(f"{w['nom']}\n{ligne_avis(a)}\nPourquoi : " + ", ".join(a["why"]) +
           f"\nLevier : {a['levier']}\nVolatilité : {a['vol'] * 100:.0f} %/an"
           + ("".join(f"\n• {n}" for n in a["notes"]))
           + "\n\nLa confiance est un indice maison, pas une probabilité de gain.")


def cmd_achat(q):
    w = resolve(q)
    if not w:
        notify(f"Je ne connais pas « {q} ». Essaie le nom court (ex. nvidia) ou le ticker.")
        return
    last = float(history(w["ticker"]).Close.iloc[-1])
    state["positions"][w["ticker"]] = {"ref": last, "peak": last, "date": str(TODAY)}
    notify(f"📌 Je suis maintenant ta position {w['nom']}.\nPrix de référence : {px(last)} "
           f"(cours Yahoo dans sa devise, pas forcément celui de ton courtier).\n"
           f"Je te préviens si elle recule trop. Pense à poser un stop dans l'app si c'est possible.")


def cmd_vente(q):
    w = resolve(q)
    if w and state["positions"].pop(w["ticker"], None):
        notify(f"✅ J'arrête de suivre ta position {w['nom']}.")
    else:
        notify(f"Aucune position « {q} » suivie.")


def statut():
    lines = ["📋 Mon avis sur tes valeurs"]
    for w in WL:
        try:
            a = avis(w, finished_bars(history(w["ticker"]), w["ticker"]))
            lines.append(f"• {w['nom']} : {ligne_avis(a)}")
        except Exception:
            lines.append(f"• {w['nom']} : pas de données")
    return "\n".join(lines)


# ------------------------------------------------------------ récap du matin (9h Paris)
def daily():
    if not MANUAL and (PARIS.hour != 9 or state["daily_sent"] == str(TODAY)):
        return  # deux crons (été/hiver) : on n'envoie qu'une fois, à 9h heure de Paris
    state["daily_sent"] = str(TODAY)

    rows, fails, ordres, prevu = [], [], [], []
    for w in WL:
        try:
            df = finished_bars(history(w["ticker"]), w["ticker"])
            a = avis(w, df)
        except Exception:
            fails.append(w["nom"])
            continue
        m1 = float(df.Close.iloc[-1] / df.Close.iloc[-2] - 1)
        old = state["last_verdict"].get(w["ticker"])
        state["last_verdict"][w["ticker"]] = a["verdict"]
        rows.append((w, a, m1, old))

    for w, a, m1, old in rows:
        chg = f" (hier : {old})" if old and old != a["verdict"] else ""
        if a["verdict"] != "GARDER":
            ordres.append(f"{ICON[a['verdict']]} {w['nom']} — {a['verdict']} ({a['conf']} %){chg}\n"
                          f"   {', '.join(a['why'][:2])} · {pct(m1)} hier\n   Levier : {a['levier']}")
        else:
            prevu.append(f"{w['nom']} ({a['conf']} %){chg}")
        for n in a["notes"]:
            if "résultats" in n:
                ordres.append(f"⚠️ {w['nom']} : {n}")

    out = [f"📊 Bourse — {JOURS[PARIS.weekday()]} {PARIS.day} {MOIS[PARIS.month - 1]} · ouverture"]
    if ordres:
        out.append("\n".join(ordres))
    if prevu:
        out.append("⚪ GARDER : " + ", ".join(prevu))
    if not ordres:
        out.insert(1, "✅ Rien à faire : pas de signal net aujourd'hui.")

    month = f"{TODAY.year}-{TODAY.month:02d}"
    if TODAY.day >= cfg["dca_jour"] and state["dca_month"] != month:
        state["dca_month"] = month
        etfs = ", ".join(w["nom"] for w in WL if w.get("dca"))
        out.append(f"📅 Rappel du mois : ton versement sur tes ETF ({etfs}).")
    if fails:
        out.append("ℹ️ Pas de données aujourd'hui : " + ", ".join(fails) + ".")
    out.append("Avis automatique basé sur la tendance ; la confiance n'est pas une probabilité. "
               "Pas un conseil en investissement.")
    notify("\n\n".join(out))


# ------------------------------------------------------------ veille (toutes les 15 min en séance)
def flash_move(t):
    """Variation sur ~1 h à partir de données 5 min. None si la bourse est fermée ou sans données."""
    df = yf.download(t, period="1d", interval="5m", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    if len(df) < 13:
        return None
    if (pd.Timestamp(NOW) - df.index[-1].tz_convert("UTC")).total_seconds() > 20 * 60:
        return None  # dernière bougie trop ancienne : marché fermé
    return float(df.Close.iloc[-1] / df.Close.iloc[-13] - 1)


def veille():
    lines = []
    for w in WL:
        t, cat = w["ticker"], w["cat"]
        try:
            full = history(t)
        except Exception:
            continue
        if full.index[-1].date() != NOW.astimezone(ZoneInfo(tzname(t))).date():
            continue  # bourse fermée ou pas de séance aujourd'hui
        c = full.Close
        last = float(c.iloc[-1])
        m = float(last / c.iloc[-2] - 1)
        try:
            a = avis(w, finished_bars(full, t), last_live=last)
        except Exception:
            a = None
        suite = f"\n   Avis actuel : {ligne_avis(a)}" if a else ""
        tenu = " (position suivie)" if t in state["positions"] else ""
        local = []

        thr = cfg["veille"]["jour"][cat]
        if abs(m) >= thr and once(f"jour:{t}:{'h' if m > 0 else 'b'}:{TODAY}:{int(abs(m) / thr)}"):
            local.append(f"⚡ {w['nom']}{tenu} : {pct(m)} sur la journée (cours {px(last)}).")
        try:
            f = flash_move(t)
        except Exception:
            f = None
        fthr = cfg["veille"]["flash"][cat]
        if f is not None and abs(f) >= fthr and once(f"flash:{t}:{'h' if f > 0 else 'b'}:{TODAY}:{PARIS.hour}"):
            local.append(f"🚨 {w['nom']}{tenu} : {pct(f)} en 1 heure, mouvement brutal.")
        for ico, title, src, url in news_sensibles(t):
            local.append(f"{ico} {w['nom']}{tenu} : « {title} » ({src}) {url}".strip())

        if local:
            lines.append("\n".join(local) + suite)
    if lines:
        notify("Alerte bourse\n\n" + "\n\n".join(lines))
    elif MANUAL:
        notify("✅ Veille : rien d'anormal en ce moment (ou bourses fermées).")


# ------------------------------------------------------------ principal
if __name__ == "__main__":
    try:
        read_commands()
        veille() if MODE == "veille" else daily()
    finally:
        prune()
        json.dump(state, open("state.json", "w"), indent=1)
