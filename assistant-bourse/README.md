# Assistant Bourse

Un bot Telegram qui (1) te prévient quand quelque chose se passe sur **tes valeurs** et (2) te propose des **trades swing de 2 jours à 1 semaine** avec un plan chiffré : entrée, stop, sortie, délai, mise. Il ne passe **aucun ordre** : tu décides et tu agis dans ton application. Il tourne gratuitement sur GitHub Actions, sans serveur.

## Ce que tu reçois

| Quand (heure de Paris) | Quoi |
|---|---|
| **7h45** | Pré-ouverture : contexte de marché (S&P, CAC, VIX), l'état de tes positions suivies avec l'action du jour, actus sensibles de la nuit, résultats à venir, **setups swing** avec plan chiffré, tendance de chaque valeur |
| **9h → 22h, toutes les ~15 min** | Alertes, seulement si nécessaire : ⚡ grosse variation sur la journée, 🚨 mouvement brutal en 1 h, ⚠️/📰 actualité sensible, 🛑 stop touché, 🎯 objectif atteint, 🔒 « remonte ton stop », ⏱ délai atteint |
| **22h15** | Clôture : ce qu'il faut faire demain sur tes positions + nouveaux setups apparus à la clôture. Silencieux s'il n'y a rien. |

Seuils des alertes (par catégorie ETF / grande valeur / pari) dans `config.json`.

## Mise en route (10 minutes)

1. **Crée le bot** : sur Telegram, parle à **@BotFather**, `/newbot`, récupère le **token**. Écris un message à ton bot, puis ouvre `https://api.telegram.org/bot<TOKEN>/getUpdates` : ton **chat id** est dans `"chat":{"id":...}`.
2. **Crée un dépôt GitHub *privé*** et envoie-y le contenu de ce dossier (privé : `state.json` contient tes positions).
3. **Secrets** : Settings → Secrets and variables → Actions → `TG_TOKEN`, `TG_CHAT` (obligatoires), `FINNHUB_KEY` (optionnel, actus US supplémentaires, clé gratuite sur finnhub.io).
4. **Actions → assistant → Run workflow → mode `test`** : tu dois recevoir « ✅ Test OK » avec Yahoo Finance ✅.
5. **Actions → backtest → Run workflow** (≈ 5-10 min). Il valide ou non chaque setup (voir plus bas), puis se relance tout seul chaque dimanche.
6. Adapte `config.json` : surtout `capital_swing` (le capital que tu consacres au swing), ta `watchlist` et l'`univers` scanné pour les idées.

Les messages démarrent au prochain créneau. Pour voir tout de suite : Run workflow → mode `matin`.

## Les commandes Telegram

```
/idees                         setups swing du moment
/avis nvidia                   analyse d'une valeur (tendance, setups, historique)
/achat nvidia 142,5 10         je suis ta position : prix, quantité (optionnels)
/achat nvidia 142,5 10 pullback   idem avec le setup d'origine (stop et délai adaptés)
/achat nvidia long             suivi long terme (alerte de recul, sans stop)
/stop nvidia 138,5             change le stop suivi
/vente nvidia 151              clôture le suivi, note le résultat
/statut     /bilan     /aide
```
Elles sont lues à chaque passage du bot (≈ 15 min en séance, plus au récap du matin) : la réponse n'est pas instantanée. Seul ton chat est écouté.

**Après chaque achat, envoie `/achat`** pour que le bot suive stop, objectif et délai. **Pose toujours le stop dans ton application** : le bot ne peut que te prévenir, avec jusqu'à ~15 à 30 min de retard.

## Les setups swing

Trois setups classiques, **fixés à l'avance** (pas ajustés sur les données), dans `bourse/setups.py` :

- **Repli en tendance haussière** : RSI 2 j < 10 et clôture sous la moyenne 5 j, au-dessus des moyennes 50 et 200 j. Sortie : retour au-dessus de la moyenne 5 j, stop, ou 5 séances.
- **Cassure avec volume** : plus haut 20 j avec volume > 1,5× la normale, en tendance haussière. Sortie : objectif à 2× le risque, stop, ou 7 séances.
- **Rebond après chute brutale** : baisse sur 3 j ≤ −2,5 écarts-types. Sortie : retour à la moyenne 5 j, stop, ou 5 séances.

Signal à la clôture de J, entrée à l'ouverture de J+1. Le **stop** vaut 1,5 à 2 ATR (borné par catégorie). La **mise** est calculée pour ne risquer que `risque_par_trade` (1 % par défaut) de ton capital swing, plafonnée à `mise_max_pct` (25 %).

### Validé / non validé : le garde-fou
Le backtest rejoue chaque setup sur 10 ans avec **les mêmes règles que le bot**, frais et écarts inclus, et le compare à une entrée sans signal avec les mêmes sorties. Un setup n'est **« 🟢 validé »** pour une catégorie que s'il a ≥ 100 trades, un rendement net > 0, un avantage significatif (t ≥ 2,5) sur l'entrée sans signal, qu'il est positif sur ≥ 60 % des années et sur les 3 dernières années. Sinon il est signalé « 🟡 non validé » (ou « 🔴 historiquement perdant »), classé après les validés. Sur des marchés aléatoires simulés, ces critères valident à tort environ 1 % des combinaisons (mesuré sur 126 combinaisons simulées), et ils détectent un vrai effet quand il existe.

Ce que ça **ne garantit pas** : un setup validé peut cesser de marcher. Les valeurs de ta watchlist et les grandes capitalisations actuelles sont choisies avec le recul (biais de survie), donc les résultats sont probablement flatteurs. Les frais pèsent lourd : sur des cours sans aucun avantage, le rendement net par trade est négatif.

## Garde-fous intégrés
- Résultats d'entreprise dans la durée du trade → idée dégradée (risque de gap).
- Pas de repli sur les statistiques d'une autre catégorie (un pari spéculatif n'est jamais « validé » avec des résultats de grandes valeurs).
- Stop suiveur : à +1 risque, stop au prix d'entrée ; à +2 risques, stop à +1 risque.
- Alerte si Yahoo ne répond pas, et si le bot plante (une fois par jour max).

## Limites à connaître
- **GitHub Actions n'est pas du temps réel** : les crons sont souvent retardés de 5 à 30 min, parfois plus. Yahoo affiche aussi jusqu'à 15 min de retard sur certaines places. Ce n'est pas adapté au day trading.
- **Yahoo Finance est non officiel** et peut bloquer ponctuellement les serveurs GitHub. Le bot réessaie et te prévient.
- Les prix sont ceux de la place d'origine (Yahoo), pas ceux de ton courtier (écart de 0,1 à 0,5 %). Jours fériés non gérés : les alertes s'appuient sur la fraîcheur des cours.
- Le bot ne voit pas ton compte : il ne connaît que ce que tu lui dis par `/achat` et `/vente`.
- Pas de levier, pas de vente à découvert : le bot ne propose que des achats. Ce n'est pas un conseil en investissement ; tu peux perdre de l'argent.

## Coût GitHub
Un dépôt privé dispose de 2 000 min/mois gratuites ; le bot en consomme environ 1 500 (≈ 60 exécutions par jour ouvré, d'environ 1 minute). Si tu veux de la marge, remplace `*/15` par `*/30` dans `.github/workflows/assistant.yml`.

## Développement
```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v      # 27 tests sur données synthétiques, sans réseau
TG_TOKEN=... TG_CHAT=... python run.py test  # vrai test de bout en bout
python run.py matin --dry                    # affiche le récap dans le terminal, sans rien envoyer ni écrire
```
Structure : `run.py` (entrée) · `bourse/setups.py` (règles) · `bourse/backtest.py` · `bourse/jobs.py` (matin/watch/soir) · `bourse/commands.py` · `bourse/positions.py` · `bourse/render.py` (messages) · `config.json` · `state.json` (état, mis à jour par le bot) · `stats.json` (écrit par le backtest).
