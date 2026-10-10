# Assistant de trading par notifications (Trade Republic, sans automatisation)

Le bot surveille ta watchlist, calcule un score de conviction, te dit **quand acheter** (avec stop, mise et frais)
et **quand vendre** les positions que tu lui déclares. Il ne passe AUCUN ordre : tu les passes à la main dans Trade Republic.

## Mise en place (≈ 30 min, à faire depuis un ordinateur)

1. **Bot Telegram** : dans Telegram, parle à @BotFather → `/newbot` → note le **token**.
   Envoie un message (ex. "salut") à ton nouveau bot, puis ouvre dans un navigateur
   `https://api.telegram.org/bot<TOKEN>/getUpdates` et note le nombre après `"chat":{"id":` → c'est ton **chat_id**.
2. **(Optionnel) Clé Finnhub** gratuite sur finnhub.io pour le filtre news (actions US).
3. **GitHub** : crée un dépôt **privé**, envoie-y tous les fichiers de ce dossier (y compris `.github/`).
4. **Secrets** : dépôt → Settings → Secrets and variables → Actions → New repository secret :
   `TG_TOKEN`, `TG_CHAT` et (optionnel) `FINNHUB_KEY`.
5. **Autoriser l'écriture** : Settings → Actions → General → Workflow permissions → *Read and write permissions*.
6. **Premier test** : onglet Actions → *assistant* → *Run workflow*. Tu dois recevoir un message Telegram en ~1 min.
7. **Backtest** : onglet Actions → *backtest* → *Run workflow*. Le rapport arrive sur Telegram.
   Si la stratégie ne bat pas « acheter-garder » sur les deux moitiés de période, ne passe pas au réel.

## Utilisation au quotidien

- Matin (≈ 8h30) : résumé + signaux de vente. Soir (≈ 23h, après la clôture US) : signaux d'achat.
- Tu reçois « 🟢 SIGNAL ACHAT … » → si tu achètes dans Trade Republic, réponds au bot avec la commande fournie :
  `/achat NVDA 134.5 2 600` (ticker, cours du sous-jacent, levier, mise en €).
- Tu reçois « 🔴 SIGNAL VENTE … » → tu vends dans l'app, puis `/vente NVDA 150`
  (ajoute le gain réel en € si tu le connais : `/vente NVDA 150 42.5`).
- `/statut` (portefeuille), `/bilan` (journal : gagnants, gain net, frais), `/aide`.
- **Les commandes sont lues à la prochaine exécution** (matin/soir) ou quand tu lances *Run workflow*
  depuis le navigateur. Pour du temps réel il faudrait un petit serveur (plus tard).


## Mode veille v2 (surveillance multi-niveaux)

`veille.py` tourne toutes les 30 min (workflow *veille*, 7h-21h UTC en semaine) et ignore les bourses fermées.
**Alertes d'information uniquement : il ne modifie jamais tes positions, ne passe aucun ordre et ne remplace pas un stop posé dans l'app.**

**Priorités** : 🔴 urgent (risque sur une position : stop touché, baisse ≥ 4 % en équivalent produit, gap défavorable, news critique,
suspension) · 🟠 important · 🔵 information (3 max par valeur et par jour). Un seul bloc par valeur, trié par priorité,
avec cours, comparaison marché/secteur, heure d'observation et fraîcheur des données.

**Détecteurs**
- **Paliers de mouvement** 2 % / 3 % / 5 % / 7,5 % / 10 % (un message par nouveau palier franchi), avec comparaison au Nasdaq (QQQ, ou Euro Stoxx 50
  pour l'Europe) et au secteur (`secteur` dans la watchlist) : « propre à la valeur » ou « partagé avec le marché ».
- **Gap d'ouverture** ≥ 3 % (avec rappel du risque de barrière pour un turbo).
- **Volume relatif (RVOL)** à la même heure, sur jusqu'à 20 séances ; alerte à partir de x2.
- **Accélération** (15 min ≥ 2 %, 1 h ≥ 3 %), **retournement brutal** (≥ 3 % depuis le haut/bas du jour), **volatilité anormale**
  (amplitude ≥ 2 x la normale), **cassure intraday de l'EMA20 / EMA50**.
- **Suspension possible** (aucun volume ≥ 1 h en séance), **cours incohérent**, **données périmées** : aucune alerte de marché sur ces valeurs.
- **News classées** (Finnhub, actions US) : critique (enquête, sanction, faillite, suspension) / important (dilution, prévisions, dirigeant,
  incident, acquisition sans présumer qu'elle est négative) / à surveiller (journal seulement sur la watchlist). Doublons regroupés,
  démentis détectés (niveau abaissé), source, heure, lien et résumé court. **C'est une présélection par mots-clés : vérifie la source.**
- **Calendrier** : résultats et dividendes à J-5, J-2 et jour J (en séances) ; pour une position détenue, alerte de risque, jamais de vente
  automatique ; les dates inconnues sont listées une fois par semaine.
- **Positions simultanées en baisse** (exposition commune).

**Lien avec le bot de trading** : la veille pose des *drapeaux*. Un événement critique, une suspension possible ou des données douteuses
**bloquent** les nouveaux signaux d'achat (les « important » ajoutent un avertissement). Le signal final reste calculé séparément par `bot.py`.

**Commandes** : `/achat`, `/vente`, `/statut`, `/bilan` et `/journal` (10 dernières alertes avec leur évolution depuis) sont lues à chaque passage
(≈ 30 min). **Test** : onglet Actions → *veille* → *Run workflow* envoie l'état de chaque valeur même sans alerte.
Réglages dans `config.json` → bloc `veille`.

**Non inclus pour l'instant** : spread / liquidité (pas de données gratuites fiables), variation inhabituelle du RSI, accélération sur moins de
15 min, classement automatique par IA des news, décisions judiciaires annoncées. Le journal des alertes est conservé dans `veille_state.json`
pour mesurer plus tard leur utilité et réduire les faux positifs.

Limites : Yahoo gratuit a parfois jusqu'à 15 min de retard ; GitHub peut retarder ou sauter une exécution planifiée ; ~30 exécutions/jour
consomment environ 600-700 minutes/mois de ton quota gratuit (vérifie Settings → Billing). Pour du vrai temps réel : petit serveur + flux de données.

## Réglages (`config.json`)

Capital, risque par trade (2 %), positions max (3), réserve cash (20 %), levier max (2), frais, coupe-circuit (-15 %),
watchlist et seuils de volatilité par valeur. Vérifie `change_pct` (frais de change) et `spread_pct` dans ton app et ajuste.

## Règles du bot

- **Achat** : cours > EMA20 > EMA50, RSI 50-70, pas étiré (< 8 % au-dessus de l'EMA20), volume correct, plus fort que le Nasdaq
  sur 3 mois, volatilité sous le seuil, pas de news négative, pas de résultats sous 5 jours, score ≥ 40/100.
  Levier 2x seulement si score ≥ 75 et plafond de la valeur ≥ 2 ; sinon 1x.
- **Vente** : stop suiveur (plus haut − 3×ATR en 1x, 2,5×ATR avec levier, ne descend jamais), cassure EMA50, cassure EMA20 + RSI < 45,
  durée max (20 j avec levier), news négative, résultats sous 2 jours.
- **Garde-fous** : mise limitée par le risque (2 % de la poche), 30 % max par ligne, frais A/R ≤ 2 % de la mise, coupe-circuit à -15 %.

## Limites (à lire)

- Signaux sur **clôtures journalières** : les gaps de nuit ne sont pas couverts → pose un stop dans l'app quand c'est possible.
- Données Yahoo gratuites : parfois en retard ou manquantes (dates de résultats, fondamentaux). Le bot le signale au lieu d'inventer.
- Le filtre news par mots-clés est grossier. Le score fondamental n'est pas backtestable.
- Les produits à levier 2-3x s'usent dans les marchés agités (réinitialisation quotidienne) : ce sont des outils de court terme.
- Aucun gain garanti. Ceci n'est pas un conseil en investissement.
- Procède par étapes : papier 6-8 semaines → réel en 1x → levier seulement si le journal est positif net de frais.
