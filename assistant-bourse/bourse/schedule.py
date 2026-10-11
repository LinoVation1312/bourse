"""Décide quoi faire à chaque exécution, selon l'heure de Paris. Stdlib uniquement : tourne avant l'installation
des dépendances (étape « gate » du workflow), ce qui évite de les installer quand il n'y a rien à faire.

  matin  : première exécution entre 7h40 et 10h  -> récap de pré-ouverture
  watch  : de 9h à 22h05                         -> alertes en séance + commandes Telegram
  soir   : première exécution entre 22h15 et 23h50 -> bilan de clôture + nouveaux setups
  skip   : le reste (week-end, nuit, entre 7h40 et 9h une fois le matin envoyé)
"""
import datetime as dt
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


def decide(now_utc, slots, override=None):
    if override and override != "auto":
        return override
    p = now_utc.astimezone(PARIS)
    if p.weekday() >= 5:
        return "skip"
    hm = p.hour * 60 + p.minute
    today = p.date().isoformat()
    if 7 * 60 + 40 <= hm < 10 * 60 and slots.get("matin") != today:
        return "matin"
    if 22 * 60 + 15 <= hm < 23 * 60 + 50 and slots.get("soir") != today:
        return "soir"
    if 9 * 60 <= hm < 22 * 60 + 5:
        return "watch"
    return "skip"
