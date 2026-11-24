# Assistant Bourse (Trade Republic) — notifications Telegram

Le bot ne passe **aucun ordre** : il t'informe, tu agis dans ton application.

## Ce que tu reçois
Tous les récapitulatifs ont **la même structure, dans le même ordre**, en un seul message :

`🌍 Marché` (S&P 500, Nasdaq, CAC 40, VIX, ambiance : combien de tes valeurs montent ou baissent) → `💼 Tes positions` → `⚡ Gros changements` (gros mouvements, changements de tendance, stop touché, séance inhabituelle) → `📰 Actus à retenir` (6 titres au plus, une seule fois chacun : positions et mauvaises nouvelles d'abord) → `📈 Tendances de fond` (regroupées : haussière / repli / baissière) → `⏰ À surveiller`.

- **Synthèse d'ouverture (≈ 15h45, après l'ouverture de Wall Street)** : cette structure, au cours en direct. Pas de setups swing : leur entrée se fait « à l'ouverture », déjà passée.
- **Synthèse de clôture (≈ 22h25)** : la même structure sur la séance terminée, puis un second message « 📋 Détail du soir » (nouveaux setups validés pour demain, bilans de semaine et de mois).
- **En séance (toutes les 10 min)** : un seul message « 🚨 Alertes », seulement si gros mouvement, mouvement brutal en 1 h, actualité sensible, ou événement sur une de tes positions (stop touché…).
- **`/synthese` à tout moment** : la même synthèse à l'instant (marché ouvert : cours en direct ; fermé : dernière séance). Elle ne « consomme » aucune actualité : les prochaines alertes ne sont pas modifiées.
- Réglages (`config.json`) : `"synthese": {"seuil_gros_mouvement": 0.6}` (part du seuil d'alerte journalier au-delà de laquelle un mouvement compte comme « gros » ; 0,6 = 3 % pour une grande valeur).
- Le briefing « matin » de 8h existe toujours mais est désactivé : `"briefings": {"matin": ["07:40", "10:00"]}` pour le réactiver. Les autres fenêtres : `"ouverture": ["15:40", "18:30"]`, `"soir": ["22:20", "23:50"]`.

## Flash : toutes les grosses infos, le plus vite possible
- Le bot surveille en continu des flux publics (Yahoo Finance, CNBC, MarketWatch, Réserve fédérale, BCE, + Finnhub si `FINNHUB_KEY`) et envoie **immédiatement** tout ce qui compte pour le marché : banques centrales, taux, inflation, droits de douane, guerre, pétrole, faillites, krach / envolée, fusions de plusieurs milliards, grands noms…
- **Pas de filtre sur tes valeurs** : chaque titre reçoit un score d'importance (mots-clés, montants, % de mouvement, grands noms). Tes valeurs et positions *ajoutent* des points (💼 = une de tes positions), elles ne servent jamais à exclure. Les titres du genre « 5 actions à acheter » sont écartés.
- Une même histoire n'est envoyée qu'une fois, même sous plusieurs titres ou sources. 5 infos au plus par message, le reste est résumé en une ligne.
- Les grosses infos envoyées en flash reviennent aussi dans « 📰 Actus à retenir » des synthèses (sous l'étiquette « Marché »), une seule fois.
- Fenêtre : 7h-23h30 (Paris), jours ouvrés. Réglages : `"flash": {"seuil": 4, "fenetre": ["07:00", "23:30"], "max_par_message": 5, "week_end": false, "actif": true}` (seuil plus bas = plus d'infos ; plus haut = seulement les plus grosses). Ajouter une source : `"flux": [{"nom": "...", "url": "...", "bonus": 0}]`.

## Réactivité
- **Sur GitHub Actions** : cron toutes les 5 min (le minimum de GitHub, avec 5 à 20 min de retard possible). Pour du vrai quasi-temps réel, lance plutôt **`python run.py daemon`** sur un Raspberry Pi, un petit VPS ou un vieux PC : flash chaque minute, alertes de cours toutes les 5 min, commandes Telegram en quelques secondes (long polling). Variables d'environnement : `TG_TOKEN`, `TG_CHAT`, `FINNHUB_KEY` ; l'état est gardé dans `state.json` (pense à `git pull` avant de relancer). Ne lance pas le daemon et le cron GitHub en même temps.
- **Réponse aux commandes en ≈ 30 s (optionnel)** : sans webhook, le bot ne lit tes messages qu'à chaque passage du cron. Avec le webhook, chaque message déclenche aussitôt un passage `commands`.
  1. Crée un Worker Cloudflare (gratuit) avec `worker/telegram-webhook.js` et ses 4 variables (`TG_CHAT`, `WEBHOOK_SECRET`, `GH_REPO`, `GH_TOKEN` ; jeton GitHub limité à ce dépôt, permission *Contents: Read and write*).
  2. Branche Telegram dessus : `https://api.telegram.org/bot<TG_TOKEN>/setWebhook?url=<url du worker>&secret_token=<WEBHOOK_SECRET>`.
  3. Quand un webhook est actif, Telegram interdit `getUpdates` : les messages passent uniquement par le Worker (pour revenir en arrière : `deleteWebhook`).
  Limite : si tu envoies plusieurs commandes en quelques secondes, GitHub ne garde qu'un passage en attente ; renvoie la dernière si elle reste sans réponse.
- **Horaires exacts (optionnel)** : appelle `POST /repos/<toi>/bourse/actions/workflows/bot.yml/dispatches` (`{"ref":"main"}`) depuis cron-job.org à 15h45 et 22h25 (heure de Paris) : bien plus ponctuel que le cron GitHub.

## Actualités : moins de bruit
- Un article n'est gardé que s'il cite la valeur (nom, alias ou ticker) : « Spotify relevé, Alibaba lancé… » n'est plus classé sous Microsoft. Désactivable : `"actus": {"filtrer_pertinence": false}`.
- La même histoire sous deux titres ou deux sources (ex. Amazon / Decart) n'est envoyée qu'une fois.
- Les titres sont traduits en parallèle. Fréquence des actus en séance : `"alertes": {"actus_toutes_les_min": 10}` (mettre 30 pour l'ancien rythme).

## Trois façons de suivre une position (`/achat nvidia …`)
| Mot-clé | Horizon | Ce que fait le bot |
|---|---|---|
| *(rien)* ou `pullback` / `breakout` / `capitulation` | **swing**, 2 jours à 1 semaine | stop, objectif et délai calculés ; t'alerte quand stop/objectif touché, quand remonter le stop, à l'approche du délai |
| `tendance` | **2-3 mois et plus** | stop suiveur « chandelier » (plus haut de clôture − k × ATR) qui ne fait que monter ; tu gardes tant que la tendance tient ; alerte si la clôture passe sous le stop |
| `long` | très long terme | suivi sans stop : alerte si −X % depuis l'achat ou depuis le sommet |

Écris ton achat **sans prix** (`/achat nvidia`) : le bot prend le cours Yahoo, dans la même devise que ses calculs. Si tu donnes un prix très différent du cours Yahoo (euros au lieu de dollars), il le signale et utilise le cours Yahoo.

## Notifications en français
Tout le texte envoyé est en français. Les titres d'actualité (souvent en anglais chez Yahoo/Finnhub) sont traduits automatiquement ; si la traduction est indisponible, le titre d'origine est conservé. Pour la couper : `"traduire_actus": false` dans `config.json`.

## « Attention X demain » et bilans semaine / mois
- **Matin** : section « À surveiller (7 jours) » ; **soir** : « Attention : à surveiller sous 48 h ». On y trouve les résultats (ex. « D-Wave : résultats demain »), les détachements de dividende (le cours baisse d'autant) et tes propres événements.
- Les résultats et dividendes concernent aussi les valeurs de l'`univers` (Apple, Tesla, LVMH…), même sans position ni watchlist. Pour limiter aux valeurs de la watchlist : `"alertes_univers": false`.
- **Soir** : « Séance inhabituelle » si une valeur a un volume ≥ 3 × la normale ou un gap à l'ouverture.
- **Dernière séance de la semaine (vendredi) et du mois** : bilan des variations de toutes tes valeurs (🔥 / ⚠️ au-delà d'un seuil), plus hauts/bas sur 12 mois, changements de tendance de la période. Seuils : `recap` dans `config.json`.
- **Tes événements** (levée de blocage SpaceX, réunion de la Fed…) : ajoute-les dans `config.json`, par exemple
  `"evenements": [{"ticker": "SPCX", "date": "2026-11-05", "texte": "résultats prévus"}, {"date": "2026-12-16", "texte": "réunion de la Fed"}]`
  (`ticker` facultatif). Les dates de résultats et de dividendes viennent de Yahoo, parfois incomplètes ou approximatives.
- Limite : les jours fériés ne sont pas gérés, donc si le vendredi est férié le bilan de semaine saute.

## Commandes
`/synthese` · `/tendances` · `/idees` (`/idees tout`) · `/avis nvidia` · `/achat nvidia [prix] [quantité] [tendance|pullback|breakout|capitulation|long]` · `/stop nvidia 138,5` · `/vente nvidia [prix]` · `/statut` · `/bilan` · `/aide`

## Tendance de fond
États : 🟢 haussière (au-dessus des moyennes 50 et 200 j) · 🟡 repli dans la tendance · 🔴 baissière (sous la moyenne 200 j) · ⚪ trop récente pour juger (moins de 200 séances, ex. SpaceX).
Un changement n'est signalé que s'il est confirmé par 2 clôtures. C'est un filtre de risque, pas une prévision.

## Mise en route
1. Dépôt GitHub **privé** avec tous ces fichiers. GitHub n'exécute le bot que si `bot.yml` est dans `.github/workflows/` (dossier caché, souvent ignoré par le glisser-déposer). Le dossier visible **`workflows/`** contient la même copie : envoie-le avec le reste, puis dans GitHub ouvre `workflows/bot.yml` → ✏️ → dans le nom du fichier, remplace `workflows/bot.yml` par `.github/workflows/bot.yml` → *Commit*. (Si `.github/workflows/bot.yml` existe déjà dans ton dépôt, rien à faire.)
2. Secrets : `TG_TOKEN`, `TG_CHAT` (et `FINNHUB_KEY` en option). Settings → Actions → General → *Read and write permissions*.
3. Onglet Actions → *bot* → *Run workflow* : d'abord **test**, puis **backtest** (quelques minutes), puis **matin**.
4. Ensuite tout est automatique (modes disponibles : `test`, `ouverture`, `soir`, `watch`, `commands`, `backtest`). En cas de souci, regarde les logs de l'exécution dans l'onglet Actions.

## Backtests (mode `backtest`)
- **Swing** : chaque setup est « validé » seulement s'il bat une entrée sans signal, après frais, de façon régulière. Le dernier backtest n'en a validé aucun : le bot masque donc les idées non validées (`/idees tout` pour les voir, ou `"montrer_non_valides": true` dans `config.json`).
- **Tendance** : compare 3 règles à « garder » (rendement par an, baisse maximale, temps investi, par catégorie). Il dit si la règle limite les chutes et à quel prix. Lis le message reçu avant de t'y fier.
- Limites : valeurs choisies avec le recul (biais de survie), période surtout haussière, passé ≠ futur.

## Tests
`python -m unittest discover -s tests` (données synthétiques, aucun accès réseau).

## SpaceX
Cotée au Nasdaq sous **SPCX** depuis le 12 juin 2026 : historique court, donc pas de moyenne 200 j avant ~200 séances (le bot l'indique). Classée « pari » (seuils larges). Points de vigilance : résultats prévus début novembre (date non confirmée), levées de blocage échelonnées des actionnaires d'avant l'IPO (à vérifier), volatilité forte.
