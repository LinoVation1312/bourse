"""État persistant (positions suivies, alertes déjà envoyées, curseur Telegram) : un seul fichier JSON."""
import json
import pathlib
import datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT = {"offset": 0, "positions": {}, "journal": [], "sent": {}, "levels": {},
           "news_seen": {}, "news_sigs": {}, "flash_log": [], "earn": {}, "slots": {}}


def load(path=None):
    p = path or ROOT / "state.json"
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    for k, v in DEFAULT.items():
        s.setdefault(k, json.loads(json.dumps(v)))
    return s


def save(state, path=None):
    p = path or ROOT / "state.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def fresh(state, key, now, hours):
    """Vrai (et mémorise) si `key` n'a pas été émise depuis `hours` heures."""
    last = state["sent"].get(key)
    if last and (now - dt.datetime.fromisoformat(last)).total_seconds() < hours * 3600:
        return False
    state["sent"][key] = now.isoformat(timespec="seconds")
    return True


def prune(state, now):
    lim = now - dt.timedelta(days=10)
    state["sent"] = {k: v for k, v in state["sent"].items() if dt.datetime.fromisoformat(v) >= lim}
    ts_lim = (now - dt.timedelta(days=4)).timestamp()
    state["news_seen"] = {k: v for k, v in state["news_seen"].items() if v >= ts_lim}
    state["news_sigs"] = {k: v for k, v in state["news_sigs"].items() if v["t"] >= ts_lim}
    state["flash_log"] = [e for e in state["flash_log"] if e["t"] >= ts_lim]
    today = now.date().isoformat()
    state["levels"] = {k: v for k, v in state["levels"].items() if k.split("|")[1] == today}
    state["journal"] = state["journal"][-300:]
