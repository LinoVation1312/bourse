"""Mode veille v2 : surveillance multi-niveaux (ALERTES D'INFORMATION, aucun ordre, aucun signal de trading).

Lancé toutes les 30 min par GitHub Actions. Il ne modifie jamais tes positions : seules tes commandes
/achat et /vente le font. Il peut en revanche BLOQUER de nouveaux signaux d'achat du bot (drapeaux).

Détecteurs : paliers de mouvement, gap d'ouverture, volume relatif (même heure), accélération 15 min / 1 h,
retournement, volatilité anormale, cassure EMA20/EMA50, comparaison marché/secteur, suspension ou cours
incohérent, news classées (critique / important / à surveiller) et dédupliquées, calendrier J-5/J-2/J0.
Priorités : 1 urgent (risque sur une position), 2 important, 3 information (limitée par valeur et par jour).
"""
import re
import json
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import requests
import pandas as pd
import yfinance as yf

import bot

cfg, V = bot.cfg, bot.cfg["veille"]
NOW, TODAY, MANUAL, FH = bot.NOW, bot.TODAY, bot.MANUAL, bot.FH
VFILE = "veille_state.json"

try:
    vs = json.load(open(VFILE))
except Exception:
    vs = {}
for k, v in {"sent": {}, "news_seen": [], "news_titles": {}, "cal": {}, "daily": {}, "maxlvl": {},
             "p3count": {}, "flags": {}, "journal": [], "weekly": {}}.items():
    vs.setdefault(k, v)

WLD = {w["ticker"]: w for w in cfg["watchlist"]}
ALERTS, WARNS, INFO = [], [], {}
_snap_cache = {}


# ---------------------------------------------------------------- Marché
def is_eu(t):
    if t in WLD:
        return WLD[t].get("devise") == "EUR"
    return t.startswith("^STOXX") or "." in t


def tz(t):
    return ZoneInfo("Europe/Paris" if is_eu(t) else "America/New_York")


def minutes_since_open(t):
    n = NOW.astimezone(tz(t))
    if n.weekday() >= 5:
        return None
    o, c = (dt.time(9, 0), dt.time(17, 45)) if is_eu(t) else (dt.time(9, 30), dt.time(16, 10))
    if not (o <= n.time() <= c):
        return None
    return (dt.datetime.combine(n.date(), n.time()) - dt.datetime.combine(n.date(), o)).seconds / 60


def _snapshot(t):
    df = yf.download(t, period="1mo", interval="15m", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    if df.empty:
        raise ValueError("pas de données intraday")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    loc = df.index.tz_convert(tz(t))
    df = df.assign(_d=loc.date, _tod=loc.time)
    d_today = df._d.iloc[-1]
    prev, today = df[df._d < d_today], df[df._d == d_today]
    if prev.empty:
        raise ValueError("pas de clôture précédente")
    age = (pd.Timestamp(NOW) - (df.index[-1] + pd.Timedelta(minutes=15))).total_seconds() / 60
    p0, last = float(prev.Close.iloc[-1]), float(today.Close.iloc[-1])
    tod_last = today._tod.iloc[-1]
    ref = [g[g._tod <= tod_last].Volume.sum() for _, g in prev.groupby("_d")][-20:]
    rvol = float(today.Volume.sum() / np.mean(ref)) if len(ref) >= 5 and np.mean(ref) > 0 else None
    rngs = [(g.High.max() - g.Low.min()) / g.Close.iloc[0] for _, g in prev.groupby("_d")][-10:]
    first_open = float(today.Open.iloc[0])
    return {
        "last": last, "prev": p0, "move": last / p0 - 1, "age": age,
        "low": float(today.Low.min()), "high": float(today.High.max()),
        "first_open": first_open, "gap": first_open / p0 - 1,
        "rvol": rvol, "rvol_n": len(ref),
        "rng": float((today.High.max() - today.Low.min()) / p0),
        "rng_ref": float(np.mean(rngs)) if len(rngs) >= 5 else None,
        "m15": last / float(today.Close.iloc[-2]) - 1 if len(today) >= 2 else None,
        "m1h": last / float(today.Close.iloc[-5]) - 1 if len(today) >= 5 else None,
        "zero_vol": bool(len(today) >= 6 and (today.Volume.iloc[-5:-1] == 0).all()),
        "n": len(today), "ts": df.index[-1],
    }


def snapshot(t):
    if t not in _snap_cache:
        try:
            _snap_cache[t] = _snapshot(t)
        except Exception as e:
            _snap_cache[t] = e
    if isinstance(_snap_cache[t], Exception):
        raise _snap_cache[t]
    return _snap_cache[t]


def obs(s):
    return f"\n   ⏱ obs. {s['ts'].strftime('%H:%M')} UTC, données retardées de ~{max(0, s['age']):.0f} min"


def market_ctx(t, s):
    """Comparaison au marché et au secteur : mouvement propre à la valeur ou partagé ?"""
    parts, bm = [], None
    b = V["bench"]["EUR" if is_eu(t) else "USD"]
    try:
        bm = snapshot(b)["move"]
        parts.append(f"{b} {bm:+.1%}")
    except Exception:
        parts.append("marché indisponible")
    sec = WLD.get(t, {}).get("secteur")
    if sec:
        try:
            parts.append(f"{sec} {snapshot(sec)['move']:+.1%}")
        except Exception:
            pass
    tag = ""
    if bm is not None:
        idio = abs(s["move"] - bm) >= 0.03 or (s["move"] * bm < 0 and abs(s["move"]) >= 0.02)
        tag = " → propre à la valeur" if idio else " → mouvement partagé avec le marché"
    return " | ".join(parts) + tag


# ---------------------------------------------------------------- Alertes / dédoublonnage
def once(key):
    if key in vs["sent"]:
        return False
    vs["sent"][key] = str(TODAY)
    return True


def add(prio, t, kind, text, key=None, px=None):
    held = t in bot.state["positions"]
    if prio == 3:
        ck = f"{t}:{TODAY}"
        if vs["p3count"].get(ck, 0) >= V["p3_max_par_valeur"]:
            return False
    if key is not None and not once(key):
        return False
    if prio == 3:
        vs["p3count"][ck] = vs["p3count"].get(ck, 0) + 1
    ALERTS.append({"prio": prio, "t": t, "held": held, "kind": kind, "text": text})
    vs["journal"].append({"ts": NOW.isoformat(timespec="minutes"), "d": str(TODAY), "t": t, "kind": kind,
                          "prio": prio, "held": held, "px": px, "txt": text.split("\n")[0][:140]})
    return True


def flag(t, level, why):
    cur = vs["flags"].get(t)
    if cur and cur["date"] == str(TODAY) and cur["level"] == "critical" and level != "critical":
        return
    vs["flags"][t] = {"date": str(TODAY), "level": level, "why": why}


# ---------------------------------------------------------------- News classées
LV = {"watch": 1, "important": 2, "critical": 3}
LVN = {1: "watch", 2: "important", 3: "critical"}
CATS = [
    ("dilution", "important", "Dilution / émission d'actions",
     r"offering|dilut|capital raise|private placement|share sale|at-the-market|convertible notes"),
    ("analyste", "watch", "Recommandation / objectif de cours",
     r"downgrad|cuts? (price )?target|lowers? (price )?target|price target (cut|lower)"),
    ("guidance", "important", "Résultats / prévisions négatifs",
     r"profit warning|cuts? (guidance|outlook|forecast)|lowers? (guidance|outlook|forecast)|"
     r"misses? (estimates|expectations)|weak (guidance|outlook)"),
    ("juridique", "critical", "Enquête / litige / sanction",
     r"investigation|\bprobe\b|lawsuit|\bsues?\b|subpoena|sec charges|fraud|indict|sanction|class action|antitrust"),
    ("suspension", "critical", "Suspension / retrait de cotation",
     r"trading halt|halted|suspend|delist"),
    ("contrat", "important", "Contrat / partenariat / dirigeant",
     r"loses? (a )?(contract|customer)|terminates?|(ceo|cfo) (resign|step|depart)|steps? down|resigns?"),
    ("dette", "critical", "Dette / faillite",
     r"bankrupt|chapter 11|\bdefault|restructur|going concern"),
    ("incident", "important", "Cyberattaque / rappel / sécurité",
     r"cyber ?attack|data breach|\bhack|recall|safety (issue|probe)"),
    ("mna", "important", "Acquisition / fusion (impact non présumé)",
     r"acquir|acquisition|merger|takeover|buyout|to be acquired"),
]
NEG = re.compile(r"denies|denied|dismiss|rejects?|no evidence|cleared|settles?|rumou?r|false")


def classify(headline):
    h = headline.lower()
    hits = [(LV[l], lab) for _, l, lab, rx in CATS if re.search(rx, h)]
    if not hits:
        return "info", "Actualité non classée"
    n, lab = max(hits)
    if NEG.search(h):
        n -= 1
        lab += " (démenti / ambigu ?)"
    return (LVN[n] if n >= 1 else "info"), lab


def toks(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def similar(a, b):
    A, B = toks(a), toks(b)
    return bool(A and B) and len(A & B) / len(A | B) >= 0.6


def news_for(t, held):
    if not FH or is_eu(t):
        return
    try:
        r = requests.get("https://finnhub.io/api/v1/company-news",
                         params={"symbol": t, "from": TODAY - dt.timedelta(days=1), "to": TODAY, "token": FH},
                         timeout=20).json()
    except Exception:
        return
    lim = NOW.timestamp() - V["news_hours"] * 3600
    titles = vs["news_titles"].setdefault(t, [])
    sent_n = 0
    for x in sorted(r, key=lambda z: -z.get("datetime", 0)):
        h = x.get("headline", "")
        if x.get("datetime", 0) < lim or x.get("id") in vs["news_seen"]:
            continue
        vs["news_seen"].append(x.get("id"))
        if any(similar(h, o) for o in titles):
            continue  # doublon d'une information déjà vue
        titles.append(h)
        lvl, lab = classify(h)
        when = dt.datetime.fromtimestamp(x["datetime"], dt.timezone.utc).strftime("%H:%M UTC")
        body = (f"{h[:120]} ({x.get('source', '?')}, {when})\n   Catégorie : {lab}\n   {x.get('url', '')}")
        summ = (x.get("summary") or "").strip()
        if summ:
            body += f"\n   Résumé : {summ[:140]}"
        if lvl == "critical":
            flag(t, "critical", lab)
            add(1 if held else 2, t, "news", f"{t} [CRITIQUE] {body}")
        elif lvl == "important":
            flag(t, "important", lab)
            add(2, t, "news", f"{t} [IMPORTANT] {body}")
        elif held and sent_n < V["news_max_par_valeur"]:
            add(3, t, "news", f"{t} {lab if lvl == 'watch' else 'news'} : {body}")
        else:  # « à surveiller » sur watchlist : journal seulement, pas de notification
            vs["journal"].append({"ts": NOW.isoformat(timespec="minutes"), "d": str(TODAY), "t": t,
                                  "kind": "news-info", "prio": 3, "held": held, "px": None, "txt": h[:140]})
        sent_n += 1


# ---------------------------------------------------------------- Calendrier
def calendar(t):
    c = vs["cal"].get(t)
    if not c or c.get("fetched") != str(TODAY):
        c = {"fetched": str(TODAY), "earn": None, "exdiv": None}
        try:
            cal = yf.Ticker(t).calendar
            if isinstance(cal, dict):
                for key, k2 in (("Earnings Date", "earn"), ("Ex-Dividend Date", "exdiv")):
                    d = cal.get(key)
                    if d:
                        d0 = d[0] if isinstance(d, (list, tuple)) else d
                        c[k2] = str(pd.Timestamp(d0).date())
        except Exception:
            pass
        vs["cal"][t] = c
    held = t in bot.state["positions"]
    for k2, lab in (("earn", "Résultats"), ("exdiv", "Détachement de dividende")):
        if not c[k2]:
            continue
        d = dt.date.fromisoformat(c[k2])
        n = int(np.busday_count(TODAY, d)) if d >= TODAY else -1
        if n < 0 or n > 5:
            continue
        label = 0 if n == 0 else 2 if n <= 2 else 5
        txt = (f"{t} : {lab} le {d} (J-{n} séance{'s' if n > 1 else ''})."
               + (" Position détenue : vérifie ton risque avant l'événement (gap possible) ; "
                  "aucune vente automatique." if held and k2 == "earn" else ""))
        add(2 if (held and k2 == "earn" and label <= 2) else 3, t, "calendrier", txt, key=f"{t}:{k2}:{d}:{label}")


def weekly_unknown():
    wk = TODAY.isocalendar()
    key = f"{wk[0]}-{wk[1]}"
    if vs["weekly"].get("unknown") == key:
        return
    unk = [t for t in WLD if not vs["cal"].get(t, {}).get("earn")]
    vs["weekly"]["unknown"] = key
    if unk:
        ALERTS.append({"prio": 3, "t": "-", "held": False, "kind": "calendrier",
                       "text": "Dates de résultats inconnues ou non fiables pour : " + ", ".join(unk)
                               + ". Vérifie-les à la main avant d'entrer."})


# ---------------------------------------------------------------- Détecteurs de marché
def daily_ema(t):
    d = vs["daily"].get(t)
    if not d or d.get("date") != str(TODAY):
        try:
            l = bot.prep(t).iloc[-1]
            d = {"date": str(TODAY), "ema20": float(l.ema20), "ema50": float(l.ema50), "close": float(l.Close)}
        except Exception:
            d = {"date": str(TODAY), "ema20": None, "ema50": None, "close": None}
        vs["daily"][t] = d
    return d


def detect(t, s, mo):
    pos = bot.state["positions"]
    held = t in pos
    p = pos.get(t)
    lev = p["lev"] if held else 1
    nom = WLD.get(t, {}).get("nom", t)
    ctx = market_ctx(t, s)
    px = s["last"]
    th = V["position_drop_pct"]
    eventful = abs(s["move"]) >= 0.02
    INFO[t] = {"line": f"{nom} {px:.2f} ({s['move']:+.1%})", "ctx": ctx,
               "obs": f"⏱ {s['ts'].strftime('%H:%M')} UTC, données retardées de ~{max(0, s['age']):.0f} min"}

    # suspension / cours incohérent (anomalie de DONNÉES ou de marché)
    if s["zero_vol"] and (mo or 0) > 60:
        add(1 if held else 2, t, "suspension",
            f"{t} : aucun volume depuis ≥ 1 h en séance : suspension de cotation possible. Vérifie dans l'app."
            + obs(s), key=f"{t}:susp:{TODAY}", px=px)
        flag(t, "critical", "suspension possible")

    # position : baisse en équivalent produit, stop, hausse
    if held:
        prod = max(-1.0, s["move"] * lev)
        if prod <= -th:
            b = int(abs(prod) // th)
            stop_txt = f" | stop {p['stop']:.2f}" if p.get("stop") else ""
            add(1, t, "baisse", f"{t} x{lev:g} : {s['move']:+.1%} depuis la clôture ≈ {prod:+.1%} sur ton produit\n"
                f"   Cours {px:.2f}{stop_txt}\n   {ctx}" + obs(s), key=f"{t}:drop:{TODAY}:{b}", px=px)
        if p.get("stop") and s["low"] <= p["stop"]:
            add(1, t, "stop", f"{t} : stop {p['stop']:.2f} touché en séance (plus bas {s['low']:.2f}, cours {px:.2f}). "
                f"À toi de décider : /vente {t} {px:.2f}" + obs(s), key=f"{t}:stop:{TODAY}", px=px)
        if prod >= V["position_up_pct"]:
            add(2, t, "hausse", f"{t} : {s['move']:+.1%} aujourd'hui (≈ {prod:+.1%} produit). "
                f"Pense à remonter ton stop dans l'app.", key=f"{t}:up:{TODAY}", px=px)

    # paliers de mouvement (2 %, 3 %, 5 %…), un message par nouveau palier franchi
    idx = sum(abs(s["move"]) >= q for q in V["paliers"])
    if idx:
        k = f"{t}:{TODAY}:{'up' if s['move'] > 0 else 'dn'}"
        if idx > vs["maxlvl"].get(k, 0):
            pal = V["paliers"][idx - 1]
            prio = 2 if (idx >= 3 or (held and lev > 1 and idx >= 2)) else 3
            vol = f" | RVOL x{s['rvol']:.1f}" if s["rvol"] else ""
            if add(prio, t, "mouvement",
                   f"{t} ({nom}) {s['move']:+.1%} (palier {pal:.0%}) | cours {px:.2f}{vol}\n   {ctx}"
                   + ("\n   Pas un signal d'achat : attends l'analyse de clôture." if not held else "") + obs(s),
                   px=px):
                vs["maxlvl"][k] = idx

    # gap d'ouverture
    if s["n"] >= 1 and abs(s["gap"]) >= V["gap_pct"]:
        extra = (" Avec un produit à levier, recalcule ton risque au cours réel (une barrière de turbo peut "
                 "avoir été franchie à l'ouverture)." if held and lev > 1 else "")
        add(1 if (held and s["gap"] * lev <= -th) else 2, t, "gap",
            f"{t} : gap d'ouverture {s['gap']:+.1%} (ouverture {s['first_open']:.2f} vs clôture {s['prev']:.2f})."
            f"{extra}\n   {ctx}" + obs(s), key=f"{t}:gap:{TODAY}", px=px)

    # volume relatif à la même heure (référence : jusqu'à 20 séances)
    if s["rvol"] and s["rvol"] >= V["rvol_min"] and s["n"] >= 4:
        add(2 if (held or eventful) else 3, t, "volume",
            f"{t} : volume anormal, RVOL x{s['rvol']:.1f} (même heure, {s['rvol_n']} séances de référence), "
            f"variation {s['move']:+.1%}." + obs(s), key=f"{t}:rvol:{TODAY}", px=px)

    # accélération
    for kk, lab, lim in (("m15", "15 min", V["accel_15m"]), ("m1h", "1 h", V["accel_1h"])):
        m = s[kk]
        if m is not None and abs(m) >= lim:
            add(2 if held else 3, t, "accélération", f"{t} : {m:+.1%} en {lab}, cours {px:.2f}.\n   {ctx}" + obs(s),
                key=f"{t}:acc{kk}:{TODAY}:{'u' if m > 0 else 'd'}", px=px)

    # retournement brutal
    rv = V["reversal_pct"]
    if s["high"] / s["prev"] - 1 >= rv and px / s["high"] - 1 <= -rv:
        add(2 if held else 3, t, "retournement", f"{t} : retournement baissier (haut du jour {s['high']:.2f} → "
            f"{px:.2f}).\n   {ctx}" + obs(s), key=f"{t}:rev:{TODAY}:d", px=px)
    if s["low"] / s["prev"] - 1 <= -rv and px / s["low"] - 1 >= rv:
        add(2 if held else 3, t, "retournement", f"{t} : retournement haussier (bas du jour {s['low']:.2f} → "
            f"{px:.2f}).\n   {ctx}" + obs(s), key=f"{t}:rev:{TODAY}:u", px=px)

    # volatilité anormale
    if s["rng_ref"] and s["rng"] >= V["range_x"] * s["rng_ref"] and s["rng"] >= 0.03:
        add(2 if held else 3, t, "volatilité", f"{t} : amplitude du jour {s['rng']:.1%} (≈ x{s['rng'] / s['rng_ref']:.1f} "
            f"la normale récente de {s['rng_ref']:.1%})." + obs(s), key=f"{t}:vol:{TODAY}", px=px)

    # cassure EMA20 / EMA50 (niveaux calculés sur clôtures journalières)
    d = daily_ema(t)
    for n in (20, 50):
        e = d.get(f"ema{n}")
        if e and d["close"] >= e > px:
            add(2 if held else 3, t, "tendance", f"{t} : cours {px:.2f} repasse sous l'EMA{n} ({e:.2f}) en séance. "
                f"À confirmer à la clôture." + obs(s), key=f"{t}:ema{n}:{TODAY}", px=px)


# ---------------------------------------------------------------- Principal
def _clean(a):
    inf = INFO.get(a["t"], {})
    lines = [l for l in a["text"].split("\n")
             if not l.strip().startswith("⏱ obs.") and l.strip() != inf.get("ctx")]
    return "\n".join(lines).strip()


def compose():
    ico = {1: "🔴", 2: "🟠", 3: "🔵"}
    held = bot.state["positions"]
    by = {}
    for a in ALERTS:
        by.setdefault(a["t"], []).append(a)
    order = sorted([t for t in by if t != "-"],
                   key=lambda t: (min(a["prio"] for a in by[t]), t not in held, t))
    blocks = []
    for t in order:
        items = sorted(by[t], key=lambda a: a["prio"])
        inf = INFO.get(t)
        head = f"{ico[items[0]['prio']]} {t} [{'POSITION' if t in held else 'WATCH'}]"
        if inf:
            head += f" — {inf['line']}\n   {inf['ctx']}\n   {inf['obs']}"
        blocks.append(head + "\n" + "\n".join(f"{ico[a['prio']]} {_clean(a)}" for a in items))
    for a in by.get("-", []):
        blocks.append(f"{ico[a['prio']]} {a['text']}")
    if any(a["kind"] == "news" for a in ALERTS):
        blocks.append("ℹ️ Catégories de news = présélection par mots-clés, pas un fait établi : vérifie la source.")
    if blocks:
        blocks.append("Surveillance = information. Aucun ordre n'est passé et ce n'est pas un conseil d'investissement.")
    return "\n\n".join(blocks)


def prune():
    lim = str(TODAY - dt.timedelta(days=3))
    vs["sent"] = {k: v for k, v in vs["sent"].items() if v >= lim}
    vs["maxlvl"] = {k: v for k, v in vs["maxlvl"].items() if k.split(":")[1] >= lim}
    vs["p3count"] = {k: v for k, v in vs["p3count"].items() if k.split(":")[1] >= lim}
    vs["flags"] = {k: v for k, v in vs["flags"].items() if v["date"] >= str(TODAY - dt.timedelta(days=5))}
    vs["news_seen"] = vs["news_seen"][-600:]
    vs["news_titles"] = {k: v[-60:] for k, v in vs["news_titles"].items()}
    vs["journal"] = vs["journal"][-1500:]


def main():
    bot.read_commands()
    pos = bot.state["positions"]
    tickers = list(pos) + [w["ticker"] for w in cfg["watchlist"] if w["ticker"] not in pos]
    snaps, status = {}, []

    for t in tickers:
        calendar(t)
        mo = minutes_since_open(t)
        if mo is None and not MANUAL:
            continue  # bourse fermée
        try:
            s = snapshot(t)
        except Exception as e:
            if MANUAL or t in pos:
                WARNS.append(f"{t} : {e}")
            continue
        if s["age"] > V["stale_minutes"]:
            if (MANUAL or (mo or 0) > V["stale_minutes"] + 15) and (MANUAL or once(f"{t}:stale:{TODAY}")):
                WARNS.append(f"{t} : données retardées de {s['age']:.0f} min, aucune alerte (nouveaux signaux bloqués)")
                flag(t, "data", "données périmées")
            continue
        if abs(s["move"]) > 0.35:
            WARNS.append(f"{t} : cours incohérent ({s['move']:+.0%} vs clôture), ignoré ; nouveaux signaux bloqués")
            flag(t, "data", "cours incohérent")
            continue
        if vs["flags"].get(t, {}).get("level") == "data":
            del vs["flags"][t]  # les données sont redevenues fraîches
        snaps[t] = (s, mo)
        status.append(f"{t} {s['last']:.2f} ({s['move']:+.1%}"
                      + (f", RVOL x{s['rvol']:.1f}" if s["rvol"] else "") + ")")

    for t, (s, mo) in snaps.items():
        detect(t, s, mo)
    for t in tickers:
        if t in snaps or MANUAL or minutes_since_open(t) is not None:
            news_for(t, t in pos)

    # mouvement simultané de plusieurs positions : exposition commune
    hit = [t for t in pos if t in snaps and snaps[t][0]["move"] * pos[t]["lev"] <= -V["simul_pct"]]
    if len(hit) >= 2:
        add(2, "-", "simultané", f"Positions {', '.join(hit)} baissent en même temps : exposition commune "
            f"(même secteur ou marché). Revois ton risque global.", key=f"simul:{TODAY}")
    weekly_unknown()

    # suivi ultérieur des alertes (mesure d'utilité, réduction des faux positifs)
    for e in vs["journal"]:
        if e.get("px") and "ret_suivi" not in e and e["d"] < str(TODAY) and e["t"] in snaps:
            e["ret_suivi"] = round(snaps[e["t"]][0]["last"] / e["px"] - 1, 4)

    msg = compose()
    out = ([msg] if msg else []) + (["⚠️ " + "\n⚠️ ".join(WARNS)] if WARNS else [])
    if MANUAL:
        out.append("🔎 Veille (test manuel) : " + (" | ".join(status) if status else "aucune donnée")
                   + ("" if ALERTS else "\n✅ Aucune alerte."))
    if out:
        bot.notify("\n\n".join(out))


if __name__ == "__main__":
    try:
        main()
    finally:
        prune()
        json.dump(vs, open(VFILE, "w"), indent=1, default=str)
        json.dump(bot.state, open("state.json", "w"), indent=1)
