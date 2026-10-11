# Assistant Bourse

Un bot Telegram qui te donne son **avis** sur tes 14 valeurs : 🟢 ACHETER, ⚪ GARDER ou 🔴 VENDRE, avec un **niveau de confiance** et un avis sur le **levier**. Il ne passe aucun ordre : tu décides.

## Ce que tu reçois
- **7h45, pré-ouverture** (Trade Republic ouvre à 7h30) : ce qui s'est passé cette nuit (Hong Kong, or), les actualités de la nuit, l'avis du jour sur chaque valeur (acheter / vendre avec la raison et le levier, les autres en « GARDER »), les résultats proches, le rappel de versement mensuel.
- **9h05, ouverture d'Euronext** : seulement ce qui a changé depuis 7h45 : écarts d'ouverture notables sur les valeurs de Paris et avis modifiés. Si tout est calme, une ligne.
- **Alertes en séance (vérification toutes les 15 min)**, seulement si :
  - ⚡ grosse variation sur la journée (ETF ±3 %, grandes valeurs ±5 %, paris ±10 %) ;
  - 🚨 mouvement brutal sur 1 h (ETF ±1,5 %, grandes ±3 %, paris ±6 %) ;
  - ⚠️/📰 actualité sensible (enquête, procès, avertissement sur résultats, dilution… ou relèvement, rachat d'actions…).
  Chaque alerte rappelle l'avis actuel sur la valeur. Une alerte n'est jamais répétée.

## Comment l'avis est calculé
Suivi de tendance, volontairement simple : cours vs moyennes 50 et 200 jours, évolution sur 3 mois, surchauffe (RSI). Score ≥ 3 → ACHETER, ≤ -3 → VENDRE, sinon GARDER. Une position suivie (`/achat`) passe en VENDRE si elle atteint ton seuil de perte ou recule trop depuis son sommet (réglable dans `config.json`).

**Confiance** : indice maison (accord des signaux, réduit si la valeur est très volatile ou si des résultats approchent, plafonné à 85 %). Ce n'est **pas** une probabilité de gain.

**Levier** : « possible » seulement si ACHETER, confiance ≥ 65 %, volatilité ≤ 30 %/an, cours au-dessus de la moyenne 200 j, pas de surchauffe, pas de résultats sous 5 jours. **Jamais** sur les « paris ». Même quand c'est « possible » : x2 maximum, stop serré, petite part du capital.

> Ces règles n'ont **pas** été validées par un backtest. Un test sur 10 ans fait sur une version précédente n'avait pas montré qu'elles battaient le simple fait de garder. Considère l'avis comme une aide à la discipline, pas comme une prévision.

## Backtest (à lancer avant de te fier aux avis)
Actions → *backtest* → *Run workflow* (≈ 5-10 min). Tu reçois sur Telegram un verdict en cinq points, avec `report.md` et `report.png`, également disponibles dans les « Artifacts » du run. Le test :
- réplique exactement les règles du bot (le workflow vérifie d'abord la correspondance, `--selfcheck`) ;
- signal à la clôture de J, exécution à l'ouverture de J+1, frais et écarts achat/vente ;
- panier large de ~48 valeurs (gagnantes et perdantes) en plus de tes 14 valeurs, car celles-ci ont été choisies avec le recul ;
- comparaison à « garder » **et** à « garder avec la même exposition », test contre le hasard (positions décalées aléatoirement, 300 tirages) ;
- calibration de la confiance (rendement à 20 séances par tranche), sensibilité aux paramètres (18 combinaisons, aucune optimisation), stabilité dans le temps ;
- levier x2 simulé avec remise à zéro quotidienne et coût de financement.
Si le verdict est négatif, le bot reste utile comme lecture de tendance, mais ne présente pas l'avis comme une prévision. Les actualités et alertes ne sont pas backtestables.

## Commandes Telegram
`/avis nvidia` (détail) · `/achat nvidia` · `/vente nvidia` · `/statut`
Lues à chaque passage du bot (≤ 15 min en séance, et aux récaps de 7h45 et 9h05).

## Mise en place
1. Mets ces fichiers dans ton dépôt GitHub (remplace les anciens ; supprime les anciens `core.py`, `veille.py` et `veille_state.json` s'ils existent encore ; `backtest.py` et `backtest.yml` sont les nouveaux).
2. Settings → Secrets and variables → Actions : `TG_TOKEN`, `TG_CHAT`, et `FINNHUB_KEY` (optionnel, actualités US en plus de Yahoo).
3. Settings → Actions → General → Workflow permissions → *Read and write*.
4. Actions → *assistant* → *Run workflow* : premier message (en manuel, il choisit le récap selon l'heure : avant 9h = pré-ouverture, après = ouverture).
5. Sur Telegram, active les notifications du bot (c'est lui qui fait la « notif » sur ton téléphone).

## À savoir
- Les récaps de 7h45 et 9h05 utilisent chacun deux horaires UTC (été/hiver) ; le bot n'envoie que celui qui tombe à la bonne heure de Paris, une seule fois. GitHub peut retarder une exécution planifiée de 5 à 30 min : « rapide » ≈ quelques minutes à un quart d'heure, pas de la temps réel. Pour un dépôt privé, `*/15` consomme ~1 300 min/mois sur 2 000 gratuites ; passe à `*/30` dans `veille.yml` si besoin.
- Données Yahoo gratuites : retard possible, trous sur Global Hydrogen / Xiaomi (le bot l'indique).
- Ce n'est pas un conseil en investissement.
