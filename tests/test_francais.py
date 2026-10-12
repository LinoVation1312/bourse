"""Notifications en français (titres traduits) et dossier workflows."""
import unittest
from pathlib import Path
from unittest import mock

import requests

from bourse import render as R, translate

ROOT = Path(__file__).resolve().parent.parent


class FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return [[["Nvidia ouvre une enquête", "Nvidia opens probe", None, None], [".", ".", None, None]], None, "en"]


class TestTraduction(unittest.TestCase):
    def setUp(self):
        translate._cache.clear()
        translate._broken = False

    def test_titre_traduit(self):
        with mock.patch.dict("os.environ", {}, clear=False), mock.patch("requests.get", return_value=FakeResp()):
            self.assertEqual(translate.fr("Nvidia opens probe"), "Nvidia ouvre une enquête.")

    def test_repli_si_reseau_indisponible(self):
        with mock.patch("requests.get", side_effect=requests.ConnectionError()) as g:
            self.assertEqual(translate.fr("Nvidia opens probe"), "Nvidia opens probe")
            self.assertEqual(translate.fr("Autre titre"), "Autre titre")
            self.assertEqual(g.call_count, 1)          # pas de nouvel essai après un échec

    def test_desactivable(self):
        with mock.patch.dict("os.environ", {"BOURSE_NO_TRANSLATE": "1"}), mock.patch("requests.get") as g:
            self.assertEqual(translate.fr("Nvidia opens probe"), "Nvidia opens probe")
            g.assert_not_called()

    def test_news_lines_en_francais(self):
        items = {"NVDA": [{"title": "Nvidia opens probe", "kind": "neg", "src": "Reuters", "url": "https://x.test/a"}]}
        with mock.patch("requests.get", return_value=FakeResp()):
            line = R.news_lines(items, {"NVDA": "Nvidia"})[0]
        self.assertIn("enquête", line)
        self.assertIn("Reuters", line)


class TestWorkflows(unittest.TestCase):
    def test_dossier_workflows_identique_a_github(self):
        a = (ROOT / "workflows" / "bot.yml").read_text(encoding="utf-8")
        b = (ROOT / ".github" / "workflows" / "bot.yml").read_text(encoding="utf-8")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- agenda : « attention X demain », bilans
import datetime as dt
import pandas as pd
from bourse import agenda, jobs, state as ST
from bourse.indicators import compute
from test_all import CFG, FakeTG, make_ctx, synth

UTC = dt.timezone.utc


class TestAgenda(unittest.TestCase):
    def ctx(self, now=dt.datetime(2026, 10, 12, 20, 45, tzinfo=UTC)):
        return make_ctx(now=now)

    def test_resultats_demain_et_evenement_saisi(self):
        c = self.ctx()
        c.cfg = {**CFG, "evenements": [{"ticker": "SPCX", "date": "2026-10-13", "texte": "levée de blocage"},
                                       {"date": "2026-10-14", "texte": "réunion de la Fed"}]}
        tomorrow = (c.now.date() + dt.timedelta(days=1)).isoformat()
        c.state["earn"]["QBTS"] = {"fetched": str(c.now.date()), "date": tomorrow}
        c.state["positions"]["QBTS"] = {"type": "swing"}
        with mock.patch.object(agenda, "dividend_in_days", return_value=None):
            txt = agenda.upcoming(c, jobs.tracked_entries(c), 2, "⏰ Attention")
        self.assertIn("D-Wave Quantum", txt)
        self.assertIn("résultats demain", txt)
        self.assertIn("tu détiens", txt)
        self.assertIn("levée de blocage demain", txt)
        self.assertIn("réunion de la Fed dans 2 j", txt)

    def test_rien_a_signaler(self):
        c = self.ctx()
        with mock.patch.object(agenda, "dividend_in_days", return_value=None), \
                mock.patch.object(agenda.A, "earnings_in_days", return_value=None):
            self.assertIsNone(agenda.upcoming(c, jobs.tracked_entries(c), 2, "x"))

    def test_dividende(self):
        c = self.ctx()
        with mock.patch.object(agenda, "dividend_in_days", side_effect=lambda s, t, n: 1 if t == "ENGI.PA" else None), \
                mock.patch.object(agenda.A, "earnings_in_days", return_value=None):
            txt = agenda.upcoming(c, jobs.tracked_entries(c), 2, "x")
        self.assertIn("Engie", txt)
        self.assertIn("dividende demain", txt)

    def test_seance_inhabituelle(self):
        df = synth(300)
        df.iloc[-1, df.columns.get_loc("Volume")] = df.Volume.iloc[-21:-1].mean() * 5
        ind = compute(df)
        w = {"ticker": "NVDA", "nom": "Nvidia", "cat": "grande"}
        txt = agenda.unusual({"NVDA": {"w": w, "ind": ind}}, [w], CFG, ind.index[-1].date())
        self.assertIn("volume ×4", txt)
        self.assertIsNone(agenda.unusual({"NVDA": {"w": w, "ind": ind}}, [w], CFG, dt.date(2030, 1, 1)))

    def test_derniere_seance_semaine_et_mois(self):
        self.assertTrue(agenda.last_business_day(dt.date(2026, 10, 9), "semaine"))      # vendredi
        self.assertFalse(agenda.last_business_day(dt.date(2026, 10, 8), "semaine"))
        self.assertTrue(agenda.last_business_day(dt.date(2026, 10, 30), "mois"))        # vendredi 30 octobre
        self.assertFalse(agenda.last_business_day(dt.date(2026, 10, 29), "mois"))
        self.assertTrue(agenda.last_business_day(dt.date(2026, 7, 31), "mois"))
        self.assertFalse(agenda.last_business_day(dt.date(2026, 10, 10), "semaine"))    # samedi

    def test_bilan_semaine(self):
        ind = compute(synth(300, end="2026-10-09"))
        w = {"ticker": "NVDA", "nom": "Nvidia", "cat": "grande"}
        st = {"trend": {"NVDA": {"state": "down", "since": "2026-10-07"}}}
        txt = agenda.recap({"NVDA": {"w": w, "ind": ind}}, [w], CFG, st, dt.date(2026, 10, 9), "semaine")
        self.assertIn("Bilan de la semaine", txt)
        self.assertIn("Nvidia", txt)
        self.assertIn("baissière", txt)

    def test_soir_vendredi_contient_les_bilans(self):
        c = make_ctx(now=dt.datetime(2026, 10, 30, 21, 45, tzinfo=UTC))      # vendredi 30 oct., fin de mois
        snap = {w["ticker"]: {"w": w, "ind": compute(synth(300, seed=i, end="2026-10-30"))}
                for i, w in enumerate(CFG["watchlist"])}
        with mock.patch.object(jobs.A, "snapshot", return_value=snap), \
                mock.patch.object(agenda, "dividend_in_days", return_value=None), \
                mock.patch.object(agenda.A, "earnings_in_days", return_value=None), \
                mock.patch.object(jobs.A, "find_ideas", return_value=[]):
            jobs.soir(c)
        txt = "\n".join(c.tg.sent)
        self.assertIn("Bilan de la semaine", txt)
        self.assertIn("Bilan du mois", txt)


class TestUnivers(unittest.TestCase):
    def _run(self, flag):
        c = make_ctx(now=dt.datetime(2026, 10, 12, 20, 45, tzinfo=UTC))
        c.cfg = {**CFG, "alertes_univers": flag}
        tomorrow = (c.now.date() + dt.timedelta(days=1)).isoformat()
        c.state["earn"]["TSLA"] = {"fetched": str(c.now.date()), "date": tomorrow}     # Tesla : univers, pas watchlist
        with mock.patch.object(agenda, "dividend_in_days", return_value=None), \
                mock.patch.object(agenda.A, "earnings_in_days",
                                  side_effect=lambda s, t, n: 1 if t == "TSLA" else None):
            return agenda.upcoming(c, jobs.tracked_entries(c), 2, "x")

    def test_valeur_de_l_univers_prevenue(self):
        self.assertIn("résultats demain", self._run(True))

    def test_desactivable(self):
        self.assertIsNone(self._run(False))
