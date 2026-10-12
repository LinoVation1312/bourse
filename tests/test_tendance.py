"""Tests des tendances de fond, des positions « tendance », de SpaceX et des nouveaux messages (données synthétiques)."""
import re
import sys
import json
import pathlib
import unittest
import datetime as dt
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bourse import analysis as A, commands, data, jobs, positions as P, trend as T
from bourse.config import resolve
from bourse.indicators import compute
from test_all import CFG, FakeTG, fake_daily, make_ctx, synth, UTC


def frame(c, end="2026-10-09"):
    c = np.asarray(c, float)
    idx = pd.bdate_range(end=end, periods=len(c))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.004, "Low": np.minimum(o, c) * 0.996,
                         "Close": c, "Volume": np.full(len(c), 2e6)}, index=idx)


def up_series(n=400, g=0.002):
    return 100 * np.exp(np.cumsum(np.full(n, g)))


def crash_series():
    """300 j de hausse, 150 j de chute (−55 %), 150 j de stabilisation puis de reprise."""
    up = 100 * np.exp(np.cumsum(np.full(300, 0.002)))
    down = up[-1] * np.exp(np.cumsum(np.full(150, -0.0053)))
    rec = down[-1] * np.exp(np.cumsum(np.full(150, 0.002)))
    return np.r_[up, down, rec]


def check_html(testcase, msgs):
    for m in msgs:
        testcase.assertLessEqual(len(m), 4096)
        for tag in ("b", "i", "a"):
            testcase.assertEqual(len(re.findall(rf"<{tag}[ >]", m)), len(re.findall(rf"</{tag}>", m)), m[:200])


class TestTrendState(unittest.TestCase):
    def test_up_down_and_young(self):
        self.assertEqual(T.assess(compute(frame(up_series())))["state"], "up")
        down = 100 * np.exp(np.cumsum(np.full(400, -0.002)))
        self.assertEqual(T.assess(compute(frame(down)))["state"], "down")
        self.assertEqual(T.assess(compute(frame(up_series(84))))["state"], "young")     # ex. introduction en bourse récente

    def test_one_day_dip_does_not_change_confirmed_state(self):
        c = up_series(400)
        c[-1] = c[-2] * 0.80                                   # un seul jour de chute brutale sous les moyennes
        a = T.assess(compute(frame(c)))
        self.assertEqual(a["state"], "up")                     # état confirmé = celui de la veille
        self.assertNotEqual(a["raw"], "up")

    def test_detect_change_first_observation_is_silent(self):
        state = {}
        a_up = T.assess(compute(frame(up_series())))
        self.assertIsNone(T.detect_change(state, "NVDA", a_up, dt.date(2026, 10, 9)))
        self.assertIsNone(T.detect_change(state, "NVDA", a_up, dt.date(2026, 10, 10)))
        a_down = T.assess(compute(frame(100 * np.exp(np.cumsum(np.full(400, -0.002))))))
        self.assertEqual(T.detect_change(state, "NVDA", a_down, dt.date(2026, 10, 12)), ("up", "down"))
        self.assertIsNone(T.detect_change(state, "NVDA", a_down, dt.date(2026, 10, 13)))   # pas de répétition

    def test_change_text_levels(self):
        w = {"nom": "Nvidia"}
        self.assertEqual(T.change_text(w, "up", "down")[0], "alert")
        self.assertIn("moyenne 200", T.change_text(w, "up", "down")[1])
        self.assertEqual(T.change_text(w, "down", "up")[0], "info")
        self.assertEqual(T.change_text(w, "up", "pullback")[0], "info")
        self.assertIn("historique", T.change_text(w, "young", "up")[1])

    def test_section_marks_missing_and_young(self):
        ind = compute(frame(up_series(84)))
        snap = {"SPCX": {"w": {"ticker": "SPCX", "nom": "SpaceX", "cat": "pari"}, "ind": ind}}
        entries = [{"ticker": "SPCX", "nom": "SpaceX"}, {"ticker": "XXX", "nom": "Inconnue"}]
        text = T.section(snap, entries)
        self.assertIn("Tendance de fond", text)
        self.assertIn("84 séances d'historique", text)
        self.assertIn("pas de données récentes", text)
        check_html(self, [text])

    def test_section_labels_sessions_without_trading_today(self):
        ind = compute(frame(up_series(400), end="2026-10-08"))
        snap = {"MSFT": {"w": {"ticker": "MSFT", "nom": "Microsoft"}, "ind": ind}}
        text = T.section(snap, [{"ticker": "MSFT", "nom": "Microsoft"}], jour="aujourd'hui", today=dt.date(2026, 10, 9))
        self.assertIn("séance préc.", text)


class TestTendancePosition(unittest.TestCase):
    def setUp(self):
        self.ind = compute(frame(up_series(400)))
        self.entry = float(self.ind.Close.iloc[-30])
        self.date = self.ind.index[-30].date()
        self.w = {"ticker": "NVDA", "nom": "Nvidia", "cat": "grande"}

    def test_new_position_has_trailing_stop_below_entry(self):
        pos = P.new_position(CFG, self.w, self.entry, 5, "tendance", None, self.ind, self.date)
        self.assertEqual(pos["type"], "tendance")
        self.assertLess(pos["stop"], self.entry)
        self.assertEqual(pos["stop_told"], pos["stop"])
        self.assertEqual(pos["k"], 3.5)
        self.assertNotIn("max_days", pos)

    def test_trailing_stop_rises_and_never_falls(self):
        pos = P.new_position(CFG, self.w, self.entry, None, "tendance", None, self.ind, self.date)
        s0 = pos["stop"]
        self.assertTrue(P.refresh(self.w, pos, self.ind))
        s1 = pos["stop"]
        self.assertGreater(s1, s0)
        crash = self.ind.copy()
        crash.iloc[-1, crash.columns.get_loc("Close")] = float(crash.Close.iloc[-1]) * 0.7
        P.refresh(self.w, pos, crash)
        self.assertEqual(pos["stop"], s1)                      # ne descend jamais

    def test_daily_advice_raise_then_exit(self):
        pos = P.new_position(CFG, self.w, self.entry, None, "tendance", None, self.ind, self.date)
        adv = P.daily_advice(self.w, pos, self.ind, dt.date(2026, 10, 9), "soir")
        self.assertTrue(any("remonte ton stop" in a for a in adv))
        self.assertEqual(P.daily_advice(self.w, pos, self.ind, dt.date(2026, 10, 9), "soir"), [])   # annoncé une seule fois
        pos["stop"] = float(self.ind.Close.iloc[-1]) * 1.05
        adv = P.daily_advice(self.w, pos, self.ind, dt.date(2026, 10, 9), "soir")
        self.assertTrue(any("clôture sous ton stop de tendance" in a and "demain" in a for a in adv))

    def test_intraday_stop_event_and_peak_untouched(self):
        pos = P.new_position(CFG, self.w, self.entry, None, "tendance", None, self.ind, self.date)
        peak = pos["peak"]
        ev = P.intraday_events(self.w, pos, pos["stop"] - 0.01, dt.date(2026, 10, 12), CFG)
        self.assertEqual(ev[0][0], "stop")
        self.assertEqual(pos["peak"], peak)
        self.assertEqual(P.intraday_events(self.w, pos, pos["stop"] * 1.5, dt.date(2026, 10, 12), CFG), [])


class TestTrendBacktest(unittest.TestCase):
    def test_trend_rule_limits_drawdown_on_a_crash(self):
        ind = compute(frame(crash_series()))
        bh_dd = None
        for rule in T.RULES:
            r = T.simulate(ind, rule, "grande")
            self.assertIsNotNone(r, rule)
            bh_dd = r["bh_dd"]
            self.assertLess(bh_dd, -0.4, rule)                 # le scénario contient bien une grosse chute
            self.assertGreater(r["dd"], bh_dd * 0.75, rule)    # la règle réduit nettement la baisse maximale
            self.assertLess(r["invested"], 1.0)

    def test_trend_rule_stays_invested_in_a_clean_uptrend(self):
        ind = compute(frame(up_series(900, 0.0015)))
        r = T.simulate(ind, "sma200", "grande")
        self.assertGreater(r["invested"], 0.9)
        self.assertGreater(r["cagr"], 0.0)
        self.assertLess(r["trades_per_year"], 1.5)

    def test_short_history_returns_none(self):
        self.assertIsNone(T.simulate(compute(frame(up_series(300))), "sma200", "grande"))

    def test_verdict_logic(self):
        self.assertEqual(T.verdict({"dd": -0.2, "bh_dd": -0.5, "cagr": 0.12, "bh_cagr": 0.15})[0], "ok")
        self.assertEqual(T.verdict({"dd": -0.2, "bh_dd": -0.5, "cagr": 0.04, "bh_cagr": 0.15})[0], "mitige")
        self.assertEqual(T.verdict({"dd": -0.45, "bh_dd": -0.5, "cagr": 0.12, "bh_cagr": 0.15})[0], "non")

    def test_end_to_end_report_and_summary(self):
        with mock.patch.object(data, "daily", fake_daily):
            st = T.run_backtest(CFG, dt.datetime(2026, 10, 11, tzinfo=UTC))
        self.assertGreater(st["n_tickers"], 30)
        self.assertEqual(set(st["rules"]), set(T.RULES))
        a = st["rules"]["sma200"]["all"]
        self.assertGreater(a["n"], 30)
        self.assertLessEqual(abs(a["dd"]), 1.0)
        json.dumps(st)
        self.assertIn("Backtest tendance", T.report_md(st))
        summary = T.telegram_summary(st)
        self.assertIn("en gardant", summary)
        check_html(self, [summary])
        self.assertLess(len(summary), 4096)


class TestFlowsTendance(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch.object(data, "daily", fake_daily),
                  mock.patch.object(data, "last_price", lambda t, now: 100.0),
                  mock.patch.object(A, "earnings_in_days", lambda s, t, n: None),
                  mock.patch("bourse.news.fetch_many", lambda ts, now, key=None: {t: [] for t in ts})]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_spacex_is_in_watchlist_and_resolves(self):
        self.assertEqual(resolve(CFG, "spacex")["ticker"], "SPCX")
        self.assertIn("SPCX", [w["ticker"] for w in CFG["watchlist"]])

    def test_young_ipo_is_analysed(self):
        young = frame(up_series(84))
        with mock.patch.object(data, "daily", lambda ts, period="2y": {t: young for t in ts}):
            snap = A.snapshot([{"ticker": "SPCX", "nom": "SpaceX", "cat": "pari"}], dt.datetime(2026, 10, 12, 5, 45, tzinfo=UTC))
        self.assertIn("SPCX", snap)

    def test_achat_tendance_roundtrip(self):
        ctx = make_ctx(tg=FakeTG())
        for line in ["/achat nvidia tendance", "/statut", "/tendances", "/aide", "/vente nvidia 120"]:
            commands.handle(ctx, line)
        text = "\n".join(ctx.tg.sent)
        self.assertIn("Stop de tendance", text)
        self.assertIn("stop suiveur", text)
        self.assertIn("Tendance de fond", text)
        self.assertIn("/tendances", text)
        self.assertEqual(ctx.state["positions"], {})
        self.assertEqual(ctx.state["journal"][0]["type"], "tendance")
        check_html(self, ctx.tg.sent)

    def test_non_validated_ideas_hidden_by_default(self):
        ctx = make_ctx(tg=FakeTG())
        commands.handle(ctx, "/idees")
        text = "\n".join(ctx.tg.sent)
        self.assertNotIn("non validé (pas de backtest concluant)", text)
        commands.handle(ctx, "/idees tout")                       # ne doit pas planter
        check_html(self, ctx.tg.sent)

    def test_matin_has_trend_section_and_changes_after_first_run(self):
        ctx = make_ctx()
        jobs.matin(ctx)
        text = "\n".join(ctx.tg.sent)
        self.assertIn("Tendance de fond", text)
        self.assertNotIn("Changements de tendance", text)       # 1re observation silencieuse
        self.assertIn("SpaceX", text)
        # on force un ancien état « baissier » pour Nvidia : les données synthétiques sont haussières ou non, mais l'état change
        ctx.state["trend"]["NVDA"]["state"] = "down" if T.assess(compute(synth(seed=sum(map(ord, "NVDA")))))["state"] != "down" else "up"
        ctx.tg.sent.clear()
        jobs.matin(ctx)
        self.assertIn("Changements de tendance", "\n".join(ctx.tg.sent))
        check_html(self, ctx.tg.sent)

    def test_soir_reports_trend_breakdown_for_holder(self):
        ctx = make_ctx(now=dt.datetime(2026, 10, 12, 20, 40, tzinfo=UTC))
        down = frame(np.r_[up_series(300), up_series(300)[-1] * np.exp(np.cumsum(np.full(120, -0.007)))], end="2026-10-12")
        ctx.state["trend"] = {"NVDA": {"state": "up", "since": "2026-09-01"}}
        with mock.patch.object(data, "daily", lambda ts, period="2y": {t: (down if t == "NVDA" else synth(seed=sum(map(ord, t)), end="2026-10-12")) for t in ts}):
            jobs.soir(ctx)
        text = "\n".join(ctx.tg.sent)
        self.assertIn("passe sous sa moyenne 200 j", text)
        self.assertIn("Bilan de la séance", text)
        check_html(self, ctx.tg.sent)


if __name__ == "__main__":
    unittest.main()
