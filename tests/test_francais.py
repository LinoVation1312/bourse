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
