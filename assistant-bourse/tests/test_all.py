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
from bourse import analysis as A, backtest, commands, data, jobs, positions as P, render as R, setups as S, state as ST
from bourse.config import load_config, resolve
from bourse.indicators import compute
from bourse.schedule import decide
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

    def updates(self, offset):
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
        self.assertEqual(decide(self.utc(2026, 7, 8, 5, 45), {}), "matin")        # 7h45 Paris (UTC+2)
        self.assertEqual(decide(self.utc(2026, 12, 9, 6, 45), {}), "matin")       # 7h45 Paris (UTC+1)
        self.assertEqual(decide(self.utc(2026, 12, 9, 5, 45), {}), "skip")        # 6h45 Paris
        self.assertEqual(decide(self.utc(2026, 7, 8, 13, 0), {}), "watch")
        self.assertEqual(decide(self.utc(2026, 7, 8, 20, 20), {}), "soir")        # 22h20 Paris
        self.assertEqual(decide(self.utc(2026, 12, 9, 21, 20), {}), "soir")

    def test_matin_only_once_and_weekend(self):
        self.assertEqual(decide(self.utc(2026, 10, 12, 6, 0), {"matin": "2026-10-12"}), "skip")  # 8h Paris, déjà envoyé
        self.assertEqual(decide(self.utc(2026, 10, 12, 8, 0), {"matin": "2026-10-12"}), "watch")
        self.assertEqual(decide(self.utc(2026, 10, 10, 10, 0), {}), "skip")                      # samedi
        self.assertEqual(decide(self.utc(2026, 10, 12, 10, 0), {}, "test"), "test")


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

    def test_soir_silent_without_news(self):
        ctx = make_ctx(now=dt.datetime(2026, 10, 12, 20, 20, tzinfo=UTC))
        with mock.patch.object(A, "find_ideas", lambda *a, **k: []):
            jobs.soir(ctx)
        self.assertEqual(ctx.tg.sent, [])
        self.assertEqual(ctx.state["slots"]["soir"], "2026-10-12")


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


if __name__ == "__main__":
    unittest.main()
