# 🕵️ Moniteur Vinted

Un script Python qui surveille automatiquement une ou plusieurs recherches
Vinted pour repérer les annonces qui **se vendent vite**, sans jamais se
connecter à un compte, avec un rythme lent et volontairement discret.

---

## ⚠️ À lire avant de commencer

Ce script pilote un vrai navigateur pour consulter des pages **publiques**
de Vinted (aucune connexion à un compte, aucun appel aux API internes).
Malgré cela :

- L'automatisation n'est probablement **pas conforme aux conditions
  d'utilisation** de Vinted (comme sur la plupart des sites). Ce script est
  fourni à titre **personnel et éducatif**, pour un usage raisonnable, à
  faible fréquence. **Vous restez seul responsable** de son usage.
- Vinted peut à tout moment limiter ou bloquer un comportement jugé
  automatisé. Face à un blocage (CAPTCHA, page de vérification...), le
  script est conçu pour **reculer et attendre**, jamais pour forcer le
  passage : pas de résolution de CAPTCHA, pas de changement d'IP,
  pas d'acharnement. C'est un signal pour réduire la fréquence, pas insister.
- Ne partagez jamais un `config.yaml` rempli avec un vrai webhook
  Discord/token Telegram.

Si vous n'êtes pas à l'aise avec ces conditions, n'utilisez pas ce script.

---

## 🧠 Comment ça marche

À chaque cycle, le script :
1. Ouvre un vrai navigateur (Chromium) et charge vos recherches, triées par
   **« Plus récent »**, sur les pages basses (1-2) où une annonce qui se
   vend vite est encore visible ;
2. Note les annonces visibles (identifiant, titre, prix, lien) ;
3. Compare avec les cycles précédents, et va vérifier directement la page
   de toute annonce qui a disparu de la zone surveillée (pour confirmer
   avant de l'annoncer comme disparue — voir plus bas) ;
4. Affiche un résumé (`X nouvelles, Y suspectes, Z confirmées disparues`)
   et une alerte pour chaque disparition confirmée, avec le temps écoulé
   depuis la première observation ;
5. Si la disparition est "rapide" (fourchette réglable), envoie aussi une
   notification Discord/Telegram si configuré ;
6. Attend un temps aléatoire (4 à 7 minutes par défaut) avant de
   recommencer, sauf la nuit (aucune activité entre minuit et 7h).

⏱️ La « vitesse de vente » est une **estimation** entre votre première
observation et la disparition confirmée — pas forcément la date de mise en
ligne réelle de l'annonce.

Une annonce n'est **jamais abandonnée en silence** : si elle glisse hors de
la zone surveillée sans avoir disparu de Vinted, le script continue de la
vérifier périodiquement (les moins récemment vérifiées en priorité) jusqu'à
connaître son sort réel.

---

## 📁 Structure des fichiers

```
Vinted/
├── moniteur_vinted.py     # le script principal (celui que vous lancez)
├── config.yaml             # votre configuration — lisez ses commentaires,
│                            #   chaque option y est expliquée en détail
├── requirements.txt        # dépendances Python à installer
├── demarrer.sh / .bat       # lancement + redémarrage auto si plantage
├── tableau_de_bord.py       # interface web locale pour consulter les résultats
├── tableau_de_bord.sh / .bat # lance le tableau de bord
├── README.md                 # ce fichier
│
# Créés automatiquement à l'usage :
├── vinted_monitor.db        # mémoire des annonces (SQLite)
├── vinted_monitor.log       # journal détaillé
└── storage_state.json       # session de navigateur sauvegardée
```

---

## 🛠️ Installation

**0. Python 3.10+** — vérifiez avec `python --version` (Windows) ou
`python3 --version` (macOS/Linux). Si absent ou trop ancien, installez-le
depuis [python.org/downloads](https://www.python.org/downloads/) — sous
Windows, **cochez bien « Add python.exe to PATH »** pendant l'installation.

**1.** Placez tous les fichiers dans un même dossier, ouvrez un terminal
dedans (Windows : tapez `cmd` dans la barre d'adresse de l'Explorateur ;
macOS/Linux : clic droit → « Ouvrir un terminal ici »).

**2. Environnement virtuel** (recommandé, évite d'installer globalement) :

```bash
# Windows :
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux :
python3 -m venv .venv
source .venv/bin/activate
```

⚠️ Sous Windows, utilisez toujours `python`, jamais `python3` (qui déclenche
le faux message Windows renvoyant vers le Store — voir Dépannage). Vous
devez voir `(.venv)` apparaître dans le terminal ; **réactivez-le à chaque
nouvelle session de terminal**.

**3. Dépendances :**

```bash
pip install -r requirements.txt
playwright install chromium
```

(La deuxième commande télécharge le navigateur piloté par le script,
quelques centaines de Mo, une seule fois. Pour utiliser votre Chrome déjà
installé à la place, mettez `canal: "chrome"` dans `config.yaml`.)

---

## ⚙️ Configuration

Tout se règle dans `config.yaml`, déjà rempli d'exemples et de commentaires
détaillés section par section — ouvrez-le et lisez-le, il est conçu pour
être compréhensible sans savoir coder. En résumé :

- **Recherches à surveiller** : allez sur [vinted.fr](https://www.vinted.fr),
  réglez une recherche (catégorie, marque, taille...) — inutile de régler le
  tri ou le prix minimum, le script s'en charge — puis collez l'URL dans un
  bloc `recherches:`. Jusqu'à 10 recherches, dupliquez un bloc par catégorie.
- **Rotation** (`rotation: true`) : si vous suivez beaucoup de catégories,
  n'en traite qu'une par cycle (tour à tour) pour ne pas rallonger chaque
  cycle — au prix d'une fréquence de revérification plus faible par
  catégorie.
- **Prix minimum** (`prix_minimum`) optionnel : un seuil fixe appliqué
  partout, ou laissez vide pour ne filtrer par aucun prix.
- **Seuil de vente rapide** (`notifications.seuil_vente_rapide`) : la
  fourchette de temps (min/max en minutes) qui déclenche une notification
  Discord/Telegram — le terminal, lui, affiche toujours toutes les
  disparitions confirmées.
- **Discord** : salon → Intégrations → Webhooks → Nouveau webhook → copiez
  l'URL dans `webhook_url`, mettez `active: true`.
- **Telegram** : parlez à [@BotFather](https://t.me/BotFather) (`/newbot`)
  pour un `bot_token`, envoyez un message à votre bot, puis ouvrez
  `https://api.telegram.org/bot<TOKEN>/getUpdates` pour trouver le
  `chat_id` (nombre après `"chat":{"id":`).

> 💡 Alternative au webhook/token en clair : variables d'environnement
> `VINTED_DISCORD_WEBHOOK`, `VINTED_TELEGRAM_TOKEN`, `VINTED_TELEGRAM_CHAT_ID`
> (utilisées en priorité si présentes).

---

## ▶️ Lancer / ⏹️ arrêter

```bash
python moniteur_vinted.py              # tourne en continu
python moniteur_vinted.py --une-fois   # un seul cycle, pour tester la config
```

Pour un usage prolongé avec redémarrage automatique en cas de plantage
(un Ctrl+C reste un arrêt volontaire, sans redémarrage) :
- **Windows** : double-cliquez `demarrer.bat`.
- **macOS/Linux** : `chmod +x demarrer.sh` (une fois) puis `./demarrer.sh`.

Le script tourne sur **votre propre ordinateur**, avec votre IP
résidentielle habituelle — c'est à la fois le plus simple et le plus
discret (une IP de serveur loué serait plus facilement suspectée d'être un
robot). Il doit donc rester allumé, sans mise en veille, pendant la
surveillance.

**Arrêter** : `Ctrl+C` dans le terminal (fermeture propre).
**Pause temporaire** : créez un fichier vide nommé `PAUSE` dans le dossier
(le script la détecte en quelques secondes) ; supprimez-le pour reprendre.

---

## 📊 Comprendre les résultats

- **Terminal / `vinted_monitor.log`** : chaque cycle affiche les pages
  visitées et un résumé ; une alerte encadrée apparaît pour chaque
  disparition confirmée.
- **`vinted_monitor.db`** (SQLite) : historique complet. Consultable sans
  coder avec [DB Browser for SQLite](https://sqlitebrowser.org/).
- **Tableau de bord** (`tableau_de_bord.py`) : une page web locale
  (`http://localhost:8080`, rien n'est envoyé sur internet) qui lit
  uniquement cette base — cartes de statistiques, tableau par catégorie,
  dernières ventes avec badge ⚡ pour les plus rapides, et les tendances
  (mots/marques qui reviennent le plus dans les ventes confirmées). Se
  lance à part (`tableau_de_bord.bat` ou `./tableau_de_bord.sh`) et peut
  tourner en même temps que le moniteur, sans jamais rien modifier.

---

## 🤫 Rester discret

- Ne réduisez pas l'intervalle entre cycles en dessous de quelques minutes.
- Une seule instance du script à la fois.
- Peu de recherches/pages plutôt que beaucoup (1 à 3 recherches ciblées).
- Laissez la pause nocturne activée.
- Si le script signale un blocage, laissez-le patienter — ne le relancez
  pas en boucle, et n'augmentez pas la fréquence en réaction.

---

## 🔧 Limites connues

- La « vitesse de vente » est une estimation (basée sur votre première
  observation, pas la date de publication réelle).
- Le script distingue du mieux qu'il peut une vente d'un simple retrait par
  le vendeur (plusieurs signaux combinés : titre de page, messages
  visibles, présence du bouton d'achat), mais ce n'est jamais garanti à
  100 % — en cas de doute, il préfère ne pas conclure plutôt que se tromper.
- Si Vinted change son interface et que le script ne trouve plus
  d'annonces (ou se trompe souvent), les fonctions à ajuster sont
  `extraire_annonces_de_la_page` et `verifier_statut_annonce` dans
  `moniteur_vinted.py`.

---

## ❓ Dépannage

**Windows : « Python est introuvable ; installez-le depuis le Microsoft
Store » (ou code 9009)** → Python n'est pas réellement installé/accessible :
1. Installez-le depuis [python.org/downloads](https://www.python.org/downloads/)
   (pas le Store), en cochant **« Add python.exe to PATH »**.
2. Désactivez les faux raccourcis : touche Windows → « alias » → «
   Paramètres des alias d'exécution des applications » → `python.exe` et
   `python3.exe` sur Désactivé.
3. **Fermez et rouvrez** le terminal, vérifiez `python --version`.
4. Refaites l'installation (venv, `pip install`, `playwright install`).
5. Utilisez toujours `python`, jamais `python3`, sous Windows.

**« Le chargement d'une version test est bloqué par la stratégie »**
(en installant depuis le Store) → Paramètres → Applications → Paramètres
avancés des applications → « Choisir l'emplacement... » → « N'importe où ».

**`ModuleNotFoundError`** → l'environnement virtuel n'est pas activé
(`(.venv)` doit être visible), ou `pip install -r requirements.txt` a
échoué.

**« Executable doesn't exist » (Chromium)** → lancez
`playwright install chromium`.

**Aucune annonce trouvée** → testez l'URL dans un navigateur classique ;
mettez temporairement `headless: false` pour voir ce que fait le
navigateur ; vérifiez que `page_debut`/`page_fin` contiennent des résultats.

**Aucune notification Discord/Telegram** → relisez la section
Configuration ; regardez `vinted_monitor.log` pour le message d'erreur
exact.

**Tout remettre à zéro** → arrêtez le script, supprimez
`vinted_monitor.db`, relancez.

---

Bon usage, et rappelez-vous : mieux vaut un script lent et discret qui
tourne longtemps qu'un script agressif qui se fait bloquer au bout d'une
heure. 🙂
