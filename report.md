# Backtest swing — 2026-10-11
43 valeurs, 10y d'historique, signal à la clôture, entrée à l'ouverture suivante, frais et écarts inclus (voir `setups.py`).

**Limites** : les valeurs de ta watchlist et les grandes capitalisations actuelles ont été choisies avec le recul (biais de survie) ; les résultats passés ne garantissent rien ; la référence « sans signal » utilise les mêmes sorties.

## Repli en tendance haussière
_RSI 2 j < 10 et clôture sous sa moyenne 5 j, dans une tendance haussière (au-dessus des moyennes 50 et 200 j)_

| Catégorie | Trades | Gagnants | Moyen net | Sans signal | Avantage (t) | Années + | 3 dernières années | Stops | Validé |
|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 321 | 58 % | +0,00 % | −0,15 % | +0,16 % (t=1.6) | 60 % (10 ans) | +0,15 % (n=157) | 26 % | ❌ |
| grandes valeurs | 907 | 59 % | +0,13 % | −0,17 % | +0,30 % (t=2.4) | 60 % (10 ans) | +0,13 % (n=288) | 23 % | ❌ |
| paris | 137 | 53 % | −0,28 % | −0,62 % | +0,34 % (t=0.4) | 57 % (7 ans) | −0,91 % (n=64) | 23 % | ❌ |
| toutes catégories | 1365 | 58 % | +0,06 % | −0,20 % | +0,26 % (t=2.1) | 40 % (10 ans) | +0,00 % (n=509) | 24 % | ❌ |

## Cassure avec volume
_plus haut de clôture sur 20 j avec un volume > 1,5x la normale, dans une tendance haussière_

| Catégorie | Trades | Gagnants | Moyen net | Sans signal | Avantage (t) | Années + | 3 dernières années | Stops | Validé |
|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 172 | 52 % | +0,23 % | +0,04 % | +0,20 % (t=0.9) | 70 % (10 ans) | +0,44 % (n=79) | 28 % | ❌ |
| grandes valeurs | 688 | 49 % | +0,28 % | +0,17 % | +0,11 % (t=0.5) | 50 % (10 ans) | +0,75 % (n=214) | 30 % | ❌ |
| paris | 183 | 48 % | +2,80 % | +0,40 % | +2,40 % (t=2.0) | 67 % (9 ans) | +4,28 % (n=99) | 33 % | ❌ |
| toutes catégories | 1043 | 49 % | +0,72 % | +0,16 % | +0,56 % (t=2.1) | 60 % (10 ans) | +1,58 % (n=392) | 30 % | ❌ |

## Rebond après chute brutale
_baisse sur 3 j <= −2,5 écarts-types (volatilité habituelle de la valeur) : rebond possible_

| Catégorie | Trades | Gagnants | Moyen net | Sans signal | Avantage (t) | Années + | 3 dernières années | Stops | Validé |
|---|---|---|---|---|---|---|---|---|---|
| ETF / or | 251 | 60 % | +0,16 % | −0,12 % | +0,28 % (t=1.5) | 50 % (10 ans) | +1,15 % (n=78) | 26 % | ❌ |
| grandes valeurs | 660 | 55 % | −0,01 % | −0,11 % | +0,10 % (t=0.5) | 70 % (10 ans) | +1,03 % (n=208) | 24 % | ❌ |
| paris | 149 | 23 % | −0,38 % | −0,71 % | +0,33 % (t=0.5) | 43 % (7 ans) | −0,43 % (n=71) | 13 % | ❌ |
| toutes catégories | 1060 | 52 % | −0,02 % | −0,19 % | +0,17 % (t=1.0) | 60 % (10 ans) | +0,77 % (n=357) | 23 % | ❌ |
