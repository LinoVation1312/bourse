# Assistant Bourse (Trade Republic) — notifications Telegram

Le bot ne passe **aucun ordre** : il t'informe, tu agis dans ton application.

## Ce que tu reçois
- **Matin (≈ 8h)** : tes positions, changements de tendance, actualités sensibles, résultats proches, setups swing *validés*, tendance de fond de chaque valeur.
- **Soir (≈ 22h30, après la clôture US)** : bilan de la séance de chacune de tes valeurs, tes positions, changements de tendance, résultats imminents, nouveaux setups validés.
- **En séance (toutes les 30 min)** : alerte seulement si gros mouvement, mouvement brutal en 1 h, actualité sensible, ou événement sur une de tes positions (stop touché…).

## Trois façons de suivre une position (`/achat nvidia …`)
| Mot-clé | Horizon | Ce que fait le bot |
|---|---|---|
| *(rien)* ou `pullback` / `breakout` / `capitulation` | **swing**, 2 jours à 1 semaine | stop, objectif et délai calculés ; t'alerte quand stop/objectif touché, quand remonter le stop, à l'approche du délai |
| `tendance` | **2-3 mois et plus** | stop suiveur « chandelier » (plus haut de clôture − k × ATR) qui ne fait que monter ; tu gardes tant que la tendance tient ; alerte si la clôture passe sous le stop |
| `long` | très long terme | suivi sans stop : alerte si −X % depuis l'achat ou depuis le sommet |

Écris ton achat **sans prix** (`/achat nvidia`) : le bot prend le cours Yahoo, dans la même devise que ses calculs. Si tu donnes un prix très différent du cours Yahoo (euros au lieu de dollars), il le signale et utilise le cours Yahoo.

## Notifications en français
Tout le texte envoyé est en français. Les titres d'actualité (souvent en anglais chez Yahoo/Finnhub) sont traduits automatiquement ; si la traduction est indisponible, le titre d'origine est conservé. Pour la couper : `"traduire_actus": false` dans `config.json`.

## Commandes
`/tendances` · `/idees` (`/idees tout`) · `/avis nvidia` · `/achat nvidia [prix] [quantité] [tendance|pullback|breakout|capitulation|long]` · `/stop nvidia 138,5` · `/vente nvidia [prix]` · `/statut` · `/bilan` · `/aide`

## Tendance de fond
États : 🟢 haussière (au-dessus des moyennes 50 et 200 j) · 🟡 repli dans la tendance · 🔴 baissière (sous la moyenne 200 j) · ⚪ trop récente pour juger (moins de 200 séances, ex. SpaceX).
Un changement n'est signalé que s'il est confirmé par 2 clôtures. C'est un filtre de risque, pas une prévision.

## Mise en route
1. Dépôt GitHub **privé** avec tous ces fichiers. GitHub n'exécute le bot que si `bot.yml` est dans `.github/workflows/` (dossier caché, souvent ignoré par le glisser-déposer). Le dossier visible **`workflows/`** contient la même copie : envoie-le avec le reste, puis dans GitHub ouvre `workflows/bot.yml` → ✏️ → dans le nom du fichier, remplace `workflows/bot.yml` par `.github/workflows/bot.yml` → *Commit*. (Si `.github/workflows/bot.yml` existe déjà dans ton dépôt, rien à faire.)
2. Secrets : `TG_TOKEN`, `TG_CHAT` (et `FINNHUB_KEY` en option). Settings → Actions → General → *Read and write permissions*.
3. Onglet Actions → *bot* → *Run workflow* : d'abord **test**, puis **backtest** (quelques minutes), puis **matin**.
4. Ensuite tout est automatique. En cas de souci, regarde les logs de l'exécution dans l'onglet Actions.

## Backtests (mode `backtest`)
- **Swing** : chaque setup est « validé » seulement s'il bat une entrée sans signal, après frais, de façon régulière. Le dernier backtest n'en a validé aucun : le bot masque donc les idées non validées (`/idees tout` pour les voir, ou `"montrer_non_valides": true` dans `config.json`).
- **Tendance** : compare 3 règles à « garder » (rendement par an, baisse maximale, temps investi, par catégorie). Il dit si la règle limite les chutes et à quel prix. Lis le message reçu avant de t'y fier.
- Limites : valeurs choisies avec le recul (biais de survie), période surtout haussière, passé ≠ futur.

## Tests
`python -m unittest discover -s tests` (données synthétiques, aucun accès réseau).

## SpaceX
Cotée au Nasdaq sous **SPCX** depuis le 12 juin 2026 : historique court, donc pas de moyenne 200 j avant ~200 séances (le bot l'indique). Classée « pari » (seuils larges). Points de vigilance : résultats prévus début novembre (date non confirmée), levées de blocage échelonnées des actionnaires d'avant l'IPO (à vérifier), volatilité forte.
