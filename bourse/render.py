"""Construction des messages Telegram (HTML)."""
from . import positions as P, setups as S, translate
from .analysis import trend, next_session
from .fmt import b, esc, pct, price, num, fr_date, short_date

FOOTER = ("Le bot ne passe aucun ordre. Prix Yahoo de la place d'origine : ton courtier peut différer de 0,1 à 0,5 %. "
          "Ce n'est pas un conseil en investissement : tu peux perdre de l'argent.")


def idea_block(i, today=None):
    w, p, name = i["w"], i["plan"], i["setup"]
    t, sp = w["ticker"], S.SETUPS[i["setup"]]
    if i["validated"]:
        tag = "🟢 validé par le backtest"
    elif i.get("earn_risk"):
        tag = f"🟡 résultats dans {i['earn']} j : risque de gap, à éviter"
    elif i["bad"]:
        tag = "🔴 historiquement perdant : évite"
    else:
        tag = "🟡 non validé (pas de backtest concluant) : prudence"
    first = next_session(i["bar"])
    out = [f"{b(w['nom'])} ({esc(t)}) · {esc(sp['nom'])}  {tag}",
           f"Clôture du {short_date(i['bar'])} : {price(t, p['close'])} · RSI 2 j {i['rsi2']:.0f}",
           f"▸ Entrée à l'ouverture, sans courir : pas au-dessus de {price(t, p['max_entry'])}",
           f"▸ Stop {price(t, p['stop'])} ({pct(-p['stop_pct'])}), à poser dans l'app dès l'achat"]
    sortie = []
    if p["target"]:
        sortie.append(f"objectif {price(t, p['target'])}")
    if p["sma5"]:
        sortie.append(f"clôture au-dessus de ≈ {price(t, p['sma5'])} (moy. 5 j)")
    sortie.append(f"au plus tard {short_date(S.deadline(first, p['hold']))} à l'ouverture ({p['hold']} séances)")
    out.append("▸ Sortie : " + ", ou ".join(sortie))
    out.append(f"▸ Mise ≤ {num(p['mise'], 0)} € (risque ≈ {num(p['risk_eur'], 0)} € si le stop saute, frais ≈ {pct(p['fees_pct'], 1, False)})")
    st, ts = i["stat"], i.get("tstat")
    if st:
        out.append(f"▸ Historique ({st['scope']}, frais inclus) : {st['n']} trades, {st['win'] * 100:.0f} % gagnants, "
                   f"{pct(st['mean'], 2)} par trade (sans signal : {pct(st['base'], 2)})")
    if ts and ts["n"] >= 5:
        out.append(f"▸ Sur cette valeur : {ts['n']} trades, {ts['win'] * 100:.0f} % gagnants, {pct(ts['mean'], 2)} par trade (échantillon petit)")
    if i.get("also"):
        out.append("▸ Aussi déclenché : " + esc(", ".join(i["also"])))
    if i["trend"] == "down" and name == "capitulation":
        out.append("▸ ⚠ Tendance de fond baissière : rebond plus risqué")
    if p["illiquide"]:
        out.append("▸ ⚠ Peu liquide : l'écart achat/vente peut manger le gain")
    if i["w"]["cat"] == "pari":
        out.append("▸ ⚠ Valeur très spéculative : mise réduite, stop large")
    if i.get("earn") is not None and not i.get("earn_risk") and i["earn"] <= 14:
        out.append(f"▸ Résultats dans {i['earn']} j (hors durée du trade)")
    return "\n".join(out)


def ideas_section(ideas, titre="🎯 Setups swing du jour"):
    if not ideas:
        return f"{b(titre)}\nAucun setup ce matin : rester à l'écart est une position."
    return f"{b(titre)}\n\n" + "\n\n".join(idea_block(i) for i in ideas)


def trend_section(snap, order):
    lines = [b("📈 Tendance de fond (filtre de risque, pas une prévision)")]
    for t in order:
        s = snap.get(t)
        if not s:
            continue
        ind, w = s["ind"], s["w"]
        r = ind.iloc[-1]
        ico = trend(ind)[1].split()[0]
        lines.append(f"{ico} {esc(w['nom'])} {price(t, float(r.Close))} · 1 j {pct(float(ind.Close.pct_change().iloc[-1]))} · 5 j {pct(float(r.ret5))}")
    return "\n".join(lines)


def regime_line(rg):
    if not rg:
        return None
    parts = []
    for label in ("S&P 500", "CAC 40"):
        r = rg.get(label)
        if r:
            parts.append(f"{label} {pct(r['ret1'])} ({'au-dessus de' if r['above200'] else 'sous'} sa moy. 200 j)")
    if "VIX" in rg:
        v = rg["VIX"]
        parts.append(f"VIX {v:.0f} ({'calme' if v < 18 else 'nerveux' if v < 25 else 'tendu' if v < 32 else 'panique'})")
    return "🌍 " + " · ".join(parts) if parts else None


def news_lines(items_by_ticker, cfg_names):
    lines = []
    translate.fr_many(it["title"] for items in items_by_ticker.values() for it in items)
    for t, items in items_by_ticker.items():
        for it in items:
            ico = "⚠️" if it["kind"] == "neg" else "📰"
            src = f" ({esc(it['src'])})" if it.get("src") else ""
            link = f' <a href="{esc(it["url"])}">lien</a>' if it.get("url") else ""
            lines.append(f"{ico} {b(cfg_names.get(t, t))} : {esc(translate.fr(it['title'])[:140])}{src}{link}")
    return lines
