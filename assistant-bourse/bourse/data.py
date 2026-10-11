"""Accès aux cours (Yahoo Finance via yfinance). Tout est regroupé en un seul appel par lot, avec nouvelles tentatives."""
import time
import datetime as dt

import pandas as pd

from . import markets

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _clean(df, intraday=False):
    if df is None or len(df) == 0:
        return None
    df = df[[c for c in OHLCV if c in df.columns]].dropna(subset=["Close"])
    if df.empty or "Open" not in df.columns:
        return None
    df = df[~df.index.duplicated(keep="last")].sort_index()
    idx = pd.to_datetime(df.index)
    if intraday:
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    elif idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    return df


def _split(raw, tickers, intraday):
    out = {}
    if raw is None or len(raw) == 0:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        l0, l1 = set(raw.columns.get_level_values(0)), set(raw.columns.get_level_values(1))
        for t in tickers:
            if t in l0:
                sub = raw[t]
            elif t in l1:
                sub = raw.xs(t, axis=1, level=1)
            else:
                continue
            c = _clean(sub, intraday)
            if c is not None:
                out[t] = c
    elif len(tickers) == 1:
        c = _clean(raw, intraday)
        if c is not None:
            out[tickers[0]] = c
    return out


def _download(tickers, retries=3, **kw):
    import yfinance as yf
    tickers = list(dict.fromkeys(tickers))
    got, todo = {}, list(tickers)
    intraday = kw.get("interval", "1d") != "1d"
    for attempt in range(retries):
        if not todo:
            break
        try:
            raw = yf.download(todo, auto_adjust=True, group_by="ticker", threads=True, progress=False, **kw)
            got.update(_split(raw, todo, intraday))
        except Exception:
            pass
        todo = [t for t in tickers if t not in got]
        if todo and attempt < retries - 1:
            time.sleep(2 + 3 * attempt)
    return got


def daily(tickers, period="2y"):
    """{ticker: DataFrame OHLCV journalier}. Les tickers sans données sont absents."""
    return _download(tickers, period=period, interval="1d")


def intraday(tickers, period="5d", interval="5m"):
    """{ticker: DataFrame OHLCV intrajournalier, index en UTC}."""
    return _download(tickers, retries=2, period=period, interval=interval)


def completed(df, ticker, now):
    """Retire la séance du jour si elle n'est peut-être pas terminée (clôture + 20 min de marge)."""
    if df is None or df.empty:
        return df
    loc = markets.local_now(ticker, now)
    if df.index[-1].date() == loc.date():
        end = dt.datetime.combine(loc.date(), markets.close_time(ticker)) + dt.timedelta(minutes=20)
        if markets.market(ticker) == "FUT" or loc.replace(tzinfo=None) < end:
            return df.iloc[:-1]
    return df


def live_stats(df, ticker, now):
    """Prix actuel, variation sur la journée et sur 1 h, à partir de barres de 5 min (index UTC)."""
    if df is None or df.empty:
        return None
    tz = markets.tz(ticker)
    loc = df.index.tz_convert(tz)
    today = now.astimezone(tz).date()
    is_today = pd.Series(loc.date == today, index=df.index)
    if not is_today.any():
        return None
    before = df.Close[~is_today]
    if before.empty:
        return None
    last_ts = df.index[-1]
    price = float(df.Close.iloc[-1])
    prev = float(before.iloc[-1])
    age = (now - last_ts.to_pydatetime()).total_seconds() / 60
    day = df[is_today]
    past = day[day.index <= last_ts - pd.Timedelta(minutes=60)]
    flash = price / float(past.Close.iloc[-1]) - 1 if len(past) else None
    return {"price": price, "prev": prev, "day": price / prev - 1, "flash": flash, "age_min": age}


def last_price(ticker, now):
    """Dernier cours connu (intrajournalier si possible, sinon dernière clôture)."""
    try:
        d = intraday([ticker]).get(ticker)
        if d is not None and len(d):
            return float(d.Close.iloc[-1])
    except Exception:
        pass
    d = daily([ticker], period="1mo").get(ticker)
    return float(d.Close.iloc[-1]) if d is not None and len(d) else None
