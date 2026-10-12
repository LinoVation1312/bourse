# Backtest swing — 2026-10-12
43 valeurs, 10y d'historique, signal à la clôture, entrée à l'ouverture suivante, frais et écarts inclus (voir `setups.py`).

**Limites** : les valeurs de ta watchlist et les grandes capitalisations actuelles ont été choisies avec le recul (biais de survie) ; les résultats passés ne garantissent rien ; la référence « sans signal » utilise les mêmes sorties.

## Repli en tendance haussière
_RSI 2 j < 10 et clôture sous sa moyenne 5 j, dans une tendance haussière (au-dessus des moyennes 50 et 200 j)_

| Catégorie | Trades | Gagnants | Moyen net | Sans signal | Avantage (t) | Années + | 3 dernières années | Stops | Validé |
|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 321 | 58 % | +0,00 % | −0,15 % | +0,16 % (t=1.6) | 60 % (10 ans) | +0,15 % (n=157) | 26 % | ❌ |
| grandes valeurs | 905 | 59 % | +0,13 % | −0,17 % | +0,30 % (t=2.3) | 60 % (10 ans) | +0,13 % (n=288) | 23 % | ❌ |
| paris | 137 | 53 % | −0,28 % | −0,62 % | +0,34 % (t=0.4) | 57 % (7 ans) | −0,91 % (n=64) | 23 % | ❌ |
| toutes catégories | 1363 | 58 % | +0,06 % | −0,20 % | +0,26 % (t=2.1) | 40 % (10 ans) | +0,00 % (n=509) | 24 % | ❌ |

## Cassure avec volume
_plus haut de clôture sur 20 j avec un volume > 1,5x la normale, dans une tendance haussière_

| Catégorie | Trades | Gagnants | Moyen net | Sans signal | Avantage (t) | Années + | 3 dernières années | Stops | Validé |
|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 171 | 52 % | +0,24 % | +0,04 % | +0,20 % (t=1.0) | 70 % (10 ans) | +0,44 % (n=79) | 29 % | ❌ |
| grandes valeurs | 685 | 49 % | +0,30 % | +0,18 % | +0,12 % (t=0.6) | 60 % (10 ans) | +0,77 % (n=213) | 30 % | ❌ |
| paris | 183 | 48 % | +2,80 % | +0,40 % | +2,40 % (t=2.0) | 67 % (9 ans) | +4,28 % (n=99) | 33 % | ❌ |
| toutes catégories | 1039 | 49 % | +0,73 % | +0,16 % | +0,57 % (t=2.2) | 60 % (10 ans) | +1,59 % (n=391) | 30 % | ❌ |

## Rebond après chute brutale
_baisse sur 3 j <= −2,5 écarts-types (volatilité habituelle de la valeur) : rebond possible_

| Catégorie | Trades | Gagnants | Moyen net | Sans signal | Avantage (t) | Années + | 3 dernières années | Stops | Validé |
|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 251 | 60 % | +0,16 % | −0,12 % | +0,28 % (t=1.5) | 50 % (10 ans) | +1,15 % (n=78) | 26 % | ❌ |
| grandes valeurs | 660 | 55 % | −0,01 % | −0,11 % | +0,11 % (t=0.5) | 70 % (10 ans) | +1,04 % (n=209) | 24 % | ❌ |
| paris | 149 | 23 % | −0,38 % | −0,71 % | +0,33 % (t=0.5) | 43 % (7 ans) | −0,43 % (n=71) | 13 % | ❌ |
| toutes catégories | 1060 | 52 % | −0,02 % | −0,19 % | +0,17 % (t=1.0) | 60 % (10 ans) | +0,77 % (n=358) | 23 % | ❌ |


# Backtest tendance — 2026-10-12
43 valeurs, 10y d'historique, signal à la clôture, exécution à l'ouverture suivante, frais et écarts inclus. Chaque ligne = médiane des valeurs de la catégorie, comparée à « garder » (acheter et ne rien faire).

**Limites** : valeurs choisies avec le recul (biais de survie : garder est flatté) ; période surtout haussière ; les résultats passés ne garantissent rien ; la fiscalité n'est pas modélisée.

## Au-dessus de la moyenne 200 j
_acheter quand la clôture repasse au-dessus de sa moyenne 200 j, vendre quand elle passe 3 % en dessous_

| Catégorie | Valeurs | Rend./an règle | Rend./an garder | Baisse max règle | Baisse max garder | Investi | Trades/an | Moins de baisse (part des valeurs) | 3 dernières années (règle vs garder, baisse max) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 10 | +7,6 % | +13,7 % | −27 % | −32 % | 80 % | 1.3 | 70 % | +16 % vs +24 %, −19 % vs −21 % | ne protège pas assez des chutes |
| grandes valeurs | 25 | +13,1 % | +18,2 % | −40 % | −55 % | 74 % | 1.6 | 84 % | +16 % vs +25 %, −34 % vs −34 % | limite les chutes sans trop coûter |
| paris | 8 | +17,1 % | +13,8 % | −78 % | −89 % | 54 % | 2.5 | 100 % | +38 % vs +45 %, −67 % vs −77 % | ne protège pas assez des chutes |
| toutes catégories | 43 | +11,5 % | +14,6 % | −40 % | −53 % | 73 % | 1.6 | 84 % | +19 % vs +27 %, −34 % vs −34 % | ne protège pas assez des chutes |

## Moyennes 50 / 200 j
_détenir tant que la moyenne 50 j est au-dessus de la moyenne 200 j_

| Catégorie | Valeurs | Rend./an règle | Rend./an garder | Baisse max règle | Baisse max garder | Investi | Trades/an | Moins de baisse (part des valeurs) | 3 dernières années (règle vs garder, baisse max) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 10 | +8,4 % | +13,7 % | −31 % | −32 % | 75 % | 0.8 | 60 % | +20 % vs +24 %, −20 % vs −21 % | ne protège pas assez des chutes |
| grandes valeurs | 25 | +13,7 % | +18,2 % | −46 % | −55 % | 72 % | 0.8 | 84 % | +18 % vs +25 %, −33 % vs −34 % | ne protège pas assez des chutes |
| paris | 8 | +9,8 % | +13,8 % | −77 % | −89 % | 53 % | 0.8 | 100 % | +21 % vs +45 %, −63 % vs −77 % | ne protège pas assez des chutes |
| toutes catégories | 43 | +11,6 % | +14,6 % | −46 % | −53 % | 70 % | 0.8 | 81 % | +19 % vs +27 %, −30 % vs −34 % | ne protège pas assez des chutes |

## Cassure + stop suiveur
_acheter sur un plus haut de clôture sur 100 j au-dessus de la moyenne 200 j, vendre si la clôture passe sous (plus haut − k × ATR)_

| Catégorie | Valeurs | Rend./an règle | Rend./an garder | Baisse max règle | Baisse max garder | Investi | Trades/an | Moins de baisse (part des valeurs) | 3 dernières années (règle vs garder, baisse max) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 10 | +3,8 % | +13,7 % | −17 % | −32 % | 35 % | 2.9 | 100 % | +5 % vs +24 %, −12 % vs −21 % | limite les chutes mais rapporte nettement moins que garder |
| grandes valeurs | 25 | +5,3 % | +18,2 % | −33 % | −55 % | 36 % | 2.2 | 92 % | +6 % vs +25 %, −23 % vs −34 % | limite les chutes mais rapporte nettement moins que garder |
| paris | 8 | +19,8 % | +13,8 % | −68 % | −89 % | 24 % | 1.3 | 100 % | +37 % vs +45 %, −59 % vs −77 % | ne protège pas assez des chutes |
| toutes catégories | 43 | +5,3 % | +14,6 % | −33 % | −53 % | 34 % | 2.2 | 95 % | +6 % vs +27 %, −23 % vs −34 % | limite les chutes mais rapporte nettement moins que garder |

## Au-dessus de la moyenne 100 j
_comme la moyenne 200 j mais plus réactive : acheter au-dessus de la moyenne 100 j, vendre 3 % en dessous_

| Catégorie | Valeurs | Rend./an règle | Rend./an garder | Baisse max règle | Baisse max garder | Investi | Trades/an | Moins de baisse (part des valeurs) | 3 dernières années (règle vs garder, baisse max) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 10 | +10,2 % | +13,7 % | −27 % | −32 % | 79 % | 1.7 | 60 % | +19 % vs +24 %, −15 % vs −21 % | ne protège pas assez des chutes |
| grandes valeurs | 25 | +13,7 % | +18,2 % | −43 % | −55 % | 71 % | 2.7 | 84 % | +13 % vs +25 %, −39 % vs −34 % | ne protège pas assez des chutes |
| paris | 8 | +20,0 % | +13,8 % | −79 % | −89 % | 52 % | 4.2 | 88 % | +36 % vs +45 %, −75 % vs −77 % | ne protège pas assez des chutes |
| toutes catégories | 43 | +11,7 % | +14,6 % | −42 % | −53 % | 71 % | 2.6 | 79 % | +15 % vs +27 %, −39 % vs −34 % | ne protège pas assez des chutes |

## Moyenne 200 j, sortie large
_acheter au-dessus de la moyenne 200 j, vendre seulement 8 % en dessous (moins de faux signaux)_

| Catégorie | Valeurs | Rend./an règle | Rend./an garder | Baisse max règle | Baisse max garder | Investi | Trades/an | Moins de baisse (part des valeurs) | 3 dernières années (règle vs garder, baisse max) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 10 | +8,4 % | +13,7 % | −30 % | −32 % | 87 % | 0.7 | 60 % | +20 % vs +24 %, −19 % vs −21 % | ne protège pas assez des chutes |
| grandes valeurs | 25 | +16,7 % | +18,2 % | −44 % | −55 % | 77 % | 0.9 | 92 % | +14 % vs +25 %, −36 % vs −34 % | ne protège pas assez des chutes |
| paris | 8 | +11,0 % | +13,8 % | −81 % | −89 % | 58 % | 2.1 | 100 % | +40 % vs +45 %, −71 % vs −77 % | ne protège pas assez des chutes |
| toutes catégories | 43 | +10,7 % | +14,6 % | −42 % | −53 % | 78 % | 0.9 | 86 % | +21 % vs +27 %, −36 % vs −34 % | ne protège pas assez des chutes |

## Momentum 12 mois
_détenir tant que la clôture est au-dessus de celle d'il y a 12 mois (≈ 252 séances)_

| Catégorie | Valeurs | Rend./an règle | Rend./an garder | Baisse max règle | Baisse max garder | Investi | Trades/an | Moins de baisse (part des valeurs) | 3 dernières années (règle vs garder, baisse max) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 10 | +8,9 % | +13,7 % | −29 % | −32 % | 75 % | 2.6 | 80 % | +21 % vs +24 %, −22 % vs −21 % | ne protège pas assez des chutes |
| grandes valeurs | 25 | +12,8 % | +18,2 % | −46 % | −55 % | 78 % | 2.3 | 84 % | +19 % vs +25 %, −34 % vs −34 % | ne protège pas assez des chutes |
| paris | 8 | +19,3 % | +13,8 % | −72 % | −89 % | 53 % | 2.9 | 100 % | +26 % vs +45 %, −71 % vs −77 % | ne protège pas assez des chutes |
| toutes catégories | 43 | +12,6 % | +14,6 % | −46 % | −53 % | 70 % | 2.5 | 86 % | +20 % vs +27 %, −33 % vs −34 % | ne protège pas assez des chutes |

## Cassure 50 j + stop large
_acheter sur un plus haut de clôture sur 50 j au-dessus de la moyenne 200 j, vendre sous (plus haut − (k + 1,5) × ATR) : reste investi plus longtemps_

| Catégorie | Valeurs | Rend./an règle | Rend./an garder | Baisse max règle | Baisse max garder | Investi | Trades/an | Moins de baisse (part des valeurs) | 3 dernières années (règle vs garder, baisse max) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 10 | +6,4 % | +13,7 % | −26 % | −32 % | 51 % | 2.3 | 90 % | +9 % vs +24 %, −16 % vs −21 % | ne protège pas assez des chutes |
| grandes valeurs | 25 | +9,8 % | +18,2 % | −35 % | −55 % | 53 % | 1.8 | 96 % | +9 % vs +25 %, −32 % vs −34 % | limite les chutes mais rapporte nettement moins que garder |
| paris | 8 | +30,4 % | +13,8 % | −69 % | −89 % | 41 % | 1.2 | 100 % | +54 % vs +45 %, −63 % vs −77 % | ne protège pas assez des chutes |
| toutes catégories | 43 | +9,0 % | +14,6 % | −37 % | −53 % | 51 % | 1.8 | 95 % | +11 % vs +27 %, −32 % vs −34 % | limite les chutes sans trop coûter |
