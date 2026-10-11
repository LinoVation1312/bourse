"""Horaires des places boursières (approximatifs : jours fériés non gérés)."""
import datetime as dt
from zoneinfo import ZoneInfo

SESSIONS = {
    "US":  ("America/New_York", dt.time(9, 30), dt.time(16, 0)),
    "EU":  ("Europe/Paris",     dt.time(9, 0),  dt.time(17, 30)),
    "PL":  ("Europe/Warsaw",    dt.time(9, 0),  dt.time(17, 0)),
    "HK":  ("Asia/Hong_Kong",   dt.time(9, 30), dt.time(16, 0)),
    "CA":  ("America/Toronto",  dt.time(9, 30), dt.time(16, 0)),
    "UK":  ("Europe/London",    dt.time(8, 0),  dt.time(16, 30)),
    "FUT": ("America/New_York", dt.time(0, 0),  dt.time(23, 59)),
}
EU_SUFFIX = (".PA", ".AS", ".DE", ".MI", ".MC", ".BR", ".LS", ".F", ".VI", ".HE")
INDEX_MARKET = {"^FCHI": "EU", "^STOXX50E": "EU", "^GDAXI": "EU", "^HSI": "HK"}


def market(ticker):
    t = ticker.upper()
    if t.endswith("=F"):
        return "FUT"
    if t.startswith("^"):
        return INDEX_MARKET.get(t, "US")
    if t.endswith(".WA"):
        return "PL"
    if t.endswith(".HK"):
        return "HK"
    if t.endswith((".TO", ".V")):
        return "CA"
    if t.endswith(".L"):
        return "UK"
    if t.endswith(EU_SUFFIX):
        return "EU"
    return "US"


def tz(ticker):
    return ZoneInfo(SESSIONS[market(ticker)][0])


def local_now(ticker, now):
    return now.astimezone(tz(ticker))


def is_open(ticker, now):
    _, o, c = SESSIONS[market(ticker)]
    loc = local_now(ticker, now)
    return loc.weekday() < 5 and o <= loc.time() < c


def close_time(ticker):
    return SESSIONS[market(ticker)][2]
