"""Les trois tâches planifiées : matin (7h45), watch (toutes les 15 min en séance), soir (22h15)."""
import datetime as dt
from dataclasses import dataclass, field

from . import analysis as A, data, markets, news as N, positions as P, render as R, state as ST
from .config import scan_entries, watch_entries, entry_for
from .fmt import b, esc, fr_date, pct, price

# durée avant de renvoyer le même événement de position (heures)
COOLDOWN = {"stop": 3, "target": 6, "time": 20, "trail1": 999, "trail2": 999, "loss": 24, "peak": 24}


@dataclass
class Ctx:
    cfg: dict
    state: dict
    stats: dict
    tg: object
    now: dt.datetime
    finnhub: str = None
    names: dict = field(default_factory=dict)


def tracked_entries(ctx):
    """Watchlist + positions suivies (même hors watchlist)."""
    seen = {w["ticker"]: w for w in watch_entries(ctx.cfg)}
    for t in ctx.state["positions"]:
        seen.setdefault(t, entry_for(ctx.cfg, t))
    return list(seen.values())


def names(ctx):
    return {w["ticker"]: w["nom"] for w in tracked_entries(ctx)}


def warn_data(ctx, what):
    if ST.fresh(ctx.state, "warn|data", ctx.now, 20):
        ctx.tg.send(f"⚠️ {esc(what)}. Yahoo Finance est parfois indisponible depuis les serveurs GitHub : je réessaie au prochain passage.")


# ------------------------------------------------------------------ matin
def matin(ctx):
    now, today, cfg = ctx.now, ctx.now.date(), ctx.cfg
    entries = scan_entries(cfg)
    snap = A.snapshot(entries, now)
    if len(snap) < max(3, len(entries) // 3):
        warn_data(ctx, f"Données incomplètes ce matin ({len(snap)}/{len(entries)} valeurs)")
        if not snap:
            return
    tracked = tracked_entries(ctx)
    nm = names(ctx)
    out = [f"☀️ {b('Pré-ouverture')} · {fr_date(today)}"]
    rl = R.regime_line(A.regime(now))
    if rl:
        out.append(rl)

    # positions
    if ctx.state["positions"]:
        lines = [b("💼 Tes positions")]
        for t, pos in ctx.state["positions"].items():
            w = entry_for(cfg, t)
            if t not in snap:
                lines.append(f"{esc(w['nom'])} : pas de données")
                continue
            ind = snap[t]["ind"]
            lines.append(P.line(w, pos, float(ind.Close.iloc[-1]), today))
            lines += [f"    {a}" for a in P.daily_advice(w, pos, ind, today, "matin")]
        out.append("\n".join(lines))

    # actualités de la nuit (titres sensibles) + résultats proches
    items = N.fetch_many([w["ticker"] for w in tracked if w["cat"] != "etf" or w["ticker"] in ctx.state["positions"]],
                         now, ctx.finnhub)
    hot = {t: N.sensitive(its, now, ctx.state, hours=16, limit=2) for t, its in items.items()}
    nl = R.news_lines({t: h for t, h in hot.items() if h}, nm)
    out.append(b("📰 Actualités sensibles de la nuit") + "\n" + ("\n".join(nl) if nl else "Rien de notable."))
    earn = []
    for w in tracked:
        if w["cat"] == "etf":
            continue
        e = A.earnings_in_days(ctx.state, w["ticker"], now)
        if e is not None and e <= 7:
            earn.append(f"{esc(w['nom'])} : {'aujourd’hui' if e == 0 else f'dans {e} j'}"
                        + (" ⚠ tu détiens cette valeur : gap possible" if w["ticker"] in ctx.state["positions"] else ""))
    if earn:
        out.append(b("📅 Résultats (7 jours)") + "\n" + "\n".join(earn))

    # setups
    ideas = A.find_ideas(snap, cfg, ctx.stats, ctx.state, now, exclude=set(ctx.state["positions"]))
    out.append(R.ideas_section(ideas))
    if not ctx.stats:
        out.append("ℹ️ Aucun backtest encore : lance « backtest » (onglet Actions) pour que les setups soient validés.")
    out.append(R.trend_section(snap, [w["ticker"] for w in watch_entries(cfg)]))
    out.append(R.FOOTER)
    ctx.tg.send("\n\n".join(out))
    ctx.state["slots"]["matin"] = str(today)


# ------------------------------------------------------------------ soir
def soir(ctx):
    now, today, cfg = ctx.now, ctx.now.date(), ctx.cfg
    snap = A.snapshot(scan_entries(cfg), now)
    ctx.state["slots"]["soir"] = str(today)
    if not snap:
        warn_data(ctx, "Pas de données pour le bilan du soir")
        return
    blocks = []
    pos_lines = []
    for t, pos in ctx.state["positions"].items():
        w = entry_for(cfg, t)
        if t not in snap:
            continue
        adv = P.daily_advice(w, pos, snap[t]["ind"], today, "soir")
        if adv:
            pos_lines.append(P.line(w, pos, float(snap[t]["ind"].Close.iloc[-1]), today) + "\n" + "\n".join(f"    {a}" for a in adv))
    if pos_lines:
        blocks.append(b("💼 À faire demain sur tes positions") + "\n" + "\n".join(pos_lines))
    ideas = [i for i in A.find_ideas(snap, cfg, ctx.stats, ctx.state, now, exclude=set(ctx.state["positions"]))
             if i["bar"] == today]
    ideas = [i for i in ideas if ST.fresh(ctx.state, f"idea|{i['w']['ticker']}|{i['setup']}|{i['bar']}", now, 48)]
    if ideas:
        blocks.append(R.ideas_section(ideas, "🎯 Nouveaux setups à la clôture (pour demain)"))
    if blocks:
        ctx.tg.send(f"🌙 {b('Clôture')} · {fr_date(today)}\n\n" + "\n\n".join(blocks) + "\n\n" + R.FOOTER)


# ------------------------------------------------------------------ watch
def watch(ctx):
    now, today, cfg, state = ctx.now, ctx.now.date(), ctx.cfg, ctx.state
    tracked = tracked_entries(ctx)
    alerts = []
    live = [w for w in tracked if markets.is_open(w["ticker"], now)]
    if live:
        raw = data.intraday([w["ticker"] for w in live])
        if not raw:
            warn_data(ctx, "Aucun cours reçu en séance")
        for w in live:
            t, cat = w["ticker"], w["cat"]
            st = data.live_stats(raw.get(t), t, now)
            if st is None or st["age_min"] > 45:
                continue
            alerts += market_alerts(ctx, w, st, today)
            pos = state["positions"].get(t)
            if pos:
                for kind, _, text in P.intraday_events(w, pos, st["price"], today, cfg):
                    key = f"pos|{t}|{pos['date']}|{kind}"
                    if ST.fresh(state, key, now, COOLDOWN.get(kind, 24)):
                        alerts.append(text)
    alerts += news_alerts(ctx, tracked)
    if alerts:
        ctx.tg.send("\n\n".join(alerts))


def market_alerts(ctx, w, st, today):
    t, cat, state, cfg = w["ticker"], w["cat"], ctx.state, ctx.cfg
    out = []
    thr = cfg["alertes"]["jour"][cat]
    lvl = int(abs(st["day"]) / thr)
    if lvl >= 1:
        key = f"day|{today}|{t}|{'+' if st['day'] > 0 else '-'}"
        if lvl > state["levels"].get(key, 0):
            state["levels"][key] = lvl
            out.append(f"⚡ {b(w['nom'])} {pct(st['day'])} sur la journée · {price(t, st['price'])}" + held_note(ctx, w, st["price"]))
    fl = cfg["alertes"]["flash"][cat]
    if st["flash"] is not None and abs(st["flash"]) >= fl and ST.fresh(state, f"flash|{t}", ctx.now, 2):
        out.append(f"🚨 {b(w['nom'])} {pct(st['flash'])} en 1 h · {price(t, st['price'])}, mouvement brutal" + held_note(ctx, w, st["price"]))
    return out


def held_note(ctx, w, last):
    pos = ctx.state["positions"].get(w["ticker"])
    if not pos:
        return ""
    p = last / pos["entry"] - 1
    if pos["type"] == "swing":
        return f"\n    ta position : {pct(p)} · stop {price(w['ticker'], pos['stop'])} ({pct(pos['stop'] / last - 1)})"
    return f"\n    ta position : {pct(p)} depuis ton achat"


def news_alerts(ctx, tracked):
    now, state = ctx.now, ctx.state
    if now.minute % 30 >= 15:       # une exécution sur deux (≈ toutes les 30 min), sans rien stocker
        return []
    cands = [w for w in tracked if w["cat"] != "etf" or w["ticker"] in state["positions"]]
    items = N.fetch_many([w["ticker"] for w in cands], now, ctx.finnhub)
    hot = {t: N.sensitive(its, now, state, hours=4, limit=2) for t, its in items.items()}
    return R.news_lines({t: h for t, h in hot.items() if h}, names(ctx))[:6]


# ------------------------------------------------------------------ test
def test(ctx):
    """Diagnostic : vérifie Telegram (ce message), Yahoo Finance et l'état des données."""
    raw = data.daily(["SPY", "NVDA", "ENGI.PA"], period="1mo")
    ok = ", ".join(sorted(raw)) or "aucune"
    n = len(ctx.state["positions"])
    ctx.tg.send(f"✅ {b('Test OK')} : Telegram fonctionne.\nYahoo Finance : {'✅' if raw else '❌'} (reçu : {esc(ok)}).\n"
                f"Positions suivies : {n} · backtest : {'présent (' + ctx.stats['generated'] + ')' if ctx.stats else 'pas encore lancé'}.\n"
                "/aide pour la liste des commandes.")
