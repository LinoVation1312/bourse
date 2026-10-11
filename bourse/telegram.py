"""Envoi et lecture de messages Telegram. En mode --dry, tout s'affiche dans le terminal."""
import re
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def chunk(text, limit=3800):
    """Coupe aux sauts de paragraphe, sans jamais dépasser `limit`."""
    parts, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > limit:                       # paragraphe géant : coupe à une fin de ligne
            cut = para.rfind("\n", 0, limit) or limit
            cut = cut if cut > 0 else limit
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(para[:cut])
            para = para[cut:].lstrip("\n")
        if cur and len(cur) + len(para) + 2 > limit:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return parts


class Telegram:
    def __init__(self, token, chat, dry=False):
        self.token, self.chat, self.dry = token, str(chat), dry
        self.sent = []          # utile pour les tests

    def _post(self, method, **data):
        for attempt in range(3):
            try:
                r = requests.post(API.format(token=self.token, method=method), data=data, timeout=25)
            except requests.RequestException as e:
                print(f"Telegram {method} : erreur réseau ({type(e).__name__})")
                time.sleep(2)
                continue
            if r.status_code == 429:
                time.sleep(min(int(r.json().get("parameters", {}).get("retry_after", 3)), 30))
                continue
            if r.status_code != 200:                       # visible dans les logs GitHub (token ou chat_id faux, etc.)
                print(f"Telegram {method} -> HTTP {r.status_code} : {r.text[:200]}")
            return r
        return None

    def send(self, text):
        for part in chunk(text):
            self.sent.append(part)
            if self.dry:
                print("─" * 60 + "\n" + re.sub(r"</?[a-z]+>", "", part))
                continue
            r = self._post("sendMessage", chat_id=self.chat, text=part, parse_mode="HTML",
                           disable_web_page_preview="true")
            if r is not None and r.status_code == 400:      # balise HTML mal formée : on renvoie en texte brut
                self._post("sendMessage", chat_id=self.chat, text=re.sub(r"</?[a-z]+>", "", part),
                           disable_web_page_preview="true")

    def updates(self, offset):
        """[(update_id, texte)] des messages reçus de TON chat uniquement."""
        if self.dry:
            return []
        try:
            r = requests.get(API.format(token=self.token, method="getUpdates"),
                             params={"offset": offset + 1, "timeout": 0}, timeout=25).json()
        except (requests.RequestException, ValueError):
            print("Telegram getUpdates : réponse illisible")
            return []
        if not r.get("ok", True):
            print(f"Telegram getUpdates refusé : {str(r)[:200]}")
        out = []
        for u in r.get("result", []):
            msg = u.get("message") or {}
            ok = str(msg.get("chat", {}).get("id")) == self.chat
            out.append((u["update_id"], msg.get("text", "") if ok else None))
        return out
