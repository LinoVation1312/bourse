"""Tests sur données synthétiques (aucun accès réseau) : python -m unittest discover -s tests -v"""
import sys
import json
import pathlib
import unittest
import datetime as dt
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from bourse import analysis as A, backtest, commands, data, flash as F, jobs, positions as P, render as R, setups as S, state as ST
from bourse.config import load_config, resolve
from bourse.indicators import compute
from bourse.schedule import decide
from bourse.synthese import compose as synthese_compose
from bourse.telegram import Telegram, chunk

UTC = dt.timezone.utc
CFG = load_config()


def synth(n=1500, seed=1, drift=0.0004, vol=0.018, end="2026-10-09"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    ret = rng.normal(drift, vol, n)
    c = 100 * np.exp(np.cumsum(ret))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.003, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, n)))
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": rng.integers(1e6, 5e6, n).astype(float)}, index=idx)


def pullback_df():
    """Forte tendance haussière puis 3 jours de baisse : doit déclencher « pullback »."""
    n = 320
    idx = pd.bdate_range(end="2026-10-09", periods=n)
    c = 100 * np.exp(np.cumsum(np.full(n, 0.003)))
    c[-3:] = c[-4] * np.array([0.985, 0.972, 0.962])
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.004, "Low": np.minimum(o, c) * 0.996,
                         "Close": c, "Volume": np.full(n, 2e6)}, index=idx)


class FakeTG(Telegram):
    def __init__(self, inbox=()):
        super().__init__("t", "1", dry=True)
        self.inbox = list(inbox)

    def send(self, text):
        self.sent.append(text)

    def updates(self, offset, timeout=0):
        return [(offset + 1 + i, t) for i, t in enumerate(self.inbox)]


def make_ctx(tg=None, state=None, stats=None, now=None):
    return jobs.Ctx(cfg=CFG, state=state or ST.load(pathlib.Path("/nonexistent")), stats=stats or {},
                    tg=tg or FakeTG(), now=now or dt.datetime(2026, 10, 12, 5, 45, tzinfo=UTC))


def fake_daily(tickers, period="2y"):
    return {t: synth(seed=sum(map(ord, t))) for t in tickers}


class TestSimulate(unittest.TestCase):
    def arr(self, O, H, L, C):
        n = len(O)
        return {"O": np.array(O, float), "H": np.array(H, float), "L": np.array(L, float), "C": np.array(C, float),
                "S5": np.full(n, 1e9), "A": np.full(n, 0.02)}

    def test_stop_gap_down_fills_at_open(self):
        # signal i=0, entrée O[1]=100, stop grande = clip(1.5*2%=3%,3%,8%) = 3 % -> 97 ; ouverture J+2 à 90
        a = self.arr([100, 100, 90, 90, 90, 90, 90], [101] * 7, [99, 99, 89, 89, 89, 89, 89], [100] * 7)
        r = S.simulate(a, 0, "pullback", "grande")
        self.assertEqual(r[2], "stop")
        self.assertAlmostEqual(r[0], 90 / 100 - 1)

    def test_target_and_stop_same_bar_is_stop(self):
        a = self.arr([100, 100, 100, 100], [101, 120, 101, 101], [99, 90, 99, 99], [100] * 4)
        r = S.simulate(a, 0, "breakout", "grande")
        self.assertEqual(r[2], "stop")

    def test_target_hit(self):
        # breakout : k=2 -> 4 % de stop, objectif 2R = +8 %
        a = self.arr([100, 100, 101, 100, 100, 100, 100, 100, 100],
                     [101, 101, 109, 101, 101, 101, 101, 101, 101],
                     [99, 99, 99.5, 99, 99, 99, 99, 99, 99], [100] * 9)
        r = S.simulate(a, 0, "breakout", "grande")
        self.assertEqual(r[2], "objectif")
        self.assertAlmostEqual(r[0], 0.08, places=6)

    def test_time_exit(self):
        a = self.arr([100] * 12, [101] * 12, [99.5] * 12, [100] * 12)
        r = S.simulate(a, 0, "breakout", "grande")
        self.assertEqual((r[1], r[2]), (7, "délai"))

    def test_returns_none_when_history_too_short(self):
        a = self.arr([100] * 4, [101] * 4, [99.5] * 4, [100] * 4)
        self.assertIsNone(S.simulate(a, 0, "breakout", "grande"))
        self.assertIsNone(S.simulate(a, 3, "breakout", "grande"))

    def test_mean_reversion_exit(self):
        n = 10
        a = self.arr([100] * n, [101] * n, [99.5] * n, [100] * n)
        a["S5"] = np.full(n, 99.0)       # clôture > moyenne 5 j dès la 1re séance
        r = S.simulate(a, 0, "pullback", "grande")
        self.assertEqual((r[1], r[2]), (1, "moyenne"))


class TestSignals(unittest.TestCase):
    def test_pullback_triggers(self):
        ind = compute(pullback_df())
        self.assertIn("pullback", S.active(ind))
        self.assertNotIn("breakout", S.active(ind))

    def test_no_lookahead(self):
        """Les signaux à la date J ne changent pas si on retire les données futures."""
        df = synth(seed=7)
        full = S.signals(compute(df))
        cut = S.signals(compute(df.iloc[:1000]))
        pd.testing.assert_frame_equal(full.iloc[:1000], cut, check_dtype=False)

    def test_trades_do_not_overlap(self):
        ind = compute(synth(seed=3, vol=0.03))
        tr = S.trades(ind, "capitulation", "grande")
        self.assertTrue(len(tr) >= 0)
        for t in tr:
            self.assertTrue(np.isfinite(t["ret"]))

    def test_plan_sizing_respects_limits(self):
        ind = compute(pullback_df())
        p = S.trade_plan(ind, "grande", "pullback", CFG)
        self.assertLessEqual(p["mise"], CFG["capital_swing"] * CFG["mise_max_pct"] + 1e-6)
        self.assertLessEqual(p["risk_eur"], CFG["capital_swing"] * CFG["risque_par_trade"] + 1e-6)
        self.assertLess(p["stop"], p["close"])
        self.assertGreaterEqual(p["stop_pct"], S.STOP_BOUNDS["grande"][0])
        self.assertLessEqual(p["stop_pct"], S.STOP_BOUNDS["grande"][1])


class TestSchedule(unittest.TestCase):
    def utc(self, y, m, d, h, mi):
        return dt.datetime(y, m, d, h, mi, tzinfo=UTC)

    def test_summer_and_winter(self):
        self.assertEqual(decide(self.utc(2026, 7, 8, 13, 45), {}), "ouverture")   # 15h45 Paris (UTC+2)
        self.assertEqual(decide(self.utc(2026, 12, 9, 14, 45), {}), "ouverture")  # 15h45 Paris (UTC+1)
        self.assertEqual(decide(self.utc(2026, 7, 8, 13, 25), {}), "watch")       # 15h25 : Wall Street pas encore ouvert
        self.assertEqual(decide(self.utc(2026, 7, 8, 10, 0), {}), "watch")        # 12h
        self.assertEqual(decide(self.utc(2026, 7, 8, 5, 45), {}), "flash")        # 7h45 : plus de briefing matinal, mais les grosses infos
        self.assertEqual(decide(self.utc(2026, 7, 8, 20, 10), {}), "flash")       # 22h10 : trop tôt pour le bilan (barre US pas prête)
        self.assertEqual(decide(self.utc(2026, 7, 8, 3, 0), {}), "skip")          # 5h : nuit
        self.assertEqual(decide(self.utc(2026, 7, 8, 20, 22), {}), "soir")        # 22h22 Paris
        self.assertEqual(decide(self.utc(2026, 12, 9, 21, 25), {}), "soir")

    def test_briefings_only_once_and_weekend(self):
        self.assertEqual(decide(self.utc(2026, 10, 12, 13, 50), {"ouverture": "2026-10-12"}), "watch")   # déjà envoyé
        self.assertEqual(decide(self.utc(2026, 10, 12, 20, 30), {"soir": "2026-10-12"}), "flash")      # bilan déjà envoyé
        self.assertEqual(decide(self.utc(2026, 10, 10, 13, 50), {}), "skip")                             # samedi
        self.assertEqual(decide(self.utc(2026, 10, 10, 13, 50), {}, cfg={"flash": {"week_end": True}}), "flash")   # option week-end
        self.assertEqual(decide(self.utc(2026, 10, 12, 13, 50), {}, cfg={"flash": {"actif": False}}), "ouverture")
        self.assertEqual(decide(self.utc(2026, 10, 12, 10, 0), {}, "test"), "test")
        self.assertEqual(decide(self.utc(2026, 10, 12, 10, 0), {}, "commands"), "commands")

    def test_matin_can_be_reenabled_and_windows_configured(self):
        cfg = {"briefings": {"matin": ["07:40", "10:00"]}}
        self.assertEqual(decide(self.utc(2026, 10, 12, 6, 0), {}, cfg=cfg), "matin")                    # 8h Paris
        self.assertEqual(decide(self.utc(2026, 10, 12, 7, 30), {"matin": "2026-10-12"}, cfg=cfg), "watch")   # 9h30, déjà envoyé
        cfg = {"briefings": {"ouverture": ["16:00", "18:00"]}}
        self.assertEqual(decide(self.utc(2026, 10, 12, 13, 50), {}, cfg=cfg), "watch")                  # 15h50 < 16h
        self.assertEqual(decide(self.utc(2026, 10, 12, 14, 5), {}, cfg=cfg), "ouverture")


class TestMisc(unittest.TestCase):
    def test_chunk(self):
        text = "\n\n".join("x" * 1000 for _ in range(10))
        parts = chunk(text)
        self.assertTrue(all(len(p) <= 3800 for p in parts))
        self.assertEqual(sum(len(p.replace("\n", "")) for p in parts), 10000)
        self.assertTrue(all(len(p) <= 3800 for p in chunk("y" * 9000)))

    def test_resolve(self):
        self.assertEqual(resolve(CFG, "nvidia")["ticker"], "NVDA")
        self.assertEqual(resolve(CFG, "or")["ticker"], "GC=F")
        self.assertEqual(resolve(CFG, "tsla")["ticker"], "TSLA")
        self.assertIsNone(resolve(CFG, "pas un ticker !!"))

    def test_parse_args(self):
        self.assertEqual(commands.parse_args(["nvidia", "142,5", "10", "pullback"]), ("nvidia", 142.5, 10.0, "pullback"))
        self.assertEqual(commands.parse_args(["nvidia", "long"]), ("nvidia", None, None, "long"))

    def test_live_stats(self):
        now = dt.datetime(2026, 10, 12, 15, 0, tzinfo=UTC)       # 11h New York
        idx = pd.date_range("2026-10-09 19:00", "2026-10-12 15:00", freq="5min", tz="UTC")
        idx = idx[(idx.hour * 60 + idx.minute >= 13 * 60 + 30) & (idx.hour < 20) & (idx.weekday < 5)]
        c = pd.Series(100.0, index=idx)
        c[idx >= pd.Timestamp("2026-10-12 13:30", tz="UTC")] = 105.0
        df = pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1.0})
        st = data.live_stats(df, "NVDA", now)
        self.assertAlmostEqual(st["day"], 0.05, places=6)
        self.assertLess(st["age_min"], 10)

    def test_completed_drops_unfinished_day(self):
        df = synth(n=300, end="2026-10-12")
        now = dt.datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
        self.assertEqual(df.index[-1].date(), dt.date(2026, 10, 12))
        self.assertEqual(len(data.completed(df, "NVDA", now)), 299)
        later = dt.datetime(2026, 10, 12, 21, 0, tzinfo=UTC)     # 17h NY : séance US terminée
        self.assertEqual(len(data.completed(df, "NVDA", later)), 300)


class TestFlows(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch.object(data, "daily", fake_daily),
                  mock.patch.object(data, "last_price", lambda t, now: 100.0),
                  mock.patch.object(F, "collect", lambda ctx: []),
                  mock.patch.object(A, "earnings_in_days", lambda s, t, n: 3 if t == "MSFT" else None),
                  mock.patch("bourse.news.fetch_many", lambda ts, now, key=None: {t: [{
                      "title": "SEC investigation into " + t, "src": "X", "url": "https://x.test/a", "ts": now.timestamp() - 600}]
                      for t in ts})]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def check_html(self, msgs):
        import re
        for m in msgs:
            self.assertLessEqual(len(m), 4096)
            for tag in ("b", "i", "a"):
                self.assertEqual(len(re.findall(rf"<{tag}[ >]", m)), len(re.findall(rf"</{tag}>", m)), m[:200])

    def test_matin_runs_and_is_well_formed(self):
        ctx = make_ctx()
        ctx.state["positions"]["NVDA"] = P.new_position(CFG, {"cat": "grande"}, 100.0, 10, "swing", "pullback",
                                                         compute(synth()), dt.date(2026, 10, 7))
        jobs.matin(ctx)
        text = "\n".join(ctx.tg.sent)
        self.assertIn("Pré-ouverture", text)
        self.assertIn("Tes positions", text)
        self.assertIn("Tendance de fond", text)
        self.assertIn("SEC investigation", text)
        self.assertEqual(ctx.state["slots"]["matin"], "2026-10-12")
        self.check_html(ctx.tg.sent)

    def test_commands_roundtrip(self):
        ctx = make_ctx(tg=FakeTG())
        for line in ["/achat nvidia 100 5 pullback", "/stop nvidia 96,5", "/statut", "/avis nvidia", "/idees",
                     "/vente nvidia 106", "/bilan", "/aide", "/n_importe_quoi", "/achat inconnu!!"]:
            commands.handle(ctx, line)
        self.assertEqual(ctx.state["positions"], {})
        j = ctx.state["journal"]
        self.assertEqual(len(j), 1)
        self.assertAlmostEqual(j[0]["pct"], 0.06)
        self.assertAlmostEqual(j[0]["eur"], 30.0)
        text = "\n".join(ctx.tg.sent)
        self.assertIn("Bilan swing", text)
        self.assertIn("Stop de", text)
        self.assertNotIn("Traceback", text)
        self.check_html(ctx.tg.sent)

    def test_unknown_chat_ignored_by_telegram_layer(self):
        tg = Telegram("t", "42")
        with mock.patch("requests.get") as g:
            g.return_value.json.return_value = {"result": [
                {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/statut"}},
                {"update_id": 6, "message": {"chat": {"id": 99}, "text": "/achat nvidia"}}]}
            self.assertEqual(tg.updates(0), [(5, "/statut"), (6, None)])

    def test_watch_alerts_and_dedup(self):
        now = dt.datetime(2026, 10, 12, 15, 10, tzinfo=UTC)          # 11h10 New York, 17h10 Paris
        idx = pd.date_range("2026-10-09 13:30", "2026-10-12 15:05", freq="5min", tz="UTC")
        idx = idx[(idx.weekday < 5) & (idx.hour * 60 + idx.minute >= 13 * 60 + 30) & (idx.hour < 20)]
        c = pd.Series(100.0, index=idx)
        c[idx >= pd.Timestamp("2026-10-12 13:30", tz="UTC")] = 91.0   # -9 % sur la journée
        c[idx >= pd.Timestamp("2026-10-12 14:30", tz="UTC")] = 92.0
        df = pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1.0})
        ctx = make_ctx(now=now)
        ctx.state["positions"]["NVDA"] = {"type": "swing", "entry": 100.0, "qty": 10, "date": "2026-10-09", "peak": 100.0,
                                           "stop": 95.0, "stop0": 95.0, "risk": 5.0, "target": 110.0, "sma5_exit": False,
                                           "max_days": 7, "setup": None}
        with mock.patch.object(data, "intraday", lambda ts, **k: {t: df for t in ts}):
            jobs.watch(ctx)
            first = "\n".join(ctx.tg.sent)
            self.assertIn("sur la journée", first)
            self.assertIn("stop", first.lower())
            self.assertIn("🛑", first)
            n = len(ctx.tg.sent)
            jobs.watch(ctx)                                         # même situation : aucune répétition
            self.assertEqual(len(ctx.tg.sent), n)
        self.check_html(ctx.tg.sent)

    def test_trailing_stop_and_target(self):
        w = {"ticker": "NVDA", "nom": "Nvidia", "cat": "grande"}
        pos = {"type": "swing", "entry": 100.0, "qty": None, "date": "2026-10-09", "peak": 100.0, "stop": 95.0, "stop0": 95.0,
               "risk": 5.0, "target": 110.0, "sma5_exit": False, "max_days": 7}
        ev = P.intraday_events(w, pos, 105.5, dt.date(2026, 10, 12), CFG)
        self.assertEqual(pos["stop"], 100.0)
        self.assertEqual(ev[0][0], "trail1")
        ev = P.intraday_events(w, pos, 110.5, dt.date(2026, 10, 12), CFG)
        self.assertEqual(ev[0][0], "target")

    def test_soir_always_sends_a_bilan(self):
        ctx = make_ctx(now=dt.datetime(2026, 10, 12, 20, 40, tzinfo=UTC))
        with mock.patch.object(A, "find_ideas", lambda *a, **k: []):
            jobs.soir(ctx)
        text = "\n".join(ctx.tg.sent)
        self.assertIn("Synthèse · clôture", text)
        self.assertIn("Tendances de fond", text)
        self.assertNotIn("Changements de tendance", text)          # 1re observation : silencieuse
        self.assertEqual(ctx.state["slots"]["soir"], "2026-10-12")
        self.check_html(ctx.tg.sent)


class TestIdeas(unittest.TestCase):
    def test_one_idea_per_ticker_and_validated_first(self):
        df = pullback_df()
        w = {"ticker": "NVDA", "nom": "Nvidia", "cat": "grande"}
        w2 = {"ticker": "AAPL", "nom": "Apple", "cat": "grande"}
        snap = {"NVDA": {"w": w, "ind": compute(df)}, "AAPL": {"w": w2, "ind": compute(df)}}
        stats = {"setups": {"capitulation": {"grande": {"n": 300, "win": .6, "mean": .01, "base": 0, "validated": True}}}}
        state = ST.load(pathlib.Path("/nonexistent"))
        with mock.patch.object(A, "earnings_in_days", lambda *a: None):
            ideas = A.find_ideas(snap, CFG, stats, state, dt.datetime(2026, 10, 12, tzinfo=UTC))
        self.assertEqual(sorted(i["w"]["ticker"] for i in ideas), ["AAPL", "NVDA"])
        self.assertTrue(all(i["validated"] for i in ideas))          # le setup validé passe devant
        self.assertTrue(all(i["setup"] == "capitulation" for i in ideas) or all("also" in i for i in ideas))

    def test_no_cross_category_validation(self):
        stats = {"setups": {"pullback": {"all": {"n": 900, "validated": True, "win": .6, "mean": .01, "base": 0},
                                         "grande": {"n": 800, "validated": True, "win": .6, "mean": .01, "base": 0}}}}
        self.assertIsNotNone(A.stat_for(stats, "pullback", "grande"))
        self.assertIsNone(A.stat_for(stats, "pullback", "pari"))
        self.assertIsNone(A.stat_for({"setups": {"pullback": {"grande": {"n": 99}}}}, "pullback", "grande"))

    def test_earnings_in_window_demotes_idea(self):
        w = {"ticker": "NVDA", "nom": "Nvidia", "cat": "grande"}
        snap = {"NVDA": {"w": w, "ind": compute(pullback_df())}}
        stats = {"setups": {"pullback": {"grande": {"n": 300, "win": .6, "mean": .01, "base": 0, "validated": True}}}}
        state = ST.load(pathlib.Path("/nonexistent"))
        with mock.patch.object(A, "earnings_in_days", lambda *a: 2):
            ideas = A.find_ideas(snap, CFG, stats, state, dt.datetime(2026, 10, 12, tzinfo=UTC))
        self.assertFalse(ideas[0]["validated"])
        self.assertIn("résultats", R.idea_block(ideas[0]))


class TestBacktest(unittest.TestCase):
    def test_end_to_end_on_synthetic(self):
        with mock.patch.object(data, "daily", fake_daily):
            st = backtest.run(CFG, dt.datetime(2026, 10, 11, tzinfo=UTC))
        self.assertGreater(st["n_tickers"], 30)
        self.assertIn("pullback", st["setups"])
        s = st["setups"]["pullback"]["all"]
        self.assertGreater(s["n"], 100)
        self.assertTrue(0 <= s["win"] <= 1)
        # sur des marchés aléatoires (sans edge), le backtest ne doit PAS tout valider
        validated = [(n, c) for n, d in st["setups"].items() for c, s in d.items() if s["validated"]]
        self.assertLess(len(validated), 6, validated)
        self.assertIn("Backtest", backtest.report_md(st))
        self.assertIn("Backtest", backtest.telegram_summary(st))
        json.dumps(st)                                                # sérialisable pour stats.json


class TestNews(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 12, 14, 0, tzinfo=UTC)

    def state(self):
        return {"news_seen": {}, "news_sigs": {}}

    def item(self, title):
        return {"title": title, "src": "Yahoo", "url": "https://x.test", "ts": self.NOW.timestamp() - 300}

    def test_same_story_not_repeated_under_another_title_or_ticker(self):
        from bourse import news as N
        st = self.state()
        nv, am = {"ticker": "NVDA", "nom": "Nvidia", "alias": []}, {"ticker": "AMZN", "nom": "Amazon", "alias": []}
        nv["alias"] = ["amazon"]        # cas réel : l'article est rattaché à Nvidia et cite Amazon
        a = N.sensitive([self.item("Amazon in talks to acquire AI startup Decart for $7 billion")], self.NOW, st, 4, entry=nv)
        self.assertEqual(len(a), 1)
        b = N.sensitive([self.item("Amazon in $7 billion deal talks to acquire AI startup Decart")], self.NOW, st, 4)
        self.assertEqual(b, [])                                       # même histoire, autre titre : pas renvoyée
        c = N.sensitive([self.item("Amazon in $7 billion deal talks to acquire AI startup Decart")], self.NOW, st, 4, entry=am)
        self.assertEqual(c, [])                                       # ni sous une autre valeur

    def test_template_headlines_about_different_companies_are_both_kept(self):
        from bourse import news as N
        st = self.state()
        nv, ms = {"ticker": "NVDA", "nom": "Nvidia", "alias": []}, {"ticker": "MSFT", "nom": "Microsoft", "alias": []}
        a = N.sensitive([self.item("Nvidia stock falls after SEC investigation")], self.NOW, st, 4, entry=nv)
        b = N.sensitive([self.item("Microsoft stock falls after SEC investigation")], self.NOW, st, 4, entry=ms)
        self.assertEqual((len(a), len(b)), (1, 1))

    def test_relevance_filter(self):
        from bourse import news as N
        ms = {"ticker": "MSFT", "nom": "Microsoft", "alias": ["microsoft"]}
        self.assertFalse(N.mentions("Spotify upgraded, Alibaba launched: top Wall Street analysts call", ms))
        self.assertTrue(N.mentions("Barclays downgrades Microsoft and two software stocks", ms))
        self.assertTrue(N.mentions("MSFT jumps on cloud deal", ms))
        em = {"ticker": "PAEEM.PA", "nom": "MSCI Emerging Markets (PEA)", "alias": ["emergents", "em"]}
        self.assertFalse(N.mentions("The system", em))                # l'alias « em » (2 lettres) ne doit pas matcher
        st = self.state()
        out = N.sensitive([self.item("Spotify upgraded, Alibaba launched: analysts call"),
                           self.item("Barclays downgrades Microsoft ahead of Q3 results")], self.NOW, st, 4, entry=ms)
        self.assertEqual(len(out), 1)
        self.assertIn("Microsoft", out[0]["title"])

    def test_preview_does_not_consume_news(self):
        from bourse import news as N
        st = self.state()
        N.sensitive([self.item("Barclays downgrades Microsoft")], self.NOW, st, 4, mark=False)
        self.assertEqual((st["news_seen"], st["news_sigs"]), ({}, {}))


class TestOuverture(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 12, 13, 50, tzinfo=UTC)       # 9h50 à New York, 15h50 à Paris

    def setUp(self):
        self.p = [mock.patch.object(data, "daily", fake_daily),
                  mock.patch.object(F, "collect", lambda ctx: []),
                  mock.patch.object(A, "earnings_in_days", lambda *a: None),
                  mock.patch("bourse.news.fetch_many", lambda ts, now, key=None: {t: [{
                      "title": f"Barclays downgrades {t} ahead of results", "src": "Y", "url": "https://x.test/a",
                      "ts": now.timestamp() - 600}] for t in ts})]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def intraday(self, today_close=103.0):
        idx = pd.DatetimeIndex([pd.Timestamp("2026-10-09 19:55", tz="UTC")] +
                               list(pd.date_range("2026-10-12 13:30", "2026-10-12 13:50", freq="5min", tz="UTC")))
        c = pd.Series([100.0] + [today_close] * (len(idx) - 1), index=idx)
        return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1.0})

    def position(self):
        return {"type": "swing", "entry": 100.0, "qty": 10, "date": "2026-10-09", "peak": 100.0, "stop": 95.0,
                "stop0": 95.0, "risk": 5.0, "target": 110.0, "sma5_exit": False, "max_days": 7, "setup": None}

    def test_briefing_after_open_is_complete_and_well_formed(self):
        import re
        ctx = make_ctx(now=self.NOW)
        ctx.state["positions"]["NVDA"] = self.position()
        df = self.intraday()
        with mock.patch.object(data, "intraday", lambda ts, **k: {t: df for t in ts}):
            jobs.ouverture(ctx)
        text = "\n".join(ctx.tg.sent)
        self.assertIn("Synthèse · après l'ouverture", text)
        self.assertIn("Tes positions", text)
        self.assertIn("Gros changements", text)
        self.assertIn("+3,0 %", text)                                # 100 -> 103 depuis la clôture de vendredi
        self.assertIn("Actus à retenir", text)
        self.assertNotIn("Setups swing", text)                       # l'entrée « à l'ouverture » est passée
        self.assertEqual(ctx.state["slots"]["ouverture"], "2026-10-12")
        for m in ctx.tg.sent:
            self.assertLessEqual(len(m), 4096)
            for tag in ("b", "i", "a"):
                self.assertEqual(len(re.findall(rf"<{tag}[ >]", m)), len(re.findall(rf"</{tag}>", m)))

    def test_no_live_prices_warns_and_retries(self):
        ctx = make_ctx(now=self.NOW)
        with mock.patch.object(data, "intraday", lambda ts, **k: {}):
            jobs.ouverture(ctx)
        self.assertIn("Aucun cours", "\n".join(ctx.tg.sent))
        self.assertNotIn("ouverture", ctx.state["slots"])             # pas marqué : nouvel essai au prochain passage

    def test_stop_hit_in_briefing_is_not_repeated_by_watch(self):
        ctx = make_ctx(now=self.NOW)
        ctx.state["positions"]["NVDA"] = self.position()
        df = self.intraday(today_close=94.0)                         # sous le stop à 95
        with mock.patch.object(data, "intraday", lambda ts, **k: {t: df for t in ts}):
            jobs.ouverture(ctx)
            self.assertIn("🛑", "\n".join(ctx.tg.sent))
            n = len(ctx.tg.sent)
            jobs.watch(ctx)
            self.assertFalse(any("🛑" in m for m in ctx.tg.sent[n:]))


TOPICS = ["cloud", "chips", "advertising", "gas", "bonds", "mining", "solar", "retail", "autos", "media", "banking",
          "pharma", "railway", "steel", "telecom"]


class TestSynthese(unittest.TestCase):
    """Aperçu global : même structure à chaque fois, un seul message, rien de consommé par /synthese."""
    SECTIONS = ["🌍 Marché", "⚡ Gros changements", "📰 Actus à retenir", "📈 Tendances de fond"]

    def setUp(self):
        self.p = [mock.patch.object(data, "daily", fake_daily),
                  mock.patch.object(F, "collect", lambda ctx: []),
                  mock.patch.object(A, "earnings_in_days", lambda *a: None),
                  mock.patch("bourse.news.fetch_many", lambda ts, now, key=None: {t: [{
                      "title": f"Barclays downgrades {t} on {TOPICS[i % len(TOPICS)]} outlook", "src": "Y",
                      "url": "https://x.test/a", "ts": now.timestamp() - 600}] for i, t in enumerate(ts)})]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def check_order(self, text, sections):
        pos = [text.index(sec) for sec in sections]
        self.assertEqual(pos, sorted(pos), "sections dans le désordre")

    def test_soir_is_one_synthesis_plus_one_detail_message(self):
        ctx = make_ctx(now=dt.datetime(2026, 10, 12, 20, 40, tzinfo=UTC))
        ctx.state["positions"]["NVDA"] = P.new_position(CFG, {"cat": "grande"}, 100.0, 10, "swing", "pullback",
                                                         compute(synth()), dt.date(2026, 10, 7))
        with mock.patch.object(A, "find_ideas", lambda *a, **k: []):
            jobs.soir(ctx)
        main = ctx.tg.sent[0]
        self.check_order(main, self.SECTIONS[:1] + ["💼 Tes positions"] + self.SECTIONS[1:])
        self.assertEqual(ctx.state["slots"]["soir"], "2026-10-12")

    def test_news_capped_ranked_and_held_first(self):
        ctx = make_ctx(now=dt.datetime(2026, 10, 12, 13, 50, tzinfo=UTC))
        ctx.state["positions"]["MSFT"] = TestOuverture.position(None)
        df = TestOuverture.intraday(None)
        with mock.patch.object(data, "intraday", lambda ts, **k: {t: df for t in ts}):
            text = synthese_compose(ctx, "instant")
        news = text[text.index("📰 Actus à retenir"):text.index("📈 Tendances")]
        self.assertLessEqual(news.count("Barclays"), 6)
        self.assertIn("autre(s) titre(s)", news)
        self.assertIn("💼 Microsoft", news.split("\n")[1], "la position doit passer en premier")

    def test_flashed_news_appear_once_in_the_recap(self):
        ctx = make_ctx(now=dt.datetime(2026, 10, 12, 13, 50, tzinfo=UTC))
        F.pick(ctx, [{"title": "Fed cuts interest rates by 50 basis points", "src": "CNBC", "url": "https://x.test/f",
                      "ts": ctx.now.timestamp() - 600, "bonus": 0}])
        df = TestOuverture.intraday(None)
        with mock.patch.object(data, "intraday", lambda ts, **k: {t: df for t in ts}):
            text = synthese_compose(ctx, "instant")
        news = text[text.index("📰 Actus à retenir"):text.index("📈 Tendances")]
        self.assertEqual(news.count("Fed cuts"), 1)
        self.assertIn("Marché", news)

    def test_instant_does_not_consume_news_or_slots(self):
        ctx = make_ctx(now=dt.datetime(2026, 10, 12, 13, 50, tzinfo=UTC))
        df = TestOuverture.intraday(None)
        with mock.patch.object(data, "intraday", lambda ts, **k: {t: df for t in ts}):
            commands.handle(ctx, "/synthese")
        self.assertEqual(ctx.state["news_seen"], {})
        self.assertEqual(ctx.state["slots"], {})
        self.assertIn("Synthèse · à l'instant", ctx.tg.sent[0])

    def test_synthesis_survives_yahoo_failures(self):
        ctx = make_ctx(now=dt.datetime(2026, 10, 12, 20, 40, tzinfo=UTC))
        snap = A.snapshot(jobs.tracked_entries(ctx), ctx.now)
        with mock.patch("bourse.synthese.indices", side_effect=RuntimeError("yahoo")), \
                mock.patch("bourse.news.fetch_many", side_effect=RuntimeError("yahoo")):
            text = synthese_compose(ctx, "soir", snap=snap)
        self.assertIn("Indices indisponibles", text)
        self.assertIn("Rien de notable", text)

    def test_alerts_have_a_header_and_point_to_synthese(self):
        ctx = make_ctx(now=dt.datetime(2026, 10, 12, 13, 50, tzinfo=UTC))
        df = TestOuverture.intraday(None, today_close=91.0)             # -9 % sur la journée
        with mock.patch.object(data, "intraday", lambda ts, **k: {t: df for t in ts}):
            jobs.watch(ctx)
        self.assertTrue(ctx.tg.sent and ctx.tg.sent[0].startswith("🚨"))
        self.assertIn("/synthese", ctx.tg.sent[0])


class TestFlash(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 12, 14, 0, tzinfo=UTC)

    def item(self, title, bonus=0, age=300, src="CNBC"):
        return {"title": title, "src": src, "url": "https://x.test/" + str(abs(hash(title)) % 999),
                "ts": self.NOW.timestamp() - age, "bonus": bonus}

    def test_scores_big_news_without_any_watchlist_link(self):
        for title in ("Fed cuts interest rates by 50 basis points", "Oil surges as OPEC announces surprise output cut",
                      "U.S. government shutdown looms as debt ceiling talks collapse", "Regional bank failure sparks sell-off",
                      "Amazon in talks to acquire AI startup Decart for $7 billion", "Ceasefire collapses, missile strikes resume"):
            self.assertGreaterEqual(F.score(title), 4, title)

    def test_scores_filter_out_noise(self):
        for title in ("Spotify upgraded, Alibaba launched: top Wall Street analysts call", "5 stocks to buy now, says Motley Fool",
                      "Here's why Tesla stock could rise next year", "Federal Reserve Board announces approval of application by Bank X"):
            self.assertLess(F.score(title), 4, title)

    def test_pick_dedupes_across_sources_and_never_repeats(self):
        ctx = make_ctx(now=self.NOW)
        items = [self.item("Fed cuts interest rates by 50 basis points", src="CNBC"),
                 self.item("Federal Reserve cuts interest rates by 50 basis points", src="Yahoo", age=200),
                 self.item("Gold hits record high as inflation fears rise", src="MarketWatch")]
        self.assertEqual(len(F.pick(ctx, items)), 2)                     # la baisse de taux n'apparaît qu'une fois
        self.assertEqual(F.pick(ctx, items), [])                         # rien n'est renvoyé au passage suivant
        self.assertEqual(len(ctx.state["flash_log"]), 2)

    def test_old_items_ignored_and_positions_boost_score(self):
        ctx = make_ctx(now=self.NOW)
        self.assertEqual(F.pick(ctx, [self.item("Fed cuts interest rates by 50 basis points", age=5 * 3600)]), [])
        ctx.state["positions"]["NVDA"] = {"type": "long", "entry": 100.0, "date": "2026-10-09"}
        out = F.pick(ctx, [self.item("Nvidia unveils new data center chip")])     # 0 point seul, +1 grand nom, +3 position
        self.assertEqual((len(out), out[0]["touch"]), (1, "held"))

    def test_parse_rss_and_atom_and_reject_entity_bombs(self):
        rss = (b'<?xml version="1.0"?><rss><channel><item><title>Fed holds rates</title><link>https://a.test/1</link>'
               b'<pubDate>Mon, 12 Oct 2026 13:55:00 GMT</pubDate></item></channel></rss>')
        items = F.parse_feed(rss, "CNBC", 1, self.NOW)
        self.assertEqual((items[0]["title"], items[0]["url"], items[0]["bonus"]), ("Fed holds rates", "https://a.test/1", 1))
        self.assertAlmostEqual(items[0]["ts"], dt.datetime(2026, 10, 12, 13, 55, tzinfo=UTC).timestamp())
        atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Bank failure</title><link href="https://b.test"/>'
                b'<updated>2026-10-12T13:50:00Z</updated></entry></feed>')
        self.assertEqual(F.parse_feed(atom, "BCE", 0, self.NOW)[0]["url"], "https://b.test")
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
        self.assertEqual(F.parse_feed(bomb, "X", 0, self.NOW), [])
        self.assertEqual(F.parse_feed(b"pas du xml", "X", 0, self.NOW), [])

    def test_flash_job_sends_one_message_capped_and_survives_feed_failures(self):
        ctx = make_ctx(now=self.NOW)
        titles = ["Fed cuts interest rates by 50 basis points", "Oil surges after OPEC output cut", "Regional bank failure sparks sell-off",
                  "Tariffs on all imports announced by White House", "Ceasefire collapses, missile strikes resume",
                  "Amazon in talks to acquire AI startup Decart for $7 billion", "Bitcoin plunges 12% in an hour"]
        with mock.patch.object(F, "collect", lambda c: [self.item(t) for t in titles]):
            jobs.flash(ctx)
            self.assertEqual(len(ctx.tg.sent), 1)
            self.assertIn("Flash marché", ctx.tg.sent[0])
            self.assertEqual(ctx.tg.sent[0].count("<a href"), 5)             # 5 par message au plus
            self.assertIn("autre(s) info(s)", ctx.tg.sent[0])
            jobs.flash(ctx)
            self.assertEqual(len(ctx.tg.sent), 1)                            # rien de nouveau : silence
        with mock.patch.object(F, "collect", lambda c: []):
            jobs.flash(ctx)
            self.assertEqual(len(ctx.tg.sent), 1)

    def test_recent_flash_feeds_the_synthesis_recap(self):
        ctx = make_ctx(now=self.NOW)
        F.pick(ctx, [self.item("Fed cuts interest rates by 50 basis points")])
        self.assertEqual(len(F.recent(ctx, 10)), 1)
        self.assertEqual(F.recent(ctx, 0), [])

    def test_daemon_mode_selection(self):
        import run
        self.assertEqual(run.next_mode("ouverture", {}, 1000), "ouverture")
        self.assertEqual(run.next_mode("watch", {}, 1000), "watch")
        self.assertEqual(run.next_mode("watch", {"watch": 990, "flash": 990}, 1000), "commands")
        self.assertEqual(run.next_mode("watch", {"watch": 990, "flash": 930}, 1000), "flash")
        self.assertEqual(run.next_mode("flash", {"flash": 990}, 1000), "commands")
        self.assertEqual(run.next_mode("skip", {}, 1000), "commands")


class TestWebhook(unittest.TestCase):
    def test_webhook_update_parsing(self):
        import os
        import run
        with mock.patch.dict(os.environ, {"TG_UPDATE": json.dumps({"update_id": 5, "message": {"chat": {"id": 1}, "text": "/aide"}})}):
            self.assertEqual(run.webhook_update("1"), [(5, "/aide")])
            self.assertEqual(run.webhook_update("2"), [(5, None)])    # autre chat : ignoré
        for raw in ("", "null", "pas du json"):
            with mock.patch.dict(os.environ, {"TG_UPDATE": raw}):
                self.assertEqual(run.webhook_update("1"), [])


if __name__ == "__main__":
    unittest.main()
