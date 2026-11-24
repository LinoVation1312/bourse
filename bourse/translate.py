"""Traduction des titres d'actualité en français (au mieux : en cas d'échec, le titre d'origine est conservé).

Utilise le point d'accès public de Google Traduction (aucune clé). Si le service est injoignable, on s'arrête
après le premier échec pour ne pas ralentir le bot. Désactivable : "traduire_actus": false dans config.json
ou variable d'environnement BOURSE_NO_TRANSLATE=1.
"""
import os
from concurrent.futures import ThreadPoolExecutor

import requests

URL = "https://translate.googleapis.com/translate_a/single"
_cache = {}
_broken = False


def enabled():
    return not os.environ.get("BOURSE_NO_TRANSLATE")


def fr(text, timeout=6):
    """Titre traduit en français (langue source détectée) ; le texte d'origine si la traduction échoue."""
    global _broken
    if not text or _broken or not enabled():
        return text
    if text in _cache:
        return _cache[text]
    try:
        r = requests.get(URL, params={"client": "gtx", "sl": "auto", "tl": "fr", "dt": "t", "q": text}, timeout=timeout)
        r.raise_for_status()
        out = "".join(seg[0] for seg in r.json()[0] if seg and seg[0]).strip() or text
    except (requests.RequestException, ValueError, IndexError, TypeError):
        _broken = True
        return text
    _cache[text] = out
    return out


def fr_many(texts, workers=6):
    """Traduit plusieurs titres en parallèle et remplit le cache : les appels suivants à fr() sont instantanés."""
    todo = [t for t in dict.fromkeys(texts) if t and t not in _cache]
    if not todo or _broken or not enabled():
        return
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(fr, todo))
