"""Mise en forme des messages (HTML Telegram, nombres à la française)."""
import html
import datetime as dt

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
CCY = {"US": "$", "EU": "€", "PL": "zł", "HK": "HK$", "CA": "$", "UK": "p", "FUT": "$"}


def esc(s):
    return html.escape(str(s), quote=False)


def b(s):
    return f"<b>{esc(s)}</b>"


def num(x, d=2):
    return f"{x:,.{d}f}".replace(",", "\u202f").replace(".", ",")


def pct(x, d=None, signed=True):
    if d is None:
        d = 1 if abs(x) < 0.10 else 0
    s = f"{x * 100:+.{d}f}" if signed else f"{x * 100:.{d}f}"
    return s.replace(".", ",").replace("-", "−") + " %"


def price(ticker, x):
    from .markets import market
    return f"{num(x)} {CCY.get(market(ticker), '')}".strip()


def fr_date(d):
    if isinstance(d, str):
        d = dt.date.fromisoformat(d)
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def short_date(d):
    if isinstance(d, str):
        d = dt.date.fromisoformat(d)
    return f"{d.day} {MOIS[d.month - 1]}"
