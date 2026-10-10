"""Backtest des règles TECHNIQUES du bot, net de frais, avec levier simulé.

Limites à garder en tête :
- le score fondamental et le filtre news ne sont pas rejouables (données historiques indisponibles
  en gratuit) : ici on teste seulement tendance + momentum + stops ;
- le levier est simulé avec réinitialisation quotidienne (comme un ETP 3x/2x) ; le coût de
  financement est une approximation ;
- exécution à l'ouverture du lendemain du signal ; stops évalués sur clôtures ;
- un bon résultat passé ne garantit rien.

Usage : python backtest.py            (10 ans, tickers de config.json)
"""
import os
import json

import numpy as np
import pandas as pd

from core import load, add_indicators, tech_buy, stop_mult


def simulate(df, bench63, lev, atr_max, cfg, mise=600.0, fin_annuel=0.05, eur=False):
    d = add_indicators(df)
    d["b63"] = bench63.reindex(d.index).ffill()
    cost_side = cfg["frais_ordre"] / mise + cfg["spread_pct"] + (0 if eur else cfg["change_pct"])
    fin_jour = fin_annuel * (lev - 1) / 252
    maxdays = cfg["jours_max_levier"] if lev > 1 else cfg["jours_max_1x"]
    o, c = d.Open.values, d.Close.values
    n = len(d)

    in_pos = pend_in = pend_out = False
    eq, curve, trades, days_in = 1.0, [], [], 0
    trade_ret = 1.0
    entry_date = None
    peak = stop = 0.0
    m = stop_mult(lev)

    for i in range(60, n):
        if in_pos:
            days_in += 1
            if pend_out:
                lr = lev * (o[i] / c[i - 1] - 1) - fin_jour
                eq *= (1 + lr) * (1 - cost_side)
                trade_ret *= (1 + lr) * (1 - cost_side)
                trades.append((entry_date, d.index[i], trade_ret - 1))
                in_pos = pend_out = False
            else:
                lr = lev * (c[i] / c[i - 1] - 1) - fin_jour
                eq *= (1 + lr)
                trade_ret *= (1 + lr)
        elif pend_in:
            pend_in = False
            in_pos = True
            entry_date = d.index[i]
            peak = max(o[i], c[i])
            stop = o[i] - m * d.atr.values[i - 1]
            lr = lev * (c[i] / o[i] - 1) - fin_jour
            eq *= (1 - cost_side) * (1 + lr)
            trade_ret = (1 - cost_side) * (1 + lr)
            days_in += 1
        curve.append(eq)

        row = d.iloc[i]
        if in_pos:
            peak = max(peak, c[i])
            stop = max(stop, peak - m * row.atr)
            age = (d.index[i] - entry_date).days
            if (c[i] < stop or c[i] < row.ema50 or (c[i] < row.ema20 and row.rsi < 45) or age > maxdays):
                pend_out = True
        elif tech_buy(row, row.b63, atr_max):
            pend_in = True

    if in_pos:  # position encore ouverte : valorisée à la dernière clôture
        trades.append((entry_date, d.index[-1], trade_ret - 1))

    curve = np.array(curve)
    dd = (curve / np.maximum.accumulate(curve) - 1).min() if len(curve) else 0
    bh = c[-1] / c[60] - 1
    return {"trades": trades, "total": eq - 1, "bh": bh, "maxdd": dd,
            "expo": days_in / max(1, n - 60), "start": d.index[60], "end": d.index[-1]}


def stats(trades):
    if not trades:
        return "0 trade"
    r = np.array([t[2] for t in trades])
    return f"{len(r)} trades, {np.mean(r > 0):.0%} gagnants, moyenne {r.mean():+.1%}, pire {r.min():+.1%}"


def main():
    cfg = json.load(open("config.json"))
    bench = load(cfg["benchmark"], "10y")
    b63 = bench.Close.pct_change(63)
    lines = ["BACKTEST net de frais (règles techniques seulement)", ""]
    for item in cfg["watchlist"]:
        t = item["ticker"]
        try:
            df = load(t, "10y")
        except Exception as e:
            lines.append(f"{t}: données indisponibles ({e})")
            continue
        for lev in (1, 2):
            if lev > item.get("max_lev", cfg["max_lev"]):
                continue
            r = simulate(df, b63, lev, item.get("atr_pct_max", cfg["atr_pct_max"]), cfg,
                         eur=item.get("devise") == "EUR")
            mid = r["start"] + (r["end"] - r["start"]) / 2
            h1 = [x for x in r["trades"] if x[1] <= mid]
            h2 = [x for x in r["trades"] if x[1] > mid]
            lines.append(f"{t} x{lev}: stratégie {r['total']:+.0%} vs acheter-garder 1x {r['bh']:+.0%} | "
                         f"baisse max {r['maxdd']:.0%} | investi {r['expo']:.0%} du temps")
            lines.append(f"   1re moitié : {stats(h1)}")
            lines.append(f"   2e moitié  : {stats(h2)}")
    lines += ["", "Lecture : si la stratégie ne bat pas 'acheter-garder' sur les DEUX moitiés, "
              "ou si la 2e moitié est mauvaise, ne te fie pas au résultat global."]
    report = "\n".join(lines)
    print(report)
    open("backtest_report.txt", "w").write(report)

    tok, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if tok and chat:
        import requests
        for i in range(0, len(report), 3800):
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": report[i:i + 3800]}, timeout=20)


if __name__ == "__main__":
    main()
