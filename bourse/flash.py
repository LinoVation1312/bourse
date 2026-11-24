"""Flash : toutes les grosses infos de marché, sans filtre sur tes valeurs.

Sources : flux RSS publics (Yahoo Finance, CNBC, MarketWatch, Réserve fédérale, BCE) + Finnhub (général, fusions) si
FINNHUB_KEY. Chaque titre reçoit un score d'importance (mots-clés macro / géopolitique / crise / fusions, montants en
milliards, gros mouvements en %, grands noms) ; au-dessus du seuil (4 par défaut) il part immédiatement. Tes valeurs et
tes positions *ajoutent* des points, elles ne servent jamais de filtre. Les titres de type « 5 actions à acheter » sont
pénalisés. Une même histoire n'est envoyée qu'une fois, quelle que soit la source.

Réglages (config.json) : "flash": {"seuil": 4, "fenetre": ["07:00", "23:30"], "intervalle_s": 60, "max_par_message": 5,
"heures": 3, "week_end": false, "actif": true, "flux": [{"nom": "...", "url": "...", "bonus": 0}]}
"""
import re
import time
import datetime as dt
import email.utils
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

from . import news as N, translate
from .fmt import b, esc

FEEDS = [
    {"nom": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex", "bonus": 0},
    {"nom": "CNBC", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "bonus": 0},
    {"nom": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "bonus": 0},
    {"nom": "Réserve fédérale", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "bonus": 1},
    {"nom": "BCE", "url": "https://www.ecb.europa.eu/rss/press.html", "bonus": 1},
]
UA = {"User-Agent": "Mozilla/5.0 (compatible; bourse-bot)"}
DEFAULTS = {"seuil": 4, "fenetre": ["07:00", "23:30"], "intervalle_s": 60, "max_par_message": 5, "heures": 3,
            "week_end": False, "actif": True}

# (motif, points) : chaque groupe compte une fois ; total des mots-clés plafonné à 8.
KEYWORDS = [
    (r"\bfomc\b|federal reserve|\bthe fed\b|\bfed (?:cuts?|hikes?|raises?|holds?|chair|governor|minutes|officials?)|powell|"
     r"lagarde|\becb\b|bank of japan|\bboj\b|bank of england|\bboe\b", 3),
    (r"rate (?:cut|hike|decision)|interest rates?|\bcpi\b|inflation|jobs report|nonfarm|payrolls|unemployment rate|"
     r"\bgdp\b|recession|\bpce\b", 3),
    (r"\brates?\b", 1),
    (r"tariffs?|trade war|sanctions?|embargo|export controls?", 4),
    (r"ceasefire|invasion|missile strikes?|airstrikes?|nuclear|\bcoup\b|terror attack", 4),
    (r"\bwar\b|missile|military|troops", 2),
    (r"\bopec\b|oil prices?|crude|natural gas prices?", 2),
    (r"government shutdown|debt ceiling|credit rating|downgrades? (?:u\.?s\.?|france|italy|china|japan)", 4),
    (r"bank run|bank failure|bank collapse|bailout|liquidity crisis|\bdefaults?\b|\bdefaulted\b", 4),
    (r"bankrupt|chapter 11|insolven", 3),
    (r"crash|plunge|plummet|tumble|sell-?off|\brout\b|circuit breaker|trading halt|halted", 3),
    (r"surge|soar|skyrocket|\brall(?:y|ies)\b|record high|all-time high", 2),
    (r"merger|acquir|acquisition|takeover|buyout|\bipo\b|spin-?off", 2),
    (r"layoffs?|job cuts|guidance|profit warning|earnings (?:beat|miss)|beats? estimates|misses? estimates", 2),
    (r"investigation|antitrust|lawsuit|\bsues\b|subpoena|indict|fraud|\bprobe\b|recall", 2),
    (r"\bfda\b|approval|approves", 1),
    (r"bitcoin|crypto|ether(?:eum)?\b|stablecoin", 1),
    (r"\bbreaking\b|just in|urgent", 2),
]
BIG_NAMES = ("apple", "microsoft", "nvidia", "amazon", "alphabet", "google", "meta", "tesla", "berkshire", "jpmorgan",
             "goldman", "tsmc", "openai", "anthropic", "spacex", "samsung", "lvmh", "totalenergies", "airbus", "asml",
             "intel", "amd", "boeing", "exxon", "saudi aramco", "alibaba", "tencent", "binance", "coinbase")
NOISE = (r"stocks? to (?:buy|watch|sell)|should you (?:buy|sell)|best (?:stocks?|etfs?)|here'?s why|motley fool|zacks|"
         r"top \d+ |\d+ (?:stocks|etfs)|price target|analyst picks?|what to know|things to know|horoscope|"
         r"announces? (?:approval|termination|enforcement)|written agreement|\bapplication\b")
AMOUNT = re.compile(r"[$€£]\s?(\d[\d,.]*)\s?(trillion|tn|billion|bn|million|m)\b|(\d[\d,.]*)\s?(trillion|billion|bn)\s?(?:of\s)?(?:dollars|euros)", re.I)
PCT = re.compile(r"(\d+(?:[.,]\d+)?)\s?(?:%|percent)")


def conf(ctx_or_cfg):
    cfg = ctx_or_cfg.cfg if hasattr(ctx_or_cfg, "cfg") else ctx_or_cfg
    return {**DEFAULTS, **cfg.get("flash", {})}


# ------------------------------------------------------------------ collecte
def _parse_date(text):
    text = text.strip()
    try:
        return email.utils.parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_feed(content, nom, bonus=0, now=None):
    """Titres d'un flux RSS ou Atom -> [{title, src, url, ts, bonus}]. Les flux avec DOCTYPE/ENTITY sont refusés."""
    text = content if isinstance(content, str) else content.decode("utf-8", "replace")
    if "<!DOCTYPE" in text[:2000].upper() and "<!ENTITY" in text.upper():
        return []
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return []
    out = []
    for it in root.iter():
        if it.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        g = lambda name: next((c for c in it if c.tag.rsplit("}", 1)[-1] == name), None)
        title, link = g("title"), g("link")
        date = next((d for d in map(g, ("pubDate", "published", "updated", "date")) if d is not None), None)
        if title is None or not (title.text or "").strip():
            continue
        url = (link.text or link.get("href") or "").strip() if link is not None else ""
        ts = _parse_date(date.text) if date is not None and date.text else None
        out.append({"title": re.sub(r"\s+", " ", title.text).strip(), "src": nom, "url": url,
                    "ts": ts if ts else (now.timestamp() if now else time.time()), "bonus": bonus})
    return out


def fetch_feed(feed, now):
    try:
        r = requests.get(feed["url"], headers=UA, timeout=8)
        r.raise_for_status()
        return parse_feed(r.content, feed["nom"], feed.get("bonus", 0), now)
    except (requests.RequestException, ValueError) as e:
        print(f"Flux {feed['nom']} indisponible ({type(e).__name__})")
        return []


def fetch_finnhub(key, now):
    out = []
    for cat in ("general", "merger"):
        try:
            r = requests.get("https://finnhub.io/api/v1/news", params={"category": cat, "token": key}, timeout=8).json()
            out += [{"title": x["headline"], "src": x.get("source", "Finnhub"), "url": x.get("url", ""),
                     "ts": float(x.get("datetime", 0)), "bonus": 0} for x in r if isinstance(x, dict) and x.get("headline")]
        except (requests.RequestException, ValueError, TypeError):
            print(f"Finnhub {cat} indisponible")
    return out


def collect(ctx):
    """Tous les flux en parallèle (≈ 2-5 s au total ; un flux en panne n'empêche pas les autres)."""
    feeds = conf(ctx).get("flux") or FEEDS
    jobs = [lambda f=f: fetch_feed(f, ctx.now) for f in feeds]
    if ctx.finnhub:
        jobs.append(lambda: fetch_finnhub(ctx.finnhub, ctx.now))
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        return [it for part in ex.map(lambda j: j(), jobs) for it in part]


# ------------------------------------------------------------------ score
def score(title, bonus=0):
    """Importance d'un titre, indépendamment de tes valeurs (0 = anodin, 4 = à envoyer, 8+ = majeur)."""
    low = title.lower()
    pts = min(8, sum(p for pat, p in KEYWORDS if re.search(pat, low)))
    amount = 0
    for m in AMOUNT.finditer(title):
        num = m.group(1) or m.group(3)
        unit = (m.group(2) or m.group(4)).lower()
        try:
            n = float(num.replace(",", ""))
        except ValueError:
            continue
        bn = n * (1000 if unit in ("trillion", "tn") else 1 if unit in ("billion", "bn") else 0.001)
        amount = max(amount, 4 if bn >= 100 else 3 if bn >= 10 else 2 if bn >= 1 else 0)
    pct = max((float(m.group(1).replace(",", ".")) for m in PCT.finditer(title)), default=0)
    pts += amount + (3 if pct >= 10 else 2 if pct >= 5 else 0)
    pts += min(2, sum(1 for n in BIG_NAMES if re.search(rf"\b{re.escape(n)}\b", low)))
    if re.search(NOISE, low):
        pts -= 4
    return pts + bonus


def touches(ctx, title, entries):
    """'held' si le titre cite une de tes positions, 'watch' s'il cite une valeur suivie, sinon None."""
    held, hit = ctx.state["positions"], None
    for w in entries:
        if N.mentions(title, w):
            if w["ticker"] in held:
                return "held"
            hit = "watch"
    return hit


# ------------------------------------------------------------------ sélection
def pick(ctx, items, hours=None, thr=None, mark=True):
    """Titres importants, jamais renvoyés (même histoire sous un autre titre = doublon), les plus importants d'abord."""
    from .synthese import tracked
    c = conf(ctx)
    hours = c["heures"] if hours is None else hours
    thr = c["seuil"] if thr is None else thr
    lim = ctx.now.timestamp() - hours * 3600
    seen, sigs = ctx.state["news_seen"], ctx.state.setdefault("news_sigs", {})
    entries = tracked(ctx)
    out, batch = [], []
    for it in sorted(items, key=lambda x: -x["ts"]):
        if it["ts"] < lim:
            continue
        k = N.key(it["title"])
        if k in seen:
            continue
        touch = touches(ctx, it["title"], entries)
        sc = score(it["title"], it.get("bonus", 0)) + {"held": 3, "watch": 2}.get(touch, 0)
        if sc < thr:
            continue
        w = N.words(it["title"])
        if any(N.similar(w, v["w"], 0.6) for v in sigs.values()) or any(N.similar(w, o, 0.6) for o in batch):
            if mark:
                seen[k] = ctx.now.timestamp()
            continue
        batch.append(w)
        out.append({**it, "score": sc, "touch": touch, "kind": N.classify(it["title"]) or "info", "_k": k, "_w": w})
    out.sort(key=lambda x: (-x["score"], -x["ts"]))
    if mark:
        log = ctx.state.setdefault("flash_log", [])
        for it in out:
            seen[it["_k"]] = ctx.now.timestamp()
            sigs[it["_k"]] = {"t": ctx.now.timestamp(), "w": it["_w"], "tk": None}
            log.append({"title": it["title"], "src": it["src"], "url": it["url"], "ts": it["ts"], "score": it["score"],
                        "kind": it["kind"], "t": ctx.now.timestamp()})
        del log[:-80]
    return out


def lines(picks, cap):
    shown = picks[:cap]
    translate.fr_many(it["title"] for it in shown)
    out = []
    for it in shown:
        ico = "⚠️" if it["kind"] == "neg" else "📰"
        tag = "💼 " if it.get("touch") == "held" else ""
        link = f' <a href="{esc(it["url"])}">lien</a>' if it.get("url") else ""
        out.append(f"{ico} {tag}{esc(translate.fr(it['title'])[:170])} ({esc(it['src'])}){link}")
    if len(picks) > cap:
        out.append(f"<i>+ {len(picks) - cap} autre(s) info(s) importante(s) : /synthese</i>")
    return out


def block(ctx):
    """Texte « ⚡ Flash marché » des nouvelles grosses infos (les marque comme envoyées), ou None."""
    c = conf(ctx)
    if not c["actif"]:
        return None
    picks = pick(ctx, collect(ctx))
    return (b("⚡ Flash marché") + "\n" + "\n".join(lines(picks, c["max_par_message"]))) if picks else None


def recent(ctx, hours):
    """Grosses infos déjà envoyées en flash depuis `hours` h, pour le récapitulatif des synthèses (sans rien consommer)."""
    lim = ctx.now.timestamp() - hours * 3600
    return [{**e, "touch": None} for e in ctx.state.get("flash_log", []) if e["t"] > lim]
