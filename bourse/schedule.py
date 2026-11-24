"""Décide quoi faire à chaque exécution, selon l'heure de Paris. Stdlib uniquement : tourne avant l'installation
des dépendances (étape « gate » du workflow), ce qui évite de les installer quand il n'y a rien à faire.

  ouverture : première exécution entre 15h40 et 18h30 -> briefing après l'ouverture de Wall Street (15h30 Paris)
  soir      : première exécution entre 22h20 et 23h50 -> débriefing après la clôture US (22h00 Paris)
  watch     : de 9h à 22h05                           -> alertes en séance + grosses infos + commandes Telegram
  flash     : de 7h à 23h30 hors séance               -> grosses infos de marché seulement (rapide)
  skip      : le reste (week-end, nuit)

Les fenêtres se règlent dans config.json : "briefings": {"ouverture": ["15:40", "18:30"], "soir": ["22:20", "23:50"],
"matin": false}. Le briefing « matin » (pré-ouverture, ≈ 8h) est désactivé par défaut ; pour le réactiver :
"matin": ["07:40", "10:00"].

Le soir ne commence pas avant 22h20 : data.completed() n'accepte la barre du jour qu'à clôture + 20 min.
"""
import datetime as dt
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

DEFAULT_WINDOWS = {"ouverture": ("15:40", "18:30"), "soir": ("22:20", "23:50"), "matin": None}
ORDER = ("matin", "ouverture", "soir")        # priorité si plusieurs fenêtres se chevauchent
SLEEP = ("09:00", "22:05")                    # fenêtre des alertes en séance


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def windows(cfg=None):
    conf = dict(DEFAULT_WINDOWS)
    conf.update((cfg or {}).get("briefings", {}))
    return {k: (_minutes(v[0]), _minutes(v[1])) for k, v in conf.items() if v}


def decide(now_utc, slots, override=None, cfg=None):
    if override and override != "auto":
        return override
    p = now_utc.astimezone(PARIS)
    hm = p.hour * 60 + p.minute
    fl = {"actif": True, "fenetre": ["07:00", "23:30"], "week_end": False, **(cfg or {}).get("flash", {})}
    in_flash = fl["actif"] and _minutes(fl["fenetre"][0]) <= hm < _minutes(fl["fenetre"][1])
    if p.weekday() >= 5:
        return "flash" if in_flash and fl["week_end"] else "skip"
    today = p.date().isoformat()
    win = windows(cfg)
    for name in ORDER:
        if name in win and win[name][0] <= hm < win[name][1] and slots.get(name) != today:
            return name
    if _minutes(SLEEP[0]) <= hm < _minutes(SLEEP[1]):
        return "watch"
    return "flash" if in_flash else "skip"
