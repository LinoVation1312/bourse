"""Commandes Telegram : /idees /avis /achat /stop /vente /statut /bilan /aide."""
import datetime as dt

from . import analysis as A, data, positions as P, render as R, setups as S
from .config import resolve, scan_entries, watch_entries, entry_for
from .fmt import b, esc, num, pct, price, short_date


def parse_number(tok):
    try:
        return float(tok.replace(",", ".").replace("€", "").replace("$", ""))
    except ValueError:
        return None


def parse_args(tokens):
    """['nvidia', '142,5', '10', 'pullback'] -> (nom, prix, quantité, mot-clé)."""
    name, nums, word = None, [], None
    for tok in tokens:
        n = parse_number(tok)
        if n is not None:
            nums.append(n)
        elif tok.lower() in S.SETUPS or tok.lower() in ("long", "swing"):
            word = tok.lower()
        elif name is None:
            name = tok
    return name, (nums[0] if nums else None), (nums[1] if len(nums) > 1 else None), word


def handle(ctx, text):
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return
    cmd, args = parts[0].split("@")[0].lower(), parts[1:]
    fn = {"/idees": idees, "/idées": idees, "/avis": avis, "/achat": achat, "/stop": stop, "/vente": vente,
          "/statut": statut, "/positions": statut, "/bilan": bilan}.get(cmd)
    if cmd in ("/start", "/aide", "/help") or fn is None:
        ctx.tg.send(R.help_text())
        return
    try:
        fn(ctx, args)
    except Exception as e:                                          # une commande ratée ne doit pas bloquer le reste
        ctx.tg.send(f"⚠️ Je n'ai pas pu exécuter {esc(cmd)} ({esc(type(e).__name__)}: {esc(str(e)[:120])}). /aide pour la syntaxe.")


def _need_ticker(ctx, args):
    name = args[0] if args else None
    w = resolve(ctx.cfg, name) if name else None
    if not w:
        ctx.tg.send(f"Je ne connais pas « {esc(name or '')} ». Essaie le nom court (nvidia) ou le ticker Yahoo (NVDA).")
    return w


def idees(ctx, args):
    snap = A.snapshot(scan_entries(ctx.cfg), ctx.now)
    held = set(ctx.state["positions"])
    ideas = A.find_ideas(snap, ctx.cfg, ctx.stats, ctx.state, ctx.now, exclude=held)
    ctx.tg.send(R.ideas_section(ideas) + f"\n\n{len(snap)} valeurs analysées.")


def avis(ctx, args):
    w = _need_ticker(ctx, args)
    if not w:
        return
    t = w["ticker"]
    snap = A.snapshot([w], ctx.now)
    if t not in snap:
        ctx.tg.send(f"Pas de données récentes pour {esc(t)}.")
        return
    ind = snap[t]["ind"]
    r = ind.iloc[-1]
    tr = A.trend(ind)[1]
    out = [f"{b(w['nom'])} ({esc(t)}) · {price(t, float(r.Close))} (clôture du {short_date(ind.index[-1].date())})",
           f"Tendance de fond : {tr} · RSI 14 j {float(r.rsi14):.0f} · volatilité {float(r.vol_ann) * 100:.0f} %/an · "
           f"1 j {pct(float(ind.Close.pct_change().iloc[-1]))} · 5 j {pct(float(r.ret5))} · 20 j {pct(float(r.ret20))}"]
    act = S.active(ind)
    if act:
        out.append(R.ideas_section([{"w": w, "setup": n, "plan": S.trade_plan(ind, w["cat"], n, ctx.cfg),
                                      "stat": A.stat_for(ctx.stats, n, w["cat"]), "tstat": A.ticker_stat(ctx.stats, t, n),
                                      "validated": bool((A.stat_for(ctx.stats, n, w["cat"]) or {}).get("validated")),
                                      "bad": False, "bar": ind.index[-1].date(), "rsi2": float(r.rsi2),
                                      "trend": A.trend(ind)[0]} for n in act], "🎯 Setups actifs"))
    else:
        out.append("🎯 Aucun setup swing actif aujourd'hui sur cette valeur.")
    hist = [f"{S.SETUPS[n]['nom']} : {s['n']} trades, {s['win'] * 100:.0f} % gagnants, {pct(s['mean'], 2)}/trade"
            for n in S.SETUPS if (s := A.ticker_stat(ctx.stats, t, n)) and s["n"] >= 5]
    if hist:
        out.append(b("Historique sur cette valeur (frais inclus, échantillon petit)") + "\n" + "\n".join(hist))
    if w["cat"] != "etf":
        e = A.earnings_in_days(ctx.state, t, ctx.now)
        if e is not None:
            out.append(f"📅 Résultats dans {e} j")
    pos = ctx.state["positions"].get(t)
    if pos:
        out.append(P.line(w, pos, float(r.Close), ctx.now.date()))
    ctx.tg.send("\n\n".join(out))


def achat(ctx, args):
    name, px, qty, word = parse_args(args)
    w = _need_ticker(ctx, [name] if name else [])
    if not w:
        return
    t = w["ticker"]
    kind = "long" if word == "long" else "swing"
    setup = word if word in S.SETUPS else None
    snap = A.snapshot([w], ctx.now)
    ind = snap[t]["ind"] if t in snap else None
    if px is None:
        px = data.last_price(t, ctx.now)
        if px is None:
            ctx.tg.send(f"Pas de cours pour {esc(t)} : donne ton prix, ex. /achat {esc(args[0])} 142,5")
            return
        ref = " (cours Yahoo du moment)"
    else:
        ref = ""
    pos = P.new_position(ctx.cfg, w, px, qty, kind, setup, ind, ctx.now.date())
    ctx.state["positions"][t] = pos
    msg = [f"📌 Je suis ta position {b(w['nom'])} à {price(t, px)}{ref}"
           + (f", {num(qty, 2)} titres" if qty else "") + "."]
    if kind == "swing":
        p = S.params(setup)
        stop_line = f"Stop {price(t, pos['stop'])} ({pct(pos['stop'] / px - 1)}) : pose-le dans l'app"
        sortie = [f"objectif {price(t, pos['target'])}"] if pos.get("target") else []
        if pos["sma5_exit"]:
            sortie.append("clôture au-dessus de sa moyenne 5 j")
        sortie.append(f"délai {pos['max_days']} séances (≈ {short_date(S.deadline(ctx.now.date(), pos['max_days']))})")
        msg.append(stop_line + "\nSortie : " + ", ou ".join(sortie))
        msg.append("Je te préviens si le stop ou l'objectif est touché, quand remonter ton stop, et à l'approche du délai.")
        if qty is None:
            msg.append("(Ajoute la quantité pour que je calcule ton gain en €.)")
    else:
        lt = ctx.cfg["long_terme"]
        msg.append(f"Suivi long terme : alerte si {pct(-lt['perte_depuis_achat'][w['cat']], 0)} depuis ton achat "
                   f"ou {pct(-lt['recul_depuis_sommet'][w['cat']], 0)} depuis le sommet.")
    ctx.tg.send("\n".join(msg))


def stop(ctx, args):
    name, px, _, _ = parse_args(args)
    w = _need_ticker(ctx, [name] if name else [])
    if not w:
        return
    pos = ctx.state["positions"].get(w["ticker"])
    if not pos or pos["type"] != "swing" or px is None:
        ctx.tg.send("Syntaxe : /stop nvidia 138,5 (pour une position swing suivie).")
        return
    pos["stop"] = px
    ctx.tg.send(f"🔧 Stop de {b(w['nom'])} suivi à {price(w['ticker'], px)}.")


def vente(ctx, args):
    name, px, _, _ = parse_args(args)
    w = _need_ticker(ctx, [name] if name else [])
    if not w:
        return
    pos = ctx.state["positions"].pop(w["ticker"], None)
    if not pos:
        ctx.tg.send(f"Aucune position « {esc(w['nom'])} » suivie.")
        return
    exit_px = px if px is not None else data.last_price(w["ticker"], ctx.now) or pos["entry"]
    rec = P.close_position(ctx.state, w, pos, exit_px, ctx.now.date())
    eur = f" ({'+' if rec['eur'] >= 0 else '−'}{num(abs(rec['eur']), 0)} €)" if rec["eur"] is not None else ""
    ctx.tg.send(f"✅ {b(w['nom'])} clôturée à {price(w['ticker'], exit_px)} : {pct(rec['pct'])}{eur} en {rec['days']} séances. "
                f"/bilan pour le cumul.")


def statut(ctx, args):
    today = ctx.now.date()
    out = []
    if ctx.state["positions"]:
        entries = [entry_for(ctx.cfg, t) for t in ctx.state["positions"]]
        snap = A.snapshot(entries, ctx.now)
        lines = [b("💼 Positions suivies")]
        for w in entries:
            pos = ctx.state["positions"][w["ticker"]]
            last = data.last_price(w["ticker"], ctx.now) or (float(snap[w["ticker"]]["ind"].Close.iloc[-1]) if w["ticker"] in snap else pos["entry"])
            lines.append(P.line(w, pos, last, today))
        out.append("\n".join(lines))
    else:
        out.append("💼 Aucune position suivie (/achat nvidia 142,5 10).")
    snap = A.snapshot(watch_entries(ctx.cfg), ctx.now)
    out.append(R.trend_section(snap, [w["ticker"] for w in watch_entries(ctx.cfg)]))
    ctx.tg.send("\n\n".join(out))


def bilan(ctx, args):
    j = ctx.state["journal"]
    if not j:
        ctx.tg.send("Aucun trade clôturé pour l'instant (/vente nvidia).")
        return
    swing = [x for x in j if x["type"] == "swing"] or j
    wins = [x for x in swing if x["pct"] > 0]
    eur = sum(x["eur"] for x in swing if x["eur"] is not None)
    avg = sum(x["pct"] for x in swing) / len(swing)
    last = "\n".join(f"{'🟢' if x['pct'] > 0 else '🔴'} {esc(x['nom'])} {pct(x['pct'])} en {x['days']} séances" for x in j[-8:])
    ctx.tg.send(f"{b('📒 Bilan swing')}\n{len(swing)} trades · {len(wins) * 100 // len(swing)} % gagnants · "
                f"{pct(avg, 1)} par trade en moyenne · {'+' if eur >= 0 else '−'}{num(abs(eur), 0)} € cumulés\n\n"
                f"Derniers trades :\n{last}")
