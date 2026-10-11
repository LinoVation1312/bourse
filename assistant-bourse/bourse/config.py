"""Chargement de config.json, de stats.json et résolution des noms de valeurs."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_config(path=None):
    return json.loads((path or ROOT / "config.json").read_text(encoding="utf-8"))


def load_stats(path=None):
    p = path or ROOT / "stats.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def watch_entries(cfg):
    return cfg["watchlist"]


def scan_entries(cfg):
    """Valeurs analysées pour les idées swing : ta watchlist + (option) l'univers."""
    seen, out = set(), []
    items = list(cfg["watchlist"]) + (list(cfg.get("univers", [])) if cfg["swing"].get("scanner_univers") else [])
    for w in items:
        if w["ticker"] not in seen:
            seen.add(w["ticker"])
            out.append(w)
    return out


def resolve(cfg, q):
    """Retrouve une valeur par nom court, alias ou ticker. Un ticker Yahoo inconnu est accepté (catégorie « grande »)."""
    q = q.strip().lower()
    for w in list(cfg["watchlist"]) + list(cfg.get("univers", [])):
        names = {w["ticker"].lower(), w["ticker"].split(".")[0].lower(), w["nom"].lower(), *w.get("alias", [])}
        if q in names:
            return w
    if q and all(ch.isalnum() or ch in ".=^-" for ch in q) and len(q) <= 12:
        return {"ticker": q.upper(), "nom": q.upper(), "cat": "grande", "alias": []}
    return None


def entry_for(cfg, ticker):
    for w in list(cfg["watchlist"]) + list(cfg.get("univers", [])):
        if w["ticker"] == ticker:
            return w
    return {"ticker": ticker, "nom": ticker, "cat": "grande", "alias": []}
