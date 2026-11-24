"""Tâches planifiées : ouverture (briefing après l'ouverture US), watch (alertes en séance), soir (débriefing de clôture)."""
import datetime as dt
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

from . import agenda, analysis as A, data, flash as F, markets, news as N, positions as P, render as R, state as ST, synthese, trend as T
from .config import scan_entries, watch_entries, entry_for
from .fmt import b, esc, fr_date, pct, price

PARIS = ZoneInfo("Europe/Paris")

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


def trend_changes(ctx, snap):
    """Changements de tendance de fond confirmés (watchlist + positions) depuis la dernière observation.
    La première observation d'une valeur est silencieuse. Retourne [(niveau, texte)], retournements baissiers d'abord."""
    out = []
    for w in tracked_entries(ctx):
        s = snap.get(w["ticker"])
        if not s:
            continue
        ch = T.detect_change(ctx.state, w["ticker"], T.assess(s["ind"]), ctx.now.date())
        if ch:
            out.append(T.change_text(w, *ch))
    return sorted(out, key=lambda x: x[0] != "alert")


def visible_ideas(ctx, ideas):
    """Seuls les setups validés par le backtest sont proposés (sauf swing.montrer_non_valides = true)."""
    if ctx.cfg["swing"].get("montrer_non_valides"):
        return ideas, 0
    shown = [i for i in ideas if i["validated"]]
    return shown, len(ideas) - len(shown)


def hidden_note(n):
    return (f"ℹ️ {n} setup(s) non validé(s) par le backtest masqué(s) : aucun n'a de rendement démontré après frais "
            f"(/idees tout pour les voir)." if n else None)


def hot_news(ctx, items, hours, limit=2, mark=True):
    """Titres sensibles par valeur : pertinents (le titre cite la valeur) et sans doublon d'une même histoire."""
    ent = {w["ticker"]: w for w in tracked_entries(ctx)}
    filt = ctx.cfg.get("actus", {}).get("filtrer_pertinence", True)
    return {t: N.sensitive(its, ctx.now, ctx.state, hours=hours, limit=limit, mark=mark,
                                    entry=ent.get(t) if filt else None)
            for t, its in items.items()}


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
            P.refresh(w, pos, ind)
            lines.append(P.line(w, pos, float(ind.Close.iloc[-1]), today))
            lines += [f"    {a}" for a in P.daily_advice(w, pos, ind, today, "matin")]
        out.append("\n".join(lines))

    changes = trend_changes(ctx, snap)
    if changes:
        out.append(b("🔄 Changements de tendance de fond") + "\n" + "\n".join(t for _, t in changes))

    # actualités de la nuit (titres sensibles) + résultats proches
    items = N.fetch_many([w["ticker"] for w in tracked if w["cat"] != "etf" or w["ticker"] in ctx.state["positions"]],
                         now, ctx.finnhub)
    hot = hot_news(ctx, items, hours=16)
    nl = R.news_lines({t: h for t, h in hot.items() if h}, nm)
    out.append(b("📰 Actualités sensibles de la nuit") + "\n" + ("\n".join(nl) if nl else "Rien de notable."))
    ag = agenda.upcoming(ctx, tracked, 7, "⏰ À surveiller (7 jours)")
    if ag:
        out.append(ag)

    # setups
    ideas, hidden = visible_ideas(ctx, A.find_ideas(snap, cfg, ctx.stats, ctx.state, now, exclude=set(ctx.state["positions"])))
    out.append(R.ideas_section(ideas, "🎯 Setups swing validés du jour"))
    if hidden_note(hidden):
        out.append(hidden_note(hidden))
    if not ctx.stats:
        out.append("ℹ️ Aucun backtest encore : lance « backtest » (onglet Actions) pour que les setups soient validés.")
    out.append(T.section(snap, watch_entries(cfg)))
    out.append(R.FOOTER)
    ctx.tg.send("\n\n".join(out))
    ctx.state["slots"]["matin"] = str(today)


# ------------------------------------------------------------------ ouverture
def ouverture(ctx):
    """Briefing après l'ouverture de Wall Street (≈ 15h45 Paris) : la synthèse globale, au cours actuel.
    Pas de setups swing ici : leur entrée se fait « à l'ouverture » et l'ouverture est passée ; ils restent dans le soir."""
    text = synthese.compose(ctx, "ouverture")
    if text is None:
        return                                    # slot non marqué : nouvel essai au prochain passage
    ctx.tg.send(text)
    ctx.state["slots"]["ouverture"] = str(ctx.now.date())


# ------------------------------------------------------------------ soir
def soir(ctx):
    """Débriefing de clôture, envoyé chaque jour de bourse, en deux messages : la synthèse globale (marché, positions,
    gros changements, actus, tendances, agenda), puis le détail (nouveaux setups pour demain, bilans semaine / mois)."""
    now, today, cfg = ctx.now, ctx.now.date(), ctx.cfg
    snap = A.snapshot(scan_entries(cfg), now)
    if not snap:
        warn_data(ctx, "Pas de données pour le bilan du soir")
        return                                    # slot non marqué : nouvel essai au prochain passage (jusqu'à 23h50)
    text = synthese.compose(ctx, "soir", snap=snap)
    if text is None:
        return
    ctx.state["slots"]["soir"] = str(today)
    ctx.tg.send(text)

    blocks = []
    ideas = [i for i in A.find_ideas(snap, cfg, ctx.stats, ctx.state, now, exclude=set(ctx.state["positions"]))
             if i["bar"] == today]
    ideas = [i for i in ideas if ST.fresh(ctx.state, f"idea|{i['w']['ticker']}|{i['setup']}|{i['bar']}", now, 48)]
    ideas, hidden = visible_ideas(ctx, ideas)
    if ideas:
        blocks.append(R.ideas_section(ideas, "🎯 Nouveaux setups validés à la clôture (pour demain)"))
    elif hidden_note(hidden):
        blocks.append(hidden_note(hidden))
    for unit in ("semaine", "mois"):
        if agenda.last_business_day(today, unit):
            rc = agenda.recap(snap, watch_entries(cfg), cfg, ctx.state, today, unit)
            if rc:
                blocks.append(rc)
    if blocks:
        ctx.tg.send(f"📋 {b('Détail du soir')} · {fr_date(today)}\n\n" + "\n\n".join(blocks))


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
    fl = F.block(ctx)                                    # grosses infos de marché d'abord, sans filtre sur tes valeurs
    alerts = ([fl] if fl else []) + alerts + news_alerts(ctx, tracked)
    if alerts:
        hhmm = now.astimezone(PARIS).strftime("%H:%M")
        ctx.tg.send(f"🚨 {b('Alertes')} · {hhmm}\n\n" + "\n\n".join(alerts) + "\n\n<i>/synthese : l'aperçu global à l'instant</i>")


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
    if pos["type"] in ("swing", "tendance"):
        return f"\n    ta position : {pct(p)} · stop {price(w['ticker'], pos['stop'])} ({pct(pos['stop'] / last - 1)})"
    return f"\n    ta position : {pct(p)} depuis ton achat"


def news_alerts(ctx, tracked):
    now, state = ctx.now, ctx.state
    every = ctx.cfg.get("alertes", {}).get("actus_toutes_les_min", 10)
    if every >= 30 and now.minute % 30 >= 15:       # ancien rythme : une exécution sur deux
        return []
    cands = [w for w in tracked if w["cat"] != "etf" or w["ticker"] in state["positions"]]
    items = N.fetch_many([w["ticker"] for w in cands], now, ctx.finnhub)
    hot = hot_news(ctx, items, hours=4)
    return R.news_lines({t: h for t, h in hot.items() if h}, names(ctx))[:6]


# ------------------------------------------------------------------ flash
def flash(ctx):
    """Grosses infos de marché uniquement (rapide : aucun cours, aucune donnée quotidienne)."""
    fl = F.block(ctx)
    if fl:
        ctx.tg.send(f"{fl}\n\n<i>{ctx.now.astimezone(PARIS).strftime('%H:%M')} · /synthese : l'aperçu global</i>")


# ------------------------------------------------------------------ test
def test(ctx):
    """Diagnostic : vérifie Telegram (ce message), Yahoo Finance et l'état des données."""
    raw = data.daily(["SPY", "NVDA", "ENGI.PA"], period="1mo")
    ok = ", ".join(sorted(raw)) or "aucune"
    n = len(ctx.state["positions"])
    ctx.tg.send(f"✅ {b('Test OK')} : Telegram fonctionne.\nYahoo Finance : {'✅' if raw else '❌'} (reçu : {esc(ok)}).\n"
                f"Positions suivies : {n} · backtest : {'présent (' + ctx.stats['generated'] + ')' if ctx.stats else 'pas encore lancé'}.\n"
                "/aide pour la liste des commandes.")
