"""Positions suivies : « swing » (stop, objectif, délai), « tendance » (stop suiveur, pas de délai : on garde tant que la
tendance tient) ou « long » (alerte de recul seulement).
Le bot ne passe aucun ordre : il suit tes positions et te prévient, tu agis dans ton application."""
import datetime as dt

import numpy as np

from . import setups as S, trend as T
from .fmt import b, esc, pct, price, short_date, num


def new_position(cfg, w, entry, qty, kind, setup, ind, today):
    """Crée la position avec un plan chiffré à partir du prix d'entrée réel."""
    pos = {"type": kind, "entry": entry, "qty": qty, "date": today.isoformat(), "peak": entry, "setup": setup}
    if kind == "swing":
        p = S.params(setup)
        atr_pct = float(ind.iloc[-1].atr_pct) if ind is not None and len(ind) else None
        sp = S.stop_pct(w["cat"], atr_pct, p["k"])
        pos.update(stop=entry * (1 - sp), stop0=entry * (1 - sp), risk=entry * sp,
                   target=entry * (1 + p["rr"] * sp) if p["rr"] else None,
                   sma5_exit=p["sma5_exit"], max_days=p["hold"])
    elif kind == "tendance":
        k = T.k_for(cfg, w["cat"])
        atr = float(ind.iloc[-1].atr) if ind is not None and len(ind) else None
        st = T.initial_stop(entry, atr, k)
        pos.update(stop=st, stop0=st, stop_told=st, k=k)
    return pos


def refresh(w, pos, ind):
    """Met à jour ce qui dépend de la dernière clôture (stop suiveur des positions « tendance »)."""
    return T.update_trailing(pos, ind) if pos["type"] == "tendance" and ind is not None and len(ind) else False


def days_held(pos, today):
    return int(np.busday_count(dt.date.fromisoformat(pos["date"]), today))


def days_left(pos, today):
    return pos.get("max_days", 0) - days_held(pos, today)


def pnl(pos, last):
    p = last / pos["entry"] - 1
    eur = pos["qty"] * (last - pos["entry"]) if pos.get("qty") else None
    return p, eur


def line(w, pos, last, today):
    """Ligne de statut d'une position."""
    t = w["ticker"]
    p, eur = pnl(pos, last)
    eur_s = f" ({'+' if eur >= 0 else '−'}{num(abs(eur), 0)} €)" if eur is not None else ""
    ico = "🟢" if p >= 0 else "🔴"
    head = f"{ico} {b(w['nom'])} {price(t, last)} · {pct(p)}{eur_s} depuis {price(t, pos['entry'])}"
    if pos["type"] == "swing":
        tgt = f" · objectif {price(t, pos['target'])}" if pos.get("target") else ""
        dl = days_left(pos, today)
        delai = "délai atteint" if dl <= 0 else f"{dl} séance{'s' if dl > 1 else ''} restante{'s' if dl > 1 else ''}"
        return f"{head}\n    stop {price(t, pos['stop'])} ({pct(pos['stop'] / last - 1)}){tgt} · {delai}"
    if pos["type"] == "tendance":
        return (f"{head}\n    suivi tendance · stop {price(t, pos['stop'])} ({pct(pos['stop'] / last - 1)}) · "
                f"plus haut de clôture {price(t, pos['peak'])} ({pct(last / pos['peak'] - 1)})")
    return f"{head}\n    suivi long terme · sommet {price(t, pos['peak'])} ({pct(last / pos['peak'] - 1)})"


def intraday_events(w, pos, last, today, cfg):
    """Événements à signaler en séance : [(clé, gravité, texte)]. Peut modifier pos (stop suiveur, sommet)."""
    t, ev = w["ticker"], []
    if pos["type"] != "tendance":          # tendance : le plus haut est celui des CLÔTURES (mis à jour par refresh)
        pos["peak"] = max(pos.get("peak", pos["entry"]), last)
    p, _ = pnl(pos, last)
    if pos["type"] == "swing":
        if last <= pos["stop"]:
            ev.append(("stop", 3, f"🛑 {b(w['nom'])} a touché ton stop ({price(t, pos['stop'])}) : cours {price(t, last)}, "
                                  f"{pct(p)} depuis ton achat. Vends si ce n'est pas déjà fait, c'est la discipline du swing."))
        elif pos.get("target") and last >= pos["target"]:
            ev.append(("target", 2, f"🎯 {b(w['nom'])} a atteint l'objectif ({price(t, pos['target'])}) : cours {price(t, last)}, "
                                    f"{pct(p)}. Prends tes gains (ou remonte le stop juste sous le cours)."))
        else:
            r = pos["risk"]
            if last >= pos["entry"] + 2 * r and pos["stop"] < pos["entry"] + r:
                pos["stop"] = pos["entry"] + r
                ev.append(("trail2", 1, f"🔒 {b(w['nom'])} {pct(p)} : remonte ton stop à {price(t, pos['stop'])} "
                                        f"(tu sécurises +1 risque)."))
            elif last >= pos["entry"] + r and pos["stop"] < pos["entry"]:
                pos["stop"] = pos["entry"]
                ev.append(("trail1", 1, f"🔒 {b(w['nom'])} {pct(p)} : remonte ton stop au prix d'entrée "
                                        f"({price(t, pos['stop'])}), le trade ne peut plus perdre."))
        if days_left(pos, today) <= 0:
            ev.append(("time", 2, f"⏱ {b(w['nom'])} : délai de {pos['max_days']} séances atteint ({pct(p)}). "
                                  f"Le plan prévoit de sortir aujourd'hui."))
    elif pos["type"] == "tendance":
        if last <= pos["stop"]:
            ev.append(("stop", 3, f"🛑 {b(w['nom'])} touche ton stop de tendance ({price(t, pos['stop'])}) en séance : cours "
                                  f"{price(t, last)}, {pct(p)} depuis ton achat. Un stop de tendance se juge surtout à la clôture : "
                                  f"le bilan du soir te dira s'il est confirmé. À toi de décider."))
    else:
        lt = cfg["long_terme"]
        if p <= -lt["perte_depuis_achat"][w["cat"]]:
            ev.append(("loss", 2, f"📉 {b(w['nom'])} est à {pct(p)} depuis ton achat (seuil {pct(-lt['perte_depuis_achat'][w['cat']], 0)})."))
        dd = last / pos["peak"] - 1
        if dd <= -lt["recul_depuis_sommet"][w["cat"]]:
            ev.append(("peak", 2, f"📉 {b(w['nom'])} recule de {pct(dd)} depuis son sommet depuis ton achat : à toi de voir si tu sécurises."))
    return ev


def daily_advice(w, pos, ind, today, when):
    """Conseil à partir de la dernière clôture. when = 'matin' (agir à l'ouverture) ou 'soir' (agir demain)."""
    last = ind.iloc[-1]
    c = float(last.Close)
    ev = []
    qui = "aujourd'hui à l'ouverture" if when == "matin" else "demain à l'ouverture"
    if pos["type"] == "tendance":
        refresh(w, pos, ind)
        if c <= pos["stop"]:
            ev.append(f"🛑 clôture sous ton stop de tendance ({price(w['ticker'], pos['stop'])}) : la tendance de fond est "
                      f"cassée selon ta règle. Sors {qui} si tu la suis")
        elif pos["stop"] >= pos.get("stop_told", 0) * 1.02:
            pos["stop_told"] = pos["stop"]
            ev.append(f"🔒 remonte ton stop à {price(w['ticker'], pos['stop'])} dans l'app "
                      f"({pct(pos['stop'] / pos['entry'] - 1)} par rapport à ton achat)")
        return ev
    if pos["type"] != "swing":
        return ev
    dl = days_left(pos, today)
    if c <= pos["stop"]:
        ev.append(f"🛑 clôture sous ton stop : sors {qui} si ce n'est pas fait")
    elif pos.get("target") and c >= pos["target"]:
        ev.append(f"🎯 objectif atteint à la clôture : prends tes gains {qui}")
    elif pos.get("sma5_exit") and c > float(last.sma5) and ind.index[-1].date() >= dt.date.fromisoformat(pos["date"]):
        ev.append(f"✅ retour au-dessus de la moyenne 5 j ({price(w['ticker'], float(last.sma5))}) : signal de sortie, {qui}")
    elif dl <= (0 if when == "matin" else 1):
        ev.append(f"⏱ délai atteint : sors {qui}")
    return ev


def close_position(state, w, pos, exit_px, today):
    p, eur = pnl(pos, exit_px)
    rec = {"ticker": w["ticker"], "nom": w["nom"], "type": pos["type"], "setup": pos.get("setup"),
           "entry": pos["entry"], "exit": exit_px, "pct": p, "eur": eur, "date_in": pos["date"],
           "date_out": today.isoformat(), "days": days_held(pos, today)}
    state["journal"].append(rec)
    return rec
