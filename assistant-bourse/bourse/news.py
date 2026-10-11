"""Actualités par valeur (Yahoo Finance, + Finnhub si FINNHUB_KEY) et repérage des titres sensibles."""
import hashlib
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

NEG = ["investigation", "probe", "lawsuit", "sues ", "subpoena", "sec charges", "fraud", "indict", "antitrust",
       "bankrupt", "chapter 11", "going concern", "delist", "trading halt", "halted", "stock offering",
       "public offering", "dilut", "private placement", "downgrad", "profit warning", "cuts guidance",
       "lowers guidance", "cuts forecast", "resigns", "steps down", "cyberattack", "data breach", "recall",
       "plunge", "tumble", "short seller", "short report", "misses estimates",
       "enquête", "procès", "avertissement sur résultats", "abaisse ses prévisions", "augmentation de capital",
       "redressement judiciaire", "fraude", "démission", "dégradation de la note", "abaisse son objectif"]
POS = ["upgrade", "raises guidance", "raises forecast", "record revenue", "beats estimates", "buyback",
       "to acquire", "acquisition", "contract win", "fda approval", "surge", "soars",
       "relève ses prévisions", "relèvement", "rachat d'actions", "contrat", "relève son objectif"]


def classify(title):
    low = title.lower()
    neg, pos = any(k in low for k in NEG), any(k in low for k in POS)
    return "neg" if neg else "pos" if pos else None


def fetch(ticker, now, finnhub_key=None):
    """[{title, src, url, ts}] (ts en secondes UTC)."""
    out = []
    try:
        import yfinance as yf
        for n in yf.Ticker(ticker).news or []:
            c = n.get("content") or n
            ts, title = c.get("pubDate") or c.get("providerPublishTime"), c.get("title")
            if not ts or not title:
                continue
            ts = float(ts) if isinstance(ts, (int, float)) else pd.Timestamp(ts).timestamp()
            url = (c.get("canonicalUrl") or {}).get("url") or c.get("link") or ""
            src = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo"
            out.append({"title": title, "src": src, "url": url, "ts": ts})
    except Exception:
        pass
    if finnhub_key and "." not in ticker and "=" not in ticker:
        try:
            d = now.date()
            r = requests.get("https://finnhub.io/api/v1/company-news", timeout=20, params={
                "symbol": ticker, "from": d - dt.timedelta(days=1), "to": d, "token": finnhub_key}).json()
            out += [{"title": x["headline"], "src": x.get("source", "Finnhub"), "url": x.get("url", ""),
                     "ts": float(x.get("datetime", 0))} for x in r if isinstance(x, dict) and x.get("headline")]
        except Exception:
            pass
    return sorted(out, key=lambda x: -x["ts"])


def fetch_many(tickers, now, finnhub_key=None):
    tickers = list(tickers)
    if not tickers:
        return {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(zip(tickers, ex.map(lambda t: fetch(t, now, finnhub_key), tickers)))


def key(title):
    return hashlib.md5(title.lower().encode()).hexdigest()[:12]


def sensitive(items, now, state, hours, limit=2, mark=True):
    """Titres sensibles récents, jamais renvoyés deux fois (si mark=True)."""
    lim = now.timestamp() - hours * 3600
    res = []
    for it in items:
        kind = classify(it["title"])
        if it["ts"] < lim or kind is None:
            continue
        k = key(it["title"])
        if k in state["news_seen"]:
            continue
        if mark:
            state["news_seen"][k] = now.timestamp()
        res.append({**it, "kind": kind})
        if len(res) >= limit:
            break
    return res
