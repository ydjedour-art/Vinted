#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
========================================================================
 MONITEUR VINTED — Surveillance de recherches et détection de ventes rapides
========================================================================

Ce script ouvre régulièrement un vrai navigateur (Chromium, piloté par
Playwright) pour consulter des pages de résultats de recherche Vinted
PUBLIQUES (sans jamais se connecter à un compte), et repère les annonces
qui disparaissent rapidement de la zone surveillée — signe probable
qu'elles ont été vendues vite.

Comportement volontairement peu fréquent et respectueux du site :
  - un seul onglet à la fois,
  - délais aléatoires entre chaque action (pas de rythme robotique),
  - petits défilements progressifs, comme une personne qui lit,
  - aucune activité entre minuit et 7h (plage configurable),
  - fréquence faible et configurable (8 à 15 minutes par défaut),
  - jamais de connexion à un compte Vinted,
  - jamais d'appel direct aux API internes de Vinted : uniquement des
    pages web normales, comme un visiteur ordinaire,
  - si une page de blocage / CAPTCHA est détectée, le script s'arrête
    et attend plus longtemps au lieu d'insister.

⚠️  À lire avant utilisation
    L'utilisation d'un outil automatisé sur Vinted n'est probablement pas
    conforme à ses conditions d'utilisation. Ce script est fourni à titre
    personnel et éducatif, pour un usage raisonnable et à faible fréquence.
    Vous restez seul responsable de la manière dont vous l'utilisez.
    Voir le README.md (section « Avertissement ») pour plus de détails.

Utilisation :
    python moniteur_vinted.py                  # lancement normal (boucle continue)
    python moniteur_vinted.py --une-fois        # un seul cycle, pour tester la config
    python moniteur_vinted.py --config autre.yaml

Arrêt propre :
    Ctrl+C dans le terminal.

Pause temporaire (sans arrêter le script) :
    Créez un fichier vide nommé "PAUSE" dans ce dossier.
    Supprimez ce fichier pour reprendre la surveillance.
========================================================================
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
import yaml
from playwright.sync_api import TimeoutError as ErreurDelaiPlaywright
from playwright.sync_api import sync_playwright


class BlocageDetecte(Exception):
    """Levée en interne quand Vinted semble afficher une page de blocage ou un CAPTCHA.

    Dans ce cas, le script préfère s'arrêter et attendre plutôt que d'insister :
    l'objectif est de rester discret, jamais de forcer le passage.
    """


# ========================================================================
# SECTION 1 — CONFIGURATION (chargement et vérification de config.yaml)
# ========================================================================

def charger_configuration(chemin: str) -> dict:
    """Charge et valide le fichier config.yaml. Arrête le script proprement
    avec un message clair en français si quelque chose ne va pas."""
    if not os.path.exists(chemin):
        raise SystemExit(
            f"❌ Fichier de configuration introuvable : {chemin}\n"
            f"   Vérifiez qu'un fichier 'config.yaml' existe bien dans ce dossier,\n"
            f"   ou indiquez son emplacement avec l'option --config."
        )

    with open(chemin, "r", encoding="utf-8") as fichier:
        try:
            config = yaml.safe_load(fichier)
        except yaml.YAMLError as erreur:
            raise SystemExit(f"❌ Le fichier {chemin} contient une erreur de syntaxe YAML :\n{erreur}")

    if not isinstance(config, dict):
        raise SystemExit(f"❌ Le fichier {chemin} est vide ou mal formé.")

    valider_configuration(config)
    return config


def valider_configuration(config: dict) -> None:
    """Vérifie que les champs indispensables sont présents, avec des messages
    d'erreur explicites plutôt qu'un plantage Python difficile à comprendre."""
    if not config.get("recherches"):
        raise SystemExit("❌ La section 'recherches' est vide ou absente dans config.yaml.")

    for indice, recherche in enumerate(config["recherches"], start=1):
        for champ in ("nom", "url", "page_debut", "page_fin"):
            if champ not in recherche:
                raise SystemExit(f"❌ La recherche n°{indice} n'a pas de champ '{champ}' dans config.yaml.")
        if recherche["page_debut"] > recherche["page_fin"]:
            raise SystemExit(
                f"❌ Recherche « {recherche['nom']} » : 'page_debut' doit être inférieur ou égal à 'page_fin'."
            )

    if not config.get("prix_minimum_possibles"):
        raise SystemExit("❌ La liste 'prix_minimum_possibles' est vide ou absente dans config.yaml.")

    intervalle = config.get("intervalle_minutes", {})
    if not intervalle.get("min") or not intervalle.get("max"):
        raise SystemExit("❌ La section 'intervalle_minutes' (min/max) est incomplète dans config.yaml.")
    if intervalle["min"] > intervalle["max"]:
        raise SystemExit("❌ Dans 'intervalle_minutes', 'min' doit être inférieur ou égal à 'max'.")


# ========================================================================
# SECTION 2 — LOGS (affichage console + fichier de log)
# ========================================================================

def configurer_logs(config: dict) -> None:
    """Met en place les logs : affichage dans le terminal ET écriture dans un
    fichier (qui ne grossit pas indéfiniment grâce à la rotation automatique)."""
    cfg_logs = config.get("logs", {})
    niveau = getattr(logging, str(cfg_logs.get("niveau", "INFO")).upper(), logging.INFO)
    chemin_fichier = cfg_logs.get("fichier", "vinted_monitor.log")

    formatteur = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    gestionnaire_fichier = RotatingFileHandler(chemin_fichier, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    gestionnaire_fichier.setFormatter(formatteur)

    gestionnaire_console = logging.StreamHandler(sys.stdout)
    gestionnaire_console.setFormatter(formatteur)

    racine = logging.getLogger()
    racine.setLevel(niveau)
    racine.handlers.clear()
    racine.addHandler(gestionnaire_fichier)
    racine.addHandler(gestionnaire_console)


# ========================================================================
# SECTION 3 — BASE DE DONNÉES (SQLite : mémoire des annonces déjà vues)
# ========================================================================
#
# Une seule table "annonces" retient, pour chaque recherche, la première et
# la dernière fois où une annonce a été vue, ainsi que son statut :
#   - 'active'       : vue lors du dernier passage, dans la zone surveillée
#   - 'hors_zone'     : toujours en ligne, mais a glissé au-delà de la
#                        dernière page surveillée (donc pas "vendue")
#   - 'vendu'         : disparue et confirmée comme vendue
#   - 'indisponible'  : disparue et confirmée comme retirée/supprimée
# ========================================================================

def initialiser_base_de_donnees(chemin_fichier: str) -> sqlite3.Connection:
    connexion = sqlite3.connect(chemin_fichier)
    connexion.execute(
        """
        CREATE TABLE IF NOT EXISTS annonces (
            item_id TEXT NOT NULL,
            recherche TEXT NOT NULL,
            titre TEXT,
            prix REAL,
            url TEXT,
            premiere_observation TEXT NOT NULL,
            derniere_observation TEXT NOT NULL,
            statut TEXT NOT NULL DEFAULT 'active',
            disparition_detectee TEXT,
            PRIMARY KEY (item_id, recherche)
        )
        """
    )
    connexion.commit()
    return connexion


def enregistrer_observation(bd: sqlite3.Connection, recherche: str, annonce: dict, maintenant: str) -> None:
    """Ajoute une annonce vue pour la première fois, ou met à jour sa dernière
    observation si elle était déjà connue (upsert)."""
    bd.execute(
        """
        INSERT INTO annonces (item_id, recherche, titre, prix, url, premiere_observation, derniere_observation, statut)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        ON CONFLICT(item_id, recherche) DO UPDATE SET
            derniere_observation = excluded.derniere_observation,
            titre = excluded.titre,
            prix = excluded.prix,
            statut = 'active'
        """,
        (annonce["id"], recherche, annonce["titre"], annonce["prix"], annonce["url"], maintenant, maintenant),
    )
    bd.commit()


def obtenir_annonces_a_surveiller(bd: sqlite3.Connection, recherche: str) -> list[dict]:
    """Renvoie les annonces actuellement considérées comme actives dans la zone
    surveillée pour cette recherche (celles qu'il faut comparer au scan actuel)."""
    curseur = bd.execute(
        "SELECT item_id, titre, prix, url, premiere_observation FROM annonces WHERE recherche = ? AND statut = 'active'",
        (recherche,),
    )
    colonnes = [description[0] for description in curseur.description]
    return [dict(zip(colonnes, ligne)) for ligne in curseur.fetchall()]


def marquer_statut(bd: sqlite3.Connection, recherche: str, item_id: str, statut: str, maintenant: str) -> None:
    bd.execute(
        "UPDATE annonces SET statut = ?, disparition_detectee = ? WHERE item_id = ? AND recherche = ?",
        (statut, maintenant, item_id, recherche),
    )
    bd.commit()


def marquer_hors_zone(bd: sqlite3.Connection, recherche: str, item_id: str) -> None:
    """L'annonce est toujours en ligne mais a glissé au-delà de la dernière page
    surveillée : ce n'est pas une vente, on arrête simplement de la re-vérifier
    à chaque cycle (pour ne pas multiplier les requêtes inutilement)."""
    bd.execute(
        "UPDATE annonces SET statut = 'hors_zone' WHERE item_id = ? AND recherche = ?",
        (item_id, recherche),
    )
    bd.commit()


# ========================================================================
# SECTION 4 — COMPORTEMENT « HUMAIN » (pauses et défilement naturels)
# ========================================================================

def pause_aleatoire(minimum: float, maximum: float) -> None:
    """Attend un temps aléatoire (en secondes) entre `minimum` et `maximum`,
    pour éviter des intervalles parfaitement réguliers et robotiques."""
    time.sleep(random.uniform(minimum, maximum))


def defilement_naturel(page) -> None:
    """Fait défiler la page vers le bas par petites étapes irrégulières, avec
    de courtes pauses, pour imiter la lecture d'un utilisateur humain plutôt
    qu'un saut instantané tout en bas de la page."""
    try:
        for _ in range(15):  # limite de sécurité, pour ne jamais boucler indéfiniment
            hauteur_totale = page.evaluate("document.body.scrollHeight")
            position_actuelle = page.evaluate("window.scrollY")
            hauteur_visible = page.viewport_size["height"] if page.viewport_size else 800

            if position_actuelle + hauteur_visible >= hauteur_totale - 100:
                break  # on est arrivé (à peu près) en bas de la page

            pas = random.randint(250, 650)
            page.mouse.wheel(0, pas)
            pause_aleatoire(0.4, 1.3)
    except Exception as erreur:
        logging.debug(f"Défilement interrompu sans conséquence : {erreur}")


# ========================================================================
# SECTION 5 — NAVIGATEUR (lancement Playwright et gestion des cookies)
# ========================================================================

def lancer_navigateur(playwright, config: dict):
    cfg_nav = config.get("navigateur", {})
    parametres = {"headless": cfg_nav.get("headless", True)}

    canal = cfg_nav.get("canal", "chromium")
    if canal and canal.lower() != "chromium":
        # Permet d'utiliser le vrai Google Chrome installé sur la machine (ex: "chrome")
        # au lieu du Chromium fourni avec Playwright.
        parametres["channel"] = canal

    return playwright.chromium.launch(**parametres)


def creer_contexte(navigateur, config: dict, chemin_storage_state: str):
    """Crée une session de navigation. Si une session précédente a été sauvegardée
    (cookies, choix du bandeau de cookies...), elle est réutilisée : cela évite de
    reproduire une "première visite" à chaque cycle, ce qui est plus naturel."""
    cfg_nav = config.get("navigateur", {})
    parametres = {
        "viewport": {
            "width": cfg_nav.get("largeur_fenetre", 1366),
            "height": cfg_nav.get("hauteur_fenetre", 768),
        },
        "locale": "fr-FR",
        "timezone_id": config.get("fuseau_horaire", "Europe/Paris"),
    }
    if os.path.exists(chemin_storage_state):
        parametres["storage_state"] = chemin_storage_state

    contexte = navigateur.new_context(**parametres)

    # Les navigateurs pilotés par automatisation (Playwright, Selenium...)
    # exposent par défaut `navigator.webdriver = true`, une propriété que
    # certains sites lisent pour repérer instantanément un navigateur
    # automatisé — avant même d'avoir observé le moindre comportement. Cette
    # propriété vaut `undefined` chez un vrai navigateur classique ; on
    # aligne notre navigateur dessus. Il ne s'agit pas de forcer le passage
    # d'un blocage actif (le script continue de reculer face à un vrai
    # CAPTCHA, voir page_bloquee_ou_captcha), seulement de ne pas trahir
    # gratuitement l'outil par un simple réglage par défaut.
    contexte.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )

    return contexte


def gerer_bandeau_cookies(page) -> None:
    """Accepte le bandeau de consentement aux cookies s'il est affiché. Cela
    n'arrive normalement qu'à la toute première visite : ensuite, le choix est
    mémorisé grâce à la session sauvegardée (storage_state)."""
    selecteurs_possibles = [
        "#onetrust-accept-btn-handler",
        "button:has-text('Tout accepter')",
        "button:has-text('Accepter')",
    ]
    for selecteur in selecteurs_possibles:
        try:
            bouton = page.locator(selecteur).first
            bouton.wait_for(state="visible", timeout=3000)
            pause_aleatoire(0.4, 1.2)
            bouton.click()
            pause_aleatoire(0.5, 1.0)
            return
        except Exception:
            continue  # ce sélecteur n'est pas présent ; on essaie le suivant, ou on abandonne


def page_bloquee_ou_captcha(page) -> bool:
    """Détecte une éventuelle page de blocage ou de vérification anti-robot,
    à distinguer d'une page de résultats normale. Si c'est le cas, mieux vaut
    s'arrêter et attendre plutôt qu'insister : ce script est conçu pour être
    discret, pas pour forcer un passage en force.

    Important : on ne cherche PAS ces indices dans tout le code source de la
    page (page.content()). Une page de résultats parfaitement normale peut
    contenir des mots comme "captcha" quelque part dans son code (scripts de
    protection anti-fraude présents en permanence sur tout le site, par
    exemple), sans qu'aucun blocage ne soit réellement affiché à l'écran — ce
    qui déclencherait une fausse alerte en permanence. On se base donc sur
    des signaux bien plus spécifiques : le TITRE de l'onglet, et le texte
    réellement VISIBLE à l'écran, pas le code source complet.
    """
    try:
        titre = (page.title() or "").lower()
    except Exception:
        titre = ""

    titres_suspects = [
        "just a moment",
        "attention required",
        "access denied",
        "are you a robot",
        "are you human",
        "security check",
        "checking your browser",
        "vérification de sécurité",
    ]
    if any(t in titre for t in titres_suspects):
        return True

    try:
        texte_visible = page.inner_text("body").lower()
    except Exception:
        return False

    phrases_suspectes = [
        "veuillez patienter pendant que nous vérifions",
        "confirmez que vous êtes humain",
        "trafic inhabituel détecté",
        "unusual traffic from your computer",
    ]
    return any(phrase in texte_visible for phrase in phrases_suspectes)


# ========================================================================
# SECTION 6 — CONSTRUCTION DES URLS DE RECHERCHE
# ========================================================================

def construire_url_recherche(url_base: str, prix_min: int, numero_page: int) -> str:
    """Reconstruit l'URL de recherche Vinted en forçant :
       - le tri par "Plus récent"  -> order=newest_first
       - le prix minimum du cycle -> price_from
       - le numéro de page voulu  -> page
    Tous les autres filtres déjà présents dans l'URL fournie par l'utilisateur
    (catégorie, marque, taille, texte recherché, etc.) sont conservés tels quels.
    """
    morceaux = urlparse(url_base)
    parametres = parse_qs(morceaux.query)

    parametres["order"] = ["newest_first"]
    parametres["price_from"] = [str(prix_min)]
    parametres["page"] = [str(numero_page)]

    nouvelle_requete = urlencode(parametres, doseq=True)
    return urlunparse(morceaux._replace(query=nouvelle_requete))


def url_absolue(href: str, url_de_base: str) -> str:
    """Transforme un lien relatif (ex: "/items/123-titre") en URL complète, en
    réutilisant le domaine de l'URL de recherche fournie (vinted.fr, .de, .es...)."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    origine = urlparse(url_de_base)
    return f"{origine.scheme}://{origine.netloc}{href}"


# ========================================================================
# SECTION 7 — EXTRACTION DES ANNONCES VISIBLES SUR UNE PAGE
# ========================================================================
#
# Note de maintenance : Vinted peut modifier la structure de ses pages au fil
# du temps. Cette fonction s'appuie volontairement sur un repère très stable
# (les liens vers "/items/<identifiant>") plutôt que sur des classes CSS, qui
# changent bien plus souvent. Si un jour l'extraction ne trouve plus rien,
# ouvrez une page de résultats dans un navigateur, faites clic droit ->
# "Inspecter" sur une annonce, et ajustez au besoin le sélecteur ci-dessous.
# ========================================================================

def extraire_annonces_de_la_page(page, url_de_base: str) -> list[dict]:
    """Extrait les annonces visibles sur la page de résultats actuellement
    affichée. Retourne une liste de dictionnaires {id, titre, prix, url}."""
    annonces: list[dict] = []
    ids_deja_traites: set[str] = set()

    try:
        liens = page.locator('a[href*="/items/"]')
        total_liens = liens.count()
    except Exception as erreur:
        logging.warning(f"Impossible de lire les annonces de cette page : {erreur}")
        return annonces

    for indice in range(total_liens):
        try:
            lien = liens.nth(indice)
            href = lien.get_attribute("href")
            if not href:
                continue

            correspondance = re.search(r"/items/(\d+)", href)
            if not correspondance:
                continue

            item_id = correspondance.group(1)
            if item_id in ids_deja_traites:
                continue  # une même annonce peut avoir plusieurs liens (photo + titre)
            ids_deja_traites.add(item_id)

            # Les attributs d'accessibilité (title / aria-label) sont souvent plus
            # complets et plus fiables que le texte visible, qui peut être tronqué.
            description = lien.get_attribute("title") or lien.get_attribute("aria-label")
            if not description:
                try:
                    description = lien.inner_text()
                except Exception:
                    description = ""

            titre, prix = analyser_description(description)

            annonces.append(
                {
                    "id": item_id,
                    "titre": titre,
                    "prix": prix,
                    "url": url_absolue(href, url_de_base),
                }
            )
        except Exception as erreur:
            logging.debug(f"Annonce ignorée (élément {indice}) : {erreur}")
            continue

    return annonces


def analyser_description(texte: str | None) -> tuple[str, float | None]:
    """Essaie d'extraire un prix (et un titre nettoyé) à partir du texte brut
    associé à une annonce. Le format exact peut varier, on reste tolérant."""
    if not texte:
        return ("Titre indisponible", None)

    texte_nettoye = " ".join(texte.split())

    prix = None
    correspondance_prix = re.search(r"(\d+(?:[.,]\d{2})?)\s?€", texte_nettoye)
    if correspondance_prix:
        try:
            prix = float(correspondance_prix.group(1).replace(",", "."))
        except ValueError:
            prix = None

    titre = texte_nettoye[:150] if texte_nettoye else "Titre indisponible"
    return (titre, prix)


def filtrer_par_mots_cles(annonces: list[dict], recherche: dict) -> list[dict]:
    """Applique les filtres optionnels 'inclure_mots_cles' / 'exclure_mots_cles'
    définis pour cette recherche, en comparant sur le titre extrait (insensible
    à la casse)."""
    inclure = [mot.lower() for mot in recherche.get("inclure_mots_cles", []) or [] if mot]
    exclure = [mot.lower() for mot in recherche.get("exclure_mots_cles", []) or [] if mot]

    if not inclure and not exclure:
        return annonces

    resultat = []
    for annonce in annonces:
        titre = (annonce.get("titre") or "").lower()

        if exclure and any(mot in titre for mot in exclure):
            continue
        if inclure and not any(mot in titre for mot in inclure):
            continue

        resultat.append(annonce)
    return resultat


# ========================================================================
# SECTION 8 — DÉTECTION DES DISPARITIONS (ventes probables)
# ========================================================================

def verifier_statut_annonce(page, url_annonce: str) -> str:
    """Visite la page d'une annonce précise pour déterminer si elle est encore
    active, vendue, ou supprimée/indisponible.

    Cette vérification directe évite les faux positifs : une annonce qui a
    simplement glissé au-delà de la dernière page surveillée (parce que de
    nouvelles annonces sont arrivées devant elle) n'est PAS une vente.

    Renvoie : "active", "vendu", "indisponible" ou "inconnu".
    """
    try:
        page.goto(url_annonce, wait_until="domcontentloaded", timeout=20000)
    except Exception as erreur:
        logging.debug(f"   ↳ Impossible d'ouvrir l'annonce pour vérification : {erreur}")
        return "inconnu"

    pause_aleatoire(1.0, 2.5)

    if page_bloquee_ou_captcha(page):
        raise BlocageDetecte()

    try:
        contenu = page.content().lower()
    except Exception:
        return "inconnu"

    # Vinted affiche généralement un badge "Vendu" sur la photo d'une annonce vendue.
    if "vendu" in contenu or "sold" in contenu:
        return "vendu"

    # Note : ces formulations sont une estimation raisonnable et peuvent nécessiter
    # un ajustement si Vinted change le texte exact de ses messages d'erreur.
    indicateurs_indisponible = [
        "cet article n'est plus disponible",
        "cette annonce n'existe plus",
        "n'existe pas ou n'est plus disponible",
        "page introuvable",
        "page non trouvée",
    ]
    if any(indicateur in contenu for indicateur in indicateurs_indisponible):
        return "indisponible"

    return "active"


def traiter_annonces_disparues(page, nom_recherche: str, ids_vus_ce_cycle: set, config: dict, bd: sqlite3.Connection) -> None:
    """Compare les annonces suivies en base à celles vues pendant ce cycle et
    vérifie individuellement celles qui semblent avoir disparu."""
    suivies = obtenir_annonces_a_surveiller(bd, nom_recherche)
    absentes = [a for a in suivies if a["item_id"] not in ids_vus_ce_cycle]

    if not absentes:
        return

    logging.info(f"   🔍 {len(absentes)} annonce(s) absente(s) de la zone surveillée, vérification...")

    cfg_verif = config.get("verification_avant_disparition", {})

    if not cfg_verif.get("active", True):
        # Mode rapide : on ne vérifie pas individuellement, on considère les
        # annonces absentes comme vendues directement (plus rapide, mais plus
        # sensible aux faux positifs liés à la pagination).
        for annonce in absentes:
            confirmer_disparition(bd, nom_recherche, annonce, "vendu (non vérifié)", config)
        return

    max_verifs = cfg_verif.get("max_verifications_par_cycle", 8)

    for annonce in absentes[:max_verifs]:
        pause_aleatoire(2.0, 5.0)
        statut = verifier_statut_annonce(page, annonce["url"])  # BlocageDetecte remonte naturellement

        if statut == "vendu":
            confirmer_disparition(bd, nom_recherche, annonce, "vendu", config)
        elif statut == "indisponible":
            confirmer_disparition(bd, nom_recherche, annonce, "indisponible (retirée/supprimée)", config)
        elif statut == "active":
            marquer_hors_zone(bd, nom_recherche, annonce["item_id"])
            logging.debug(f"   ↳ Annonce {annonce['item_id']} toujours active, mais hors zone surveillée.")
        # "inconnu" (erreur réseau, etc.) : on ne change rien, nouvelle tentative au prochain cycle


def confirmer_disparition(bd: sqlite3.Connection, nom_recherche: str, annonce: dict, raison: str, config: dict) -> None:
    """Enregistre la disparition confirmée d'une annonce, affiche une alerte et
    envoie les notifications externes si configurées."""
    maintenant_dt = datetime.now(timezone.utc)
    maintenant = maintenant_dt.isoformat()

    statut = "vendu" if raison.startswith("vendu") else "indisponible"
    marquer_statut(bd, nom_recherche, annonce["item_id"], statut, maintenant)

    premiere_dt = datetime.fromisoformat(annonce["premiere_observation"])
    duree = maintenant_dt - premiere_dt
    duree_str = formater_duree(duree)

    afficher_alerte_vente(annonce, duree_str, raison, config)

    seuil = config.get("notifications", {}).get("seuil_vente_rapide_minutes")
    if seuil is None or (duree.total_seconds() / 60) <= seuil:
        envoyer_notifications_externes(config, annonce, duree_str, raison)


# ========================================================================
# SECTION 9 — AFFICHAGE ET NOTIFICATIONS (console, Discord, Telegram)
# ========================================================================

def formater_duree(duree) -> str:
    """Transforme un timedelta en texte lisible (ex: "2 h 15 min")."""
    total_minutes = int(duree.total_seconds() // 60)
    if total_minutes < 60:
        return f"{total_minutes} min"

    heures = total_minutes // 60
    minutes = total_minutes % 60
    if heures < 24:
        return f"{heures} h {minutes:02d} min"

    jours = heures // 24
    heures_restantes = heures % 24
    return f"{jours} j {heures_restantes} h"


def formater_date_locale(iso_str: str, config: dict) -> str:
    """Convertit une date ISO (stockée en UTC) vers le fuseau horaire configuré,
    pour un affichage plus naturel (ex: "01/09/2026 à 14:32")."""
    if not iso_str:
        return "inconnue"
    fuseau = ZoneInfo(config.get("fuseau_horaire", "Europe/Paris"))
    dt_locale = datetime.fromisoformat(iso_str).astimezone(fuseau)
    return dt_locale.strftime("%d/%m/%Y à %H:%M")


def afficher_alerte_vente(annonce: dict, duree_str: str, raison: str, config: dict) -> None:
    """Affiche une alerte bien visible dans le terminal quand une annonce
    a disparu de la zone surveillée."""
    prix_str = f"{annonce['prix']:.2f} €" if annonce.get("prix") is not None else "inconnu"
    premiere_str = formater_date_locale(annonce["premiere_observation"], config)

    print("\n" + "═" * 62)
    print("🔥  VENTE / DISPARITION DÉTECTÉE")
    print("═" * 62)
    print(f"  Titre      : {annonce['titre']}")
    print(f"  Prix       : {prix_str}")
    print(f"  Lien       : {annonce['url']}")
    print(f"  Statut     : {raison}")
    print(f"  Vue la 1ère fois       : {premiere_str}")
    print(f"  Vitesse de vente estimée : {duree_str}")
    print("═" * 62 + "\n")


def envoyer_notifications_externes(config: dict, annonce: dict, duree_str: str, raison: str) -> None:
    """Envoie les notifications Discord/Telegram si elles sont activées.

    Astuce (facultative) : plutôt que de mettre le webhook/token en clair dans
    config.yaml, vous pouvez définir une variable d'environnement
    (VINTED_DISCORD_WEBHOOK, VINTED_TELEGRAM_TOKEN, VINTED_TELEGRAM_CHAT_ID) :
    elle sera utilisée en priorité si elle existe. C'est plus sûr si vous
    partagez ou publiez ce dossier quelque part."""
    cfg_notif = config.get("notifications", {})

    cfg_discord = cfg_notif.get("discord", {}) or {}
    if cfg_discord.get("active"):
        webhook = os.environ.get("VINTED_DISCORD_WEBHOOK", "").strip() or cfg_discord.get("webhook_url", "")
        if webhook:
            envoyer_discord(webhook, annonce, duree_str, raison)
        else:
            logging.warning("Notification Discord activée mais aucun 'webhook_url' renseigné.")

    cfg_telegram = cfg_notif.get("telegram", {}) or {}
    if cfg_telegram.get("active"):
        token = os.environ.get("VINTED_TELEGRAM_TOKEN", "").strip() or cfg_telegram.get("bot_token", "")
        chat_id = os.environ.get("VINTED_TELEGRAM_CHAT_ID", "").strip() or cfg_telegram.get("chat_id", "")
        if token and chat_id:
            envoyer_telegram(token, chat_id, annonce, duree_str, raison)
        else:
            logging.warning("Notification Telegram activée mais 'bot_token'/'chat_id' incomplets.")


def envoyer_discord(webhook_url: str, annonce: dict, duree_str: str, raison: str) -> None:
    prix_str = f"{annonce['prix']:.2f} €" if annonce.get("prix") is not None else "inconnu"
    contenu = (
        f"🔥 **Annonce disparue ({raison})**\n"
        f"**{annonce['titre']}**\n"
        f"💶 Prix : {prix_str}\n"
        f"⏱️ Vitesse estimée : {duree_str}\n"
        f"🔗 {annonce['url']}"
    )
    try:
        reponse = requests.post(webhook_url, json={"content": contenu}, timeout=10)
        reponse.raise_for_status()
        logging.info("   ↳ Notification Discord envoyée.")
    except Exception as erreur:
        logging.warning(f"   ↳ Échec de l'envoi Discord : {erreur}")


def envoyer_telegram(token: str, chat_id: str, annonce: dict, duree_str: str, raison: str) -> None:
    prix_str = f"{annonce['prix']:.2f} €" if annonce.get("prix") is not None else "inconnu"
    texte = (
        f"🔥 Annonce disparue ({raison})\n"
        f"{annonce['titre']}\n"
        f"Prix : {prix_str}\n"
        f"Vitesse estimée : {duree_str}\n"
        f"{annonce['url']}"
    )
    url_api = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        reponse = requests.post(url_api, data={"chat_id": chat_id, "text": texte}, timeout=10)
        reponse.raise_for_status()
        logging.info("   ↳ Notification Telegram envoyée.")
    except Exception as erreur:
        logging.warning(f"   ↳ Échec de l'envoi Telegram : {erreur}")


# ========================================================================
# SECTION 10 — PAUSES (manuelle et nocturne)
# ========================================================================

def fichier_pause_present(config: dict) -> bool:
    return os.path.exists(config.get("fichier_pause", "PAUSE"))


def attendre_fin_de_pause(config: dict) -> None:
    chemin = config.get("fichier_pause", "PAUSE")
    logging.info(f"⏸️  Pause manuelle activée (fichier '{chemin}' détecté). Supprimez ce fichier pour reprendre.")
    while fichier_pause_present(config):
        time.sleep(5)
    logging.info("▶️  Reprise de la surveillance (fichier de pause supprimé).")


def est_periode_nuit(config: dict) -> bool:
    cfg = config.get("pause_nocturne", {})
    if not cfg.get("active", True):
        return False

    fuseau = ZoneInfo(config.get("fuseau_horaire", "Europe/Paris"))
    maintenant = datetime.now(fuseau)

    heure_debut = cfg.get("heure_debut", 0)
    heure_fin = cfg.get("heure_fin", 7)

    if heure_debut <= heure_fin:
        return heure_debut <= maintenant.hour < heure_fin
    # Cas d'une plage traversant minuit dans l'autre sens (ex: 22h -> 6h)
    return maintenant.hour >= heure_debut or maintenant.hour < heure_fin


def attendre_fin_nuit(config: dict) -> None:
    cfg = config.get("pause_nocturne", {})
    logging.info(
        f"🌙 Pause nocturne (entre {cfg.get('heure_debut', 0)}h et {cfg.get('heure_fin', 7)}h). "
        f"Reprise automatique après {cfg.get('heure_fin', 7)}h."
    )
    while est_periode_nuit(config):
        time.sleep(300)  # revérifie toutes les 5 minutes, pas besoin de plus précis
    logging.info("☀️  Fin de la pause nocturne, reprise de la surveillance.")


def attendre(duree_secondes: float, config: dict) -> None:
    """Attend `duree_secondes`, en vérifiant régulièrement si une pause
    manuelle est demandée entre-temps (au lieu d'un seul long sommeil)."""
    fin = time.time() + duree_secondes
    while time.time() < fin:
        if fichier_pause_present(config):
            attendre_fin_de_pause(config)
            return
        time.sleep(min(30, max(1, fin - time.time())))


# ========================================================================
# SECTION 11 — UN CYCLE DE SURVEILLANCE
# ========================================================================

def traiter_recherche(page, recherche: dict, prix_min: int, config: dict, bd: sqlite3.Connection) -> None:
    """Parcourt toutes les pages configurées pour une recherche donnée."""
    nom = recherche["nom"]
    page_debut = recherche["page_debut"]
    page_fin = recherche["page_fin"]

    logging.info(f"📄 Recherche « {nom} » — pages {page_debut} à {page_fin} — prix ≥ {prix_min} €")

    ids_vus_ce_cycle: set[str] = set()
    maintenant = datetime.now(timezone.utc).isoformat()

    for numero_page in range(page_debut, page_fin + 1):
        url = construire_url_recherche(recherche["url"], prix_min, numero_page)
        logging.debug(f"   Ouverture : {url}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except ErreurDelaiPlaywright:
            logging.warning(f"   ⏱️ Temps dépassé pour charger la page {numero_page}, on continue.")
            continue
        except Exception as erreur:
            logging.warning(f"   ⚠️ Erreur au chargement de la page {numero_page} : {erreur}")
            continue

        pause_aleatoire(1.5, 4.0)

        if page_bloquee_ou_captcha(page):
            raise BlocageDetecte()

        gerer_bandeau_cookies(page)
        defilement_naturel(page)

        annonces = extraire_annonces_de_la_page(page, recherche["url"])
        annonces = filtrer_par_mots_cles(annonces, recherche)

        logging.info(f"   Page {numero_page} : {len(annonces)} annonce(s) retenue(s)")

        for annonce in annonces:
            ids_vus_ce_cycle.add(annonce["id"])
            enregistrer_observation(bd, nom, annonce, maintenant)

        pause_aleatoire(2.0, 6.0)  # pause "lecture" avant de passer à la page suivante

    traiter_annonces_disparues(page, nom, ids_vus_ce_cycle, config, bd)


def executer_cycle(config: dict, bd: sqlite3.Connection, chemin_storage_state: str) -> bool:
    """Exécute un cycle complet : ouvre un navigateur, parcourt toutes les
    recherches configurées, puis referme le navigateur. Renvoie True si un
    blocage a été détecté (pour que la boucle principale attende plus longtemps)."""
    prix_min = random.choice(config["prix_minimum_possibles"])
    logging.info(f"🔎 Nouveau cycle — prix minimum choisi pour cette passe : {prix_min} €")

    bloque = False

    with sync_playwright() as p:
        navigateur = lancer_navigateur(p, config)
        contexte = creer_contexte(navigateur, config, chemin_storage_state)
        page = contexte.new_page()

        try:
            for recherche in config["recherches"]:
                try:
                    traiter_recherche(page, recherche, prix_min, config, bd)
                except BlocageDetecte:
                    logging.warning(
                        "🚫 Vinted semble afficher une page de blocage/CAPTCHA. "
                        "Arrêt du cycle en cours par précaution (voir le README)."
                    )
                    bloque = True
                    break
                pause_aleatoire(3, 8)  # pause entre deux recherches différentes
        finally:
            try:
                contexte.storage_state(path=chemin_storage_state)
            except Exception as erreur:
                logging.debug(f"Impossible de sauvegarder la session : {erreur}")
            contexte.close()
            navigateur.close()

    return bloque


# ========================================================================
# SECTION 12 — PROGRAMME PRINCIPAL
# ========================================================================

def analyser_arguments() -> argparse.Namespace:
    parseur = argparse.ArgumentParser(
        description="Moniteur Vinted — surveille des recherches et détecte les annonces vendues rapidement."
    )
    parseur.add_argument(
        "--config",
        default="config.yaml",
        help="Chemin vers le fichier de configuration (par défaut : config.yaml).",
    )
    parseur.add_argument(
        "--une-fois",
        action="store_true",
        help="Exécute un seul cycle puis s'arrête (pratique pour tester la configuration).",
    )
    return parseur.parse_args()


def main() -> None:
    arguments = analyser_arguments()
    config = charger_configuration(arguments.config)
    configurer_logs(config)

    cfg_stockage = config.get("stockage", {})
    chemin_bd = cfg_stockage.get("base_donnees", "vinted_monitor.db")
    chemin_storage_state = cfg_stockage.get("session_navigateur", "storage_state.json")

    bd = initialiser_base_de_donnees(chemin_bd)

    logging.info("=" * 60)
    logging.info("🚀 Démarrage du moniteur Vinted")
    logging.info(f"   Recherches configurées  : {len(config['recherches'])}")
    logging.info(f"   Prix minimum possibles   : {config['prix_minimum_possibles']}")
    logging.info(
        f"   Astuce : créez un fichier nommé '{config.get('fichier_pause', 'PAUSE')}' pour "
        "mettre en pause, Ctrl+C pour arrêter proprement."
    )
    logging.info("=" * 60)

    try:
        while True:
            if fichier_pause_present(config):
                attendre_fin_de_pause(config)
                continue

            if est_periode_nuit(config):
                attendre_fin_nuit(config)
                continue

            bloque = False
            try:
                bloque = executer_cycle(config, bd, chemin_storage_state)
            except Exception as erreur:
                logging.exception(f"Erreur inattendue pendant le cycle : {erreur}")

            if arguments.une_fois:
                logging.info("Mode --une-fois activé : arrêt après ce cycle.")
                break

            if bloque:
                minutes_pause = config.get("pause_si_blocage_minutes", 60)
                logging.info(f"⏸️ Pause de précaution de {minutes_pause} minutes suite à un blocage détecté.")
                attendre(minutes_pause * 60, config)
            else:
                minutes_min = config["intervalle_minutes"]["min"]
                minutes_max = config["intervalle_minutes"]["max"]
                attente_secondes = random.uniform(minutes_min * 60, minutes_max * 60)
                logging.info(f"⏳ Prochain cycle dans environ {attente_secondes / 60:.1f} minutes.")
                attendre(attente_secondes, config)

    except KeyboardInterrupt:
        print()
        logging.info("Arrêt demandé par l'utilisateur (Ctrl+C). Fermeture propre en cours...")
    finally:
        bd.close()
        logging.info("✅ Moniteur Vinted arrêté proprement. À bientôt !")


if __name__ == "__main__":
    main()
