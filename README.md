# 🕵️ Moniteur Vinted

Un script Python qui surveille automatiquement une ou plusieurs recherches
Vinted, page profonde par page profonde, pour repérer les annonces qui
**disparaissent rapidement** (donc qui se vendent vite) — sans jamais se
connecter à un compte, avec un rythme lent et volontairement discret.

---

## ⚠️ Avertissement important — à lire avant de commencer

Ce script pilote un vrai navigateur pour consulter des pages **publiques**
de Vinted (aucune connexion à un compte, aucun appel aux API internes du
site). Malgré cela :

- L'utilisation d'outils automatisés sur Vinted n'est probablement **pas
  conforme à ses conditions d'utilisation**. C'est vrai pour la quasi-totalité
  des sites de ce type.
- Ce script est fourni à titre **personnel et éducatif**, pensé pour un usage
  raisonnable, à faible fréquence et sur un petit nombre de recherches.
  **Vous restez seul responsable** de la façon dont vous l'utilisez.
- Vinted peut, à tout moment, limiter ou bloquer un comportement qu'il juge
  automatisé (temporairement ou plus durablement). Le script est conçu pour
  **reculer et attendre** s'il détecte un blocage — jamais pour forcer le
  passage (pas de CAPTCHA à contourner, pas de changement d'adresse IP, etc.).
  Si cela arrive, c'est un signal pour réduire la fréquence, pas pour insister.
- Ne partagez jamais publiquement un fichier `config.yaml` déjà rempli avec
  un vrai webhook Discord ou un vrai token Telegram (voir la section
  Notifications plus bas).

Si vous n'êtes pas à l'aise avec ces conditions, il vaut mieux ne pas
utiliser ce script.

---

## 🧠 Comment ça marche, en résumé

À chaque cycle, le script :
1. Choisit **au hasard** un prix minimum parmi ceux que vous avez définis
   (par défaut 38 €, 40 € ou 43 €) ;
2. Ouvre un vrai navigateur (Chromium) et charge vos recherches Vinted,
   triées par **« Plus récent »**, avec ce prix minimum, sur les pages que
   vous avez choisi de surveiller (par exemple 9 à 15) ;
3. Note les annonces visibles (identifiant, titre, prix, lien) ;
4. Compare avec ce qui avait été vu au cycle précédent ;
5. Pour toute annonce qui a disparu de la zone surveillée, va vérifier
   directement sa page pour confirmer qu'elle est bien **vendue** (et pas
   juste repoussée plus loin par de nouvelles annonces) ;
6. Affiche une alerte dans le terminal (et, si configuré, sur Discord/Telegram)
   avec une estimation du temps écoulé entre la première observation et la
   disparition ;
7. Attend un temps aléatoire (8 à 15 minutes par défaut) avant de recommencer,
   sauf la nuit (aucune activité entre minuit et 7h par défaut).

⏱️ **Important à comprendre** : la « vitesse de vente » affichée est une
**estimation**, calculée entre le moment où *vous* avez commencé à observer
l'annonce et le moment où elle a disparu — pas forcément son temps de mise en
ligne réel (l'annonce a pu être publiée avant votre première observation).

---

## 📁 Structure des fichiers

```
Vinted/
├── moniteur_vinted.py     # le script principal (celui que vous lancez)
├── config.yaml             # votre configuration (recherches, horaires, notifications...)
├── requirements.txt        # liste des dépendances Python à installer
├── demarrer.sh              # lancement + redémarrage auto (macOS/Linux) — voir "Déploiement"
├── demarrer.bat             # lancement + redémarrage auto (Windows) — voir "Déploiement"
├── README.md                # ce fichier
│
# Fichiers créés automatiquement à l'usage (pas besoin d'y toucher) :
├── vinted_monitor.db        # mémoire des annonces déjà vues (base SQLite)
├── vinted_monitor.log       # journal détaillé de ce que fait le script
└── storage_state.json       # petite session de navigateur sauvegardée
```

---

## 🛠️ Installation, étape par étape

### Étape 0 — Prérequis : Python 3.10 ou plus récent

Vérifiez votre version dans un terminal :

```bash
python3 --version
```

(sous Windows, essayez `python --version`). Si Python n'est pas installé, ou
si la version est inférieure à 3.10, téléchargez-le sur
[python.org/downloads](https://www.python.org/downloads/) — sous Windows,
cochez bien la case **« Add python.exe to PATH »** pendant l'installation.

### Étape 1 — Récupérer les fichiers

Placez `moniteur_vinted.py`, `config.yaml` et `requirements.txt` dans un même
dossier (par exemple `Vinted/`), et ouvrez un terminal dans ce dossier.

- **Windows** : ouvrez le dossier dans l'Explorateur, puis dans la barre
  d'adresse tapez `cmd` et appuyez sur Entrée.
- **macOS** : clic droit sur le dossier dans le Finder → « Nouveau terminal
  au dossier » (ou ouvrez Terminal puis `cd chemin/vers/Vinted`).
- **Linux** : clic droit dans le gestionnaire de fichiers → « Ouvrir un
  terminal ici », ou `cd chemin/vers/Vinted`.

### Étape 2 — Créer un environnement virtuel (recommandé)

Cela évite d'installer les dépendances « globalement » sur votre ordinateur.

```bash
python3 -m venv .venv
```

Puis activez-le :

```bash
# Windows (invite de commandes) :
.venv\Scripts\activate

# macOS / Linux :
source .venv/bin/activate
```

Vous devriez voir `(.venv)` apparaître au début de la ligne du terminal.
**Répétez cette activation à chaque fois que vous rouvrez un terminal** pour
utiliser le script (mais l'installation, elle, ne se fait qu'une fois).

### Étape 3 — Installer les dépendances Python

```bash
pip install -r requirements.txt
```

### Étape 4 — Installer le navigateur utilisé par Playwright

```bash
playwright install chromium
```

Cette commande télécharge une version de Chromium dédiée à Playwright
(quelques centaines de Mo, une seule fois). C'est ce navigateur qui sera
piloté automatiquement par le script.

👉 Si vous préférez utiliser votre **vrai Google Chrome** déjà installé sur
votre PC plutôt que ce Chromium dédié, vous pouvez mettre `canal: "chrome"`
dans `config.yaml` (section « navigateur ») — dans ce cas, assurez-vous que
Chrome est bien installé sur la machine.

---

## ⚙️ Configuration (`config.yaml`)

Tout se règle dans `config.yaml`. Le fichier fourni est déjà rempli d'exemples
et de commentaires détaillés pour chaque option — ouvrez-le et lisez-le, il
est conçu pour être compréhensible même sans savoir coder.

Les points essentiels :

### Obtenir une URL de recherche Vinted

1. Allez sur [vinted.fr](https://www.vinted.fr) dans votre navigateur.
2. Configurez votre recherche (catégorie, marque, taille, état, mot-clé...)
   — **inutile** de régler le tri ou le prix minimum, le script s'en charge
   automatiquement à chaque cycle.
3. Copiez l'adresse complète dans la barre du navigateur.
4. Collez-la dans le champ `url` de `config.yaml`.

### Pages à surveiller

`page_debut` et `page_fin` définissent la zone observée (par exemple 9 à 15,
comme demandé, pour éviter le bruit des annonces flambant neuves de la
page 1).

### Prix minimum

`prix_minimum_possibles: [38, 40, 43]` — le script en tire un au hasard à
chaque cycle. Vous pouvez ajouter/retirer des valeurs.

### Notifications Discord (optionnel)

1. Dans Discord, allez dans les paramètres du salon souhaité → **Intégrations
   → Webhooks → Nouveau webhook**.
2. Copiez l'**URL du webhook**.
3. Dans `config.yaml`, mettez `active: true` et collez l'URL dans
   `webhook_url`.

### Notifications Telegram (optionnel)

1. Discutez avec **[@BotFather](https://t.me/BotFather)** sur Telegram,
   envoyez `/newbot` et suivez les instructions : vous obtiendrez un
   **token** (une longue chaîne de caractères).
2. Démarrez une conversation avec votre nouveau bot (cherchez son nom
   d'utilisateur et envoyez-lui n'importe quel message).
3. Récupérez votre **chat_id** : le plus simple est d'ouvrir dans un
   navigateur `https://api.telegram.org/bot<VOTRE_TOKEN>/getUpdates` juste
   après avoir envoyé un message au bot, et de repérer le nombre après
   `"chat":{"id":`.
4. Dans `config.yaml`, mettez `active: true` et renseignez `bot_token` et
   `chat_id`.

> 💡 Astuce sécurité (facultative) : au lieu d'écrire le webhook/token en
> clair dans `config.yaml`, vous pouvez définir des variables d'environnement
> `VINTED_DISCORD_WEBHOOK`, `VINTED_TELEGRAM_TOKEN` et `VINTED_TELEGRAM_CHAT_ID`
> — elles seront utilisées en priorité si elles existent. Utile si vous
> comptez partager ou publier ce dossier quelque part (ex. un dépôt Git).

---

## ▶️ Lancer le script

Assurez-vous que l'environnement virtuel est activé (vous voyez `(.venv)`
dans le terminal), puis :

```bash
python moniteur_vinted.py
```

Le script tourne alors en continu jusqu'à ce que vous l'arrêtiez. Il affiche
dans le terminal ce qu'il fait à chaque étape.

### Tester la configuration sans attendre

Pour vérifier rapidement que tout fonctionne (un seul cycle, puis arrêt) :

```bash
python moniteur_vinted.py --une-fois
```

C'est la meilleure façon de valider votre configuration avant de laisser le
script tourner pendant des heures.

### Laisser tourner en arrière-plan longtemps

Pour un usage prolongé, voir la section **🚀 Déploiement** juste en dessous.

---

## 🚀 Déploiement : faire tourner le script en continu, sur la durée

Plusieurs façons de faire tourner `moniteur_vinted.py` durablement. Voici la
recommandation, et pourquoi.

### 🏆 Recommandation : sur votre propre ordinateur

Pour un outil personnel et discret comme celui-ci, **mieux vaut le laisser
tourner sur votre PC/Mac plutôt que sur un serveur loué (VPS)**, pour deux
raisons :

1. **C'est déjà prêt** : vous avez tout installé selon les étapes
   précédentes, aucune manipulation supplémentaire n'est nécessaire.
2. **C'est plus discret.** Votre ordinateur utilise l'adresse IP de votre
   connexion internet personnelle (une IP « résidentielle »), exactement
   comme n'importe quel visiteur ordinaire de Vinted. Un serveur loué (VPS)
   utilise à l'inverse une adresse IP de centre de données — un type
   d'adresse que les systèmes anti-fraude des sites web associent bien plus
   souvent à des robots ou du scraping, donc plus facilement suspecté. Pour
   un objectif de discrétion, votre propre connexion est donc un meilleur
   choix technique, pas seulement le plus simple.

L'inconvénient : votre ordinateur doit rester allumé (et ne pas se mettre en
veille) pendant que la surveillance tourne.

#### Empêcher la mise en veille

- **Windows** : Paramètres → Système → Alimentation → mettez « Mise en
  veille » sur « Jamais » (au moins pendant vos sessions de surveillance).
- **macOS** : Réglages Système → Économiseur d'énergie → décochez la mise en
  veille automatique. Astuce : `caffeinate -i ./demarrer.sh` empêche la
  veille uniquement pendant que le script tourne.
- **Linux** : selon l'environnement de bureau, Paramètres → Énergie.

#### Redémarrage automatique en cas de plantage

Deux petits scripts sont fournis pour relancer automatiquement le moniteur
s'il s'arrête de façon inattendue (un Ctrl+C reste, lui, un arrêt volontaire
et propre, sans redémarrage) :

- **Windows** : double-cliquez sur `demarrer.bat`.
- **macOS / Linux** :
  ```bash
  chmod +x demarrer.sh   # une seule fois
  ./demarrer.sh
  ```

#### Continuer même en fermant la fenêtre de terminal (macOS/Linux)

Lancez-le dans une session `tmux` (ou `screen`), qui continue à tourner même
si vous fermez le terminal :

```bash
tmux new -s vinted       # crée une session nommée "vinted"
./demarrer.sh
# puis Ctrl+B, relâchez, puis D pour « détacher » la session (ça continue en arrière-plan)
# pour revenir plus tard : tmux attach -t vinted
```

Sous Windows, le plus simple est de laisser la fenêtre de `demarrer.bat`
ouverte (éventuellement réduite dans la barre des tâches), ou d'utiliser le
Planificateur de tâches pour le lancer automatiquement à l'ouverture de
session.

### 🏠 Alternative pour du vrai 24h/24 : un mini-PC ou Raspberry Pi à la maison

Si vous voulez une disponibilité continue sans laisser votre PC principal
allumé, un petit ordinateur dédié à la maison (mini-PC, vieux PC recyclé, ou
Raspberry Pi) est une bonne option : toujours allumé, mais toujours sur
**votre propre** adresse IP résidentielle (donc toujours discret), et sans
abonnement mensuel. Suivez les mêmes étapes d'installation que dans ce
README.

⚠️ Cas particulier du Raspberry Pi : Playwright y fonctionne, mais le
support de Chromium sur processeur ARM y est moins mature et éprouvé que sur
un PC classique (Intel/AMD), et l'installation peut demander un peu plus de
patience et de dépannage. Si vous n'êtes pas à l'aise avec ça, un vieux
PC/mini-PC classique sera plus simple à mettre en route.

### ☁️ Un serveur distant (VPS) : possible, mais pas mon premier conseil ici

Un VPS (quelques euros par mois chez des hébergeurs comme Hetzner, OVH,
Scaleway...) fonctionne techniquement très bien pour ce script, et a un vrai
avantage : une disponibilité 24h/24 indépendante de votre matériel
personnel. Mais pour ce script précis, dont l'objectif affiché est la
discrétion, l'inconvénient de l'IP de centre de données mentionné plus haut
joue contre ce choix. Si malgré tout vous préférez cette option (par exemple
si vous avez déjà un VPS pour autre chose), dites-le-moi : je peux détailler
l'installation (dépendances système Linux pour Chromium, service qui
redémarre le script automatiquement, etc.).

---

## ⏹️ Arrêter proprement

- **Arrêt complet** : dans le terminal où le script tourne, appuyez sur
  `Ctrl + C`. Le script ferme proprement le navigateur et la base de données
  avant de quitter — inutile de forcer la fermeture.
- **Pause temporaire** (sans tout arrêter) : créez, dans le même dossier, un
  fichier **vide** nommé exactement `PAUSE` (aucune extension). Le script le
  détecte en quelques secondes et met la surveillance en pause. **Supprimez**
  ce fichier pour reprendre automatiquement.
  - Sous Windows : clic droit → Nouveau → Document texte, renommez-le `PAUSE`
    en supprimant bien le `.txt` à la fin.
  - Sous macOS/Linux : `touch PAUSE` dans le terminal ; `rm PAUSE` pour
    reprendre.

---

## 📊 Comprendre les résultats

- **Dans le terminal** : chaque cycle affiche les pages visitées et le
  nombre d'annonces retenues ; une alerte encadrée `🔥 VENTE / DISPARITION
  DÉTECTÉE` apparaît pour chaque annonce confirmée disparue.
- **`vinted_monitor.log`** : le même journal, conservé sur disque (avec
  rotation automatique pour ne pas grossir indéfiniment).
- **`vinted_monitor.db`** : une base de données SQLite contenant l'historique
  complet (première/dernière observation, statut, etc.). Si vous êtes
  curieux, vous pouvez l'ouvrir avec l'outil gratuit
  [DB Browser for SQLite](https://sqlitebrowser.org/) — aucune compétence de
  code n'est nécessaire, c'est une interface graphique simple.

---

## 🤫 Conseils pour rester le plus discret possible

- **Ne réduisez pas** l'intervalle entre les cycles en dessous de quelques
  minutes : plus c'est fréquent, plus c'est repérable.
- **Ne lancez qu'une seule instance** du script à la fois (n'ouvrez pas
  plusieurs terminaux qui font tourner le script en même temps).
- Gardez un **nombre raisonnable** de recherches et de pages surveillées :
  plutôt 1 à 3 recherches ciblées que dix recherches avec des dizaines de
  pages chacune.
- Laissez la **pause nocturne** activée — un compte « actif » 24h/24, 7j/7
  est justement le genre de motif qui distingue un robot d'un humain.
- Laissez le mécanisme de **détection de blocage** faire son travail : si le
  script signale un blocage/CAPTCHA et se met en pause, ne le relancez pas
  immédiatement en boucle — c'est le signal qu'il faut lever le pied.
  Augmentez si besoin `pause_si_blocage_minutes` ou l'intervalle entre cycles.
  Ce script ne cherche jamais à contourner ce type de protection.
  Ne réduisez pas les pauses aléatoires codées dans le script.
- Ne partagez pas et ne republiez pas en masse les données récupérées.

---

## 🔧 Limites connues et maintenance

- **Estimation, pas certitude** : la « vitesse de vente » est calculée par
  rapport à votre première observation, pas à la date réelle de publication
  de l'annonce.
- **Vinted change parfois son interface.** L'extraction des annonces s'appuie
  volontairement sur un repère stable (les liens `/items/<identifiant>`)
  plutôt que sur l'apparence exacte des pages, pour limiter ce risque. Mais
  si un jour le script ne trouve plus aucune annonce, ou se trompe souvent
  sur le statut « vendu », c'est probablement le signe que Vinted a modifié
  un détail technique de ses pages — il faudra alors ajuster légèrement le
  code (voir les commentaires dans les fonctions `extraire_annonces_de_la_page`
  et `verifier_statut_annonce` de `moniteur_vinted.py`, ou demandez de l'aide
  à quelqu'un qui code, en lui montrant le message d'erreur exact).
- **Faux positifs possibles** : une annonce peut aussi disparaître parce
  qu'elle a été retirée par le vendeur (et non vendue) — le script distingue
  du mieux qu'il peut ces deux cas (« vendu » vs « indisponible »), mais ce
  n'est pas garanti à 100 %.

---

## ❓ Dépannage (FAQ)

**« ModuleNotFoundError » ou erreur d'import au lancement**
→ Vérifiez que l'environnement virtuel est activé (`(.venv)` visible dans le
terminal) et que `pip install -r requirements.txt` s'est bien terminé sans
erreur.

**« Executable doesn't exist » ou erreur similaire liée à Chromium**
→ Vous avez oublié l'étape `playwright install chromium`. Relancez-la.

**Le script tourne mais ne trouve aucune annonce**
→ Vérifiez d'abord que l'URL collée dans `config.yaml` fonctionne bien dans
un navigateur classique. Essayez de mettre `headless: false` temporairement
dans `config.yaml` pour **voir** ce que fait le navigateur automatisé.
Vérifiez aussi que les pages demandées (`page_debut`/`page_fin`) contiennent
bien des résultats pour cette recherche et ce prix minimum.

**Aucune notification Discord/Telegram ne part**
→ Relisez la section Notifications ci-dessus ; vérifiez `vinted_monitor.log`
pour un message d'erreur détaillé (webhook mal copié, token invalide...).

**Le script signale un blocage/CAPTCHA**
→ C'est normal que ça arrive occasionnellement. Laissez le script observer
sa pause automatique. Si cela se reproduit souvent, augmentez l'intervalle
entre les cycles dans `config.yaml`.

**Comment tout remettre à zéro (oublier les annonces déjà vues) ?**
→ Arrêtez le script (Ctrl+C), supprimez le fichier `vinted_monitor.db`, puis
relancez. Un nouvel historique repart de zéro.

---

Bon usage, et rappelez-vous : mieux vaut un script lent et discret qui tourne
longtemps qu'un script agressif qui se fait bloquer au bout d'une heure. 🙂
