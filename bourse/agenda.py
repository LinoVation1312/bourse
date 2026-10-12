"""« Attention X demain » et bilans de période.

  upcoming()  : événements à venir sur tes valeurs (résultats, détachement de dividende, événements saisis dans config.json)
  unusual()   : mouvements inhabituels de la séance (volume anormal, gap à l'ouverture)
  recap()     : grands changements de la semaine / du mois (variations, plus hauts/bas, changements de tendance)
Tout est factuel : aucun de ces éléments ne prédit le cours, ils te disent où regarder.
"""
import datetime as dt

import numpy as np
import pandas as pd

from . import analysis as A
from .fmt import b, esc, pct, short_date

CACHE_DAYS = 3
VOL_ALERT = 3.0                       # volume ≥ 3 × la moyenne des 20 derniers jours
RECAP_DEFAULT = {"semaine": {"etf": 0.04, "grande": 0.08, "pari": 0.15},
                 "mois": {"etf": 0.08, "grande": 0.15, "pari": 0.30}}


# ------------------------------------------------------------------ à venir
def dividend_in_days(state, ticker, now):
    """Jours avant le prochain détachement de dividende (None si inconnu). Mis en cache 3 jours."""
    store = state.setdefault("div", {})
    c, today = store.get(ticker), now.date()
    if not c or (today - dt.date.fromisoformat(c["fetched"])).days >= CACHE_DAYS:
        d = None
        try:
            import yfinance as yf
            cal = yf.Ticker(ticker).calendar
            v = cal.get("Ex-Dividend Date") if isinstance(cal, dict) else None
            if v:
                d = str(pd.Timestamp(v[0] if isinstance(v, (list, tuple)) else v).date())
        except Exception:
            pass
        c = store[ticker] = {"fetched": str(today), "date": d}
    if not c["date"]:
        return None
    n = (dt.date.fromisoformat(c["date"]) - today).days
    return n if n >= 0 else None


def when(n, today):
    if n == 0:
        return "aujourd’hui"
    if n == 1:
        return "demain"
    return f"dans {n} j ({short_date(today + dt.timedelta(days=n))})"


def upcoming(ctx, tracked, max_days, titre):
    """Section « à surveiller » : [(jours, texte)] triés, ou None s'il n'y a rien."""
    today, held = ctx.now.date(), ctx.state["positions"]
    rows = []
    if ctx.cfg.get("alertes_univers", True):                       # valeurs de l'univers : prévenues aussi, même sans position
        seen = {w["ticker"] for w in tracked}
        tracked = list(tracked) + [w for w in ctx.cfg.get("univers", []) if w["ticker"] not in seen]
    for w in tracked:
        t, nom = w["ticker"], esc(w["nom"])
        note = " ⚠ tu détiens cette valeur" if t in held else ""
        if w["cat"] != "etf":
            e = A.earnings_in_days(ctx.state, t, ctx.now)
            if e is not None and e <= max_days:
                rows.append((e, f"📊 {b(w['nom'])} : résultats {when(e, today)} : forte variation fréquente, gap possible{note}"))
        d = dividend_in_days(ctx.state, t, ctx.now)
        if d is not None and d <= min(max_days, 3):
            rows.append((d, f"💶 {b(w['nom'])} : détachement du dividende {when(d, today)} : le cours baisse d'autant, ce n'est pas une vraie perte"))
    for ev in ctx.cfg.get("evenements", []):
        try:
            n = (dt.date.fromisoformat(ev["date"]) - today).days
        except (KeyError, ValueError):
            continue
        if 0 <= n <= max_days:
            who = next((w for w in tracked if w["ticker"] == ev.get("ticker")), None)
            head = b(who["nom"]) if who else "Marché"
            rows.append((n, f"📌 {head} : {esc(ev.get('texte', 'événement'))} {when(n, today)}"
                         + (note if who and who["ticker"] in held else "")))
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    return b(titre) + "\n" + "\n".join(t for _, t in rows)


# ------------------------------------------------------------------ séance inhabituelle
def unusual(snap, entries, cfg, today):
    """Volume anormal ou gap à l'ouverture sur la dernière séance (valeurs de la watchlist uniquement)."""
    lines = []
    for w in entries:
        s = snap.get(w["ticker"])
        if not s:
            continue
        ind = s["ind"]
        if ind.index[-1].date() != today or len(ind) < 25:
            continue
        r, prev = ind.iloc[-1], float(ind.Close.iloc[-2])
        bits = []
        vr = float(r.vol_rel) if np.isfinite(r.vol_rel) else 0.0
        if vr >= VOL_ALERT:
            bits.append(f"volume ×{vr:.1f} la normale")
        gap = float(r.Open) / prev - 1
        if abs(gap) >= cfg["alertes"]["flash"][w["cat"]]:
            bits.append(f"ouverture en {'hausse' if gap > 0 else 'baisse'} de {pct(abs(gap), 1, False)} (gap)")
        if bits:
            day = float(r.Close) / prev - 1
            lines.append((-vr, f"👀 {b(w['nom'])} : {', '.join(bits)} · séance {pct(day)}"))
    if not lines:
        return None
    return b("👀 Séance inhabituelle") + "\n" + "\n".join(t for _, t in sorted(lines))


# ------------------------------------------------------------------ bilans semaine / mois
def last_business_day(today, unit):
    """Vrai si `today` est la dernière séance (hors jours fériés) de la semaine ou du mois."""
    if today.weekday() >= 5:
        return False
    nxt = pd.Timestamp(np.busday_offset(np.datetime64(today), 1, roll="forward")).date()
    if unit == "semaine":
        return nxt.isocalendar()[:2] != today.isocalendar()[:2]
    return (nxt.year, nxt.month) != (today.year, today.month)


def period_start(today, unit):
    return today - dt.timedelta(days=today.weekday()) if unit == "semaine" else today.replace(day=1)


def recap(snap, entries, cfg, state, today, unit):
    """Bilan de la semaine ou du mois : variation de chaque valeur depuis la clôture précédant la période."""
    start = period_start(today, unit)
    thr = {**RECAP_DEFAULT, **cfg.get("recap", {})}[unit]
    rows, extremes = [], []
    for w in entries:
        s = snap.get(w["ticker"])
        if not s:
            continue
        c = s["ind"].Close.astype(float)
        before = c[c.index < pd.Timestamp(start)]
        if len(before) == 0 or c.index[-1].date() < start:
            continue
        ret = float(c.iloc[-1] / before.iloc[-1] - 1)
        rows.append((ret, w))
        yr = c.tail(252)
        if len(yr) >= 120:
            if c.iloc[-1] >= yr.max() * 0.999:
                extremes.append(f"📈 {b(w['nom'])} est à son plus haut sur 12 mois")
            elif c.iloc[-1] <= yr.min() * 1.001:
                extremes.append(f"📉 {b(w['nom'])} est à son plus bas sur 12 mois")
    if not rows:
        return None
    rows.sort(key=lambda r: r[0], reverse=True)
    label = "de la semaine" if unit == "semaine" else "du mois"
    out = [b(f"🗓 Bilan {label}") + f"\n<i>depuis la clôture du {short_date(start - dt.timedelta(days=1))}</i>"]
    lines = []
    for ret, w in rows:
        big = abs(ret) >= thr[w["cat"]]
        lines.append(f"{'🔥' if big and ret > 0 else '⚠️' if big else '•'} {esc(w['nom'])} {pct(ret)}")
    out.append("\n".join(lines))
    changes = []
    for w in entries:
        tr = (state.get("trend") or {}).get(w["ticker"])
        if tr and tr.get("since") and tr["since"] >= str(start) and w["ticker"] in snap:
            from .trend import LABEL
            changes.append(f"{LABEL[tr['state']][0]} {esc(w['nom'])} : tendance de fond désormais {LABEL[tr['state']][1]}")
    if changes:
        out.append(b("🔄 Changements de tendance sur la période") + "\n" + "\n".join(changes))
    if extremes:
        out.append("\n".join(extremes))
    out.append(f"<i>🔥 / ⚠️ : variation supérieure à {pct(thr['etf'], 0, False)} (ETF), {pct(thr['grande'], 0, False)} (grandes valeurs), "
               f"{pct(thr['pari'], 0, False)} (paris).</i>")
    return "\n\n".join(out)
