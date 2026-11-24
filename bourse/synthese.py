"""Synthèse globale : un seul message, toujours construit dans le même ordre, pour le briefing d'ouverture,
le débriefing du soir et la commande /synthese.

  🌍 Marché            indices, VIX, ambiance de la séance (combien de tes valeurs montent / baissent)
  💼 Tes positions     (si tu en suis)
  ⚡ Gros changements  gros mouvements, changements de tendance, événements sur tes positions, séance inhabituelle
  📰 Actus à retenir   les titres sensibles les plus importants, une fois chacun, classés (positions et alertes d'abord)
  📈 Tendances         tendance de fond regroupée par état (haussière / repli / baissière)
  ⏰ À surveiller      résultats, dividendes, événements sous 48 h
"""
from . import agenda, analysis as A, data, flash as F, markets, news as N, positions as P, render as R, state as ST, trend as T, translate
from .config import entry_for, watch_entries
from .fmt import b, esc, fr_date, pct, price

INDICES = (("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("^FCHI", "CAC 40"))
VIX = "^VIX"
NEWS_MAX = 6
KINDS = {"ouverture": "après l'ouverture de Wall Street", "soir": "clôture", "instant": "à l'instant"}
COOLDOWN = {"stop": 3, "target": 6, "time": 20, "trail1": 999, "trail2": 999, "loss": 24, "peak": 24}


def tracked(ctx):
    """Watchlist + positions suivies (même hors watchlist)."""
    seen = {w["ticker"]: w for w in watch_entries(ctx.cfg)}
    for t in ctx.state["positions"]:
        seen.setdefault(t, entry_for(ctx.cfg, t))
    return list(seen.values())


# ------------------------------------------------------------------ marché
def indices(now, live_raw=None):
    """{'S&P 500': {day, above200}, ..., 'VIX': niveau}. Variation du jour en direct si live_raw (barres de 5 min)."""
    raw = data.daily([t for t, _ in INDICES] + [VIX], period="1y")
    out = {}
    for t, label in INDICES:
        df = raw.get(t)
        if df is None or len(df) < 200:
            continue
        c = data.completed(df, t, now).Close.astype(float)
        day = float(c.iloc[-1] / c.iloc[-2] - 1)
        if live_raw is not None:
            st = data.live_stats(live_raw.get(t), t, now)
            if st:
                day = st["day"]
        out[label] = {"day": day, "above200": bool(c.iloc[-1] > c.rolling(200).mean().iloc[-1])}
    vix = raw.get(VIX)
    if vix is not None and len(vix):
        level = float(vix.Close.iloc[-1])
        if live_raw is not None:
            st = data.live_stats(live_raw.get(VIX), VIX, now)
            level = st["price"] if st else level
        out["VIX"] = level
    return out


def day_moves(snap, tracked_entries, live_st, today, allow_previous=False):
    """{ticker: variation du jour} : en direct si disponible, sinon la dernière clôture (si elle est d'aujourd'hui,
    ou quelle que soit sa date pour /synthese quand les marchés sont fermés)."""
    out = {}
    for w in tracked_entries:
        t = w["ticker"]
        if t in live_st:
            out[t] = live_st[t]["day"]
            continue
        s = snap.get(t)
        if s and len(s["ind"]) > 2 and (allow_previous or s["ind"].index[-1].date() == today):
            out[t] = float(s["ind"].Close.pct_change().iloc[-1])
    return out


def vix_label(v):
    return "calme" if v < 18 else "nerveux" if v < 25 else "tendu" if v < 32 else "panique"


def verdict(moves, idx):
    if not moves:
        return None
    n = len(moves)
    up = sum(1 for d in moves.values() if d > 0.0005)
    dn = sum(1 for d in moves.values() if d < -0.0005)
    mood = "porteuse" if up / n >= 2 / 3 else "faible" if up / n <= 1 / 3 else "partagée"
    notes = []
    spy = idx.get("S&P 500")
    if spy and not spy["above200"]:
        notes.append("S&P 500 sous sa moy. 200 j")
    if idx.get("VIX", 0) >= 25:
        notes.append("volatilité élevée")
    return f"Ambiance {mood} : {up} de tes {n} valeurs en hausse, {dn} en baisse" + (" · " + ", ".join(notes) if notes else "")


def market_block(idx, moves, nm):
    lines = [b("🌍 Marché")]
    parts = [f"{label} {pct(idx[label]['day'])}" for _, label in INDICES if label in idx]
    if "VIX" in idx:
        parts.append(f"VIX {idx['VIX']:.0f} ({vix_label(idx['VIX'])})")
    lines.append(" · ".join(parts) if parts else "Indices indisponibles pour l'instant.")
    v = verdict(moves, idx)
    if v:
        lines.append(esc(v))
    if len(moves) >= 4:
        best, worst = max(moves, key=moves.get), min(moves, key=moves.get)
        extremes = []
        if moves[best] > 0:
            extremes.append(f"plus forte hausse : {esc(nm.get(best, best))} {pct(moves[best])}")
        if moves[worst] < 0:
            extremes.append(f"plus forte baisse : {esc(nm.get(worst, worst))} {pct(moves[worst])}")
        if extremes:
            txt = " · ".join(extremes)
            lines.append(txt[0].upper() + txt[1:])
    return "\n".join(lines)


# ------------------------------------------------------------------ gros changements
def big_changes(ctx, moves, prices, nm, changes, events, unusual_block):
    held = ctx.state["positions"]
    mult = ctx.cfg.get("synthese", {}).get("seuil_gros_mouvement", 0.6)
    lines = []
    for t, d in sorted(moves.items(), key=lambda kv: -abs(kv[1])):
        w = entry_for(ctx.cfg, t)
        if abs(d) >= ctx.cfg["alertes"]["jour"][w["cat"]] * mult:
            tag = " 💼" if t in held else ""
            lines.append(f"⚡ {b(nm.get(t, t))} {pct(d)}" + (f" · {price(t, prices[t])}" if t in prices else "") + tag)
    lines = lines[:8]
    lines += [t for _, t in changes]
    lines += events
    if unusual_block:
        lines += unusual_block.split("\n")[1:]                      # sans son titre : il est déjà dans cette section
    return b("⚡ Gros changements") + "\n" + ("\n".join(lines) if lines else "Rien de marquant.")


# ------------------------------------------------------------------ actualités
def news_block(ctx, hot, nm, flashed=()):
    """Actus à retenir : grosses infos de marché déjà envoyées en flash (`flashed`, sans filtre sur tes valeurs) + titres
    sensibles par valeur. Une même histoire n'apparaît qu'une fois ; positions d'abord, puis importance, puis date."""
    held = ctx.state["positions"]
    flat = [("_marche", {**it, "score": it.get("score", 0)}) for it in flashed]
    for t, its in hot.items():
        for it in its:
            w = N.words(it["title"])
            if not any(N.similar(w, N.words(f["title"]), 0.6) for _, f in flat):
                flat.append((t, {**it, "score": 4}))
    flat.sort(key=lambda x: (x[0] not in held, -(x[1]["score"] + (x[1]["kind"] == "neg")), -x[1]["ts"]))
    shown, rest = flat[:NEWS_MAX], len(flat) - NEWS_MAX
    translate.fr_many(it["title"] for _, it in shown)
    lines = []
    for t, it in shown:
        label = "Marché" if t == "_marche" else ("💼 " if t in held else "") + nm.get(t, t)
        lines += R.news_lines({t: [it]}, {t: label})
    if rest > 0:
        lines.append(f"<i>+ {rest} autre(s) titre(s) sensible(s) de moindre importance</i>")
    return b("📰 Actus à retenir") + "\n" + ("\n".join(lines) if lines else "Rien de notable.")


# ------------------------------------------------------------------ tendances
def trend_block(snap, entries):
    groups = {}
    for w in entries:
        s = snap.get(w["ticker"])
        if s:
            groups.setdefault(T.assess(s["ind"])["state"], []).append(w["nom"])
    lines = [b("📈 Tendances de fond") + "\n<i>filtre de risque, pas une prévision · détail par valeur : /tendances</i>"]
    for st in ("up", "pullback", "down", "young"):
        names = groups.get(st)
        if names:
            ico, lab = T.LABEL[st]
            shown = ", ".join(esc(n) for n in names[:7]) + (f" +{len(names) - 7}" if len(names) > 7 else "")
            lines.append(f"{ico} {lab.capitalize()} ({len(names)}) : {shown}")
    return "\n".join(lines) if len(lines) > 1 else lines[0] + "\nPas de données récentes."


# ------------------------------------------------------------------ assemblage
def compose(ctx, kind, snap=None):
    """Texte de la synthèse ('ouverture' | 'soir' | 'instant'), ou None si les données manquent (déjà signalé).
    Ne marque rien comme envoyé : c'est le rôle de l'appelant (créneaux, titres déjà vus)."""
    from .jobs import hot_news, trend_changes, warn_data
    now, today, cfg = ctx.now, ctx.now.date(), ctx.cfg
    entries = tracked(ctx)
    nm = {w["ticker"]: w["nom"] for w in entries}

    live = [w for w in entries if markets.is_open(w["ticker"], now)] if kind != "soir" else []
    raw = data.intraday([w["ticker"] for w in live] + [t for t, _ in INDICES] + [VIX]) if live else {}
    live_st = {}
    for w in live:
        st = data.live_stats(raw.get(w["ticker"]), w["ticker"], now)
        if st and st["age_min"] <= 45:
            live_st[w["ticker"]] = st
    if live and not live_st:
        warn_data(ctx, "Aucun cours reçu pour la synthèse")
        return None
    snap = snap if snap is not None else A.snapshot(entries, now)
    if not snap and not live_st:
        warn_data(ctx, "Pas de données pour la synthèse")
        return None

    try:
        idx = indices(now, raw if live else None)
    except Exception as e:                                          # Yahoo capricieux : la synthèse part quand même
        print(f"Indices indisponibles ({type(e).__name__})")
        idx = {}
    moves = day_moves(snap, entries, live_st, today, allow_previous=(kind == "instant" and not live))
    prices = {t: s["price"] for t, s in live_st.items()}
    titre = f"Synthèse · {KINDS[kind]}"
    out = [f"🧭 {b(titre)} · {fr_date(today)}", market_block(idx, moves, nm)]

    events = []
    if ctx.state["positions"]:
        lines = [b("💼 Tes positions")]
        for t, pos in ctx.state["positions"].items():
            w, st, s = entry_for(cfg, t), live_st.get(t), snap.get(t)
            if s:
                P.refresh(w, pos, s["ind"])
            last = st["price"] if st else (float(s["ind"].Close.iloc[-1]) if s else None)
            if last is None:
                lines.append(f"{esc(w['nom'])} : pas de données")
                continue
            lines.append(P.line(w, pos, last, today))
            if st and kind == "ouverture":
                for k, _, text in P.intraday_events(w, pos, last, today, cfg):
                    ST.fresh(ctx.state, f"pos|{t}|{pos['date']}|{k}", now, COOLDOWN.get(k, 24))   # évite le doublon du watch
                    events.append(text)
            if s and kind == "soir":
                lines += [f"    {a}" for a in P.daily_advice(w, pos, s["ind"], today, "soir")]
        out.append("\n".join(lines))

    changes = trend_changes(ctx, snap) if kind != "instant" else []
    un = agenda.unusual(snap, watch_entries(cfg), cfg, today) if kind == "soir" else None
    out.append(big_changes(ctx, moves, prices, nm, changes, events, un))

    try:
        items = N.fetch_many([w["ticker"] for w in entries if w["cat"] != "etf" or w["ticker"] in ctx.state["positions"]],
                             now, ctx.finnhub)
    except Exception as e:
        print(f"Actualités indisponibles ({type(e).__name__})")
        items = {}
    hours = {"ouverture": 20, "soir": 10, "instant": 12}[kind]
    hot = hot_news(ctx, items, hours=hours, mark=(kind != "instant"))
    out.append(news_block(ctx, {t: h for t, h in hot.items() if h}, nm, F.recent(ctx, hours)))
    out.append(trend_block(snap, watch_entries(cfg)))
    ag = agenda.upcoming(ctx, entries, 2, "⏰ À surveiller sous 48 h")
    if ag:
        out.append(ag)
    out.append(R.FOOTER)
    return "\n\n".join(out)
