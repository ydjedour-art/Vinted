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
import json
import logging
import os
import random
import re
import sqlite3
import sys
import time
from collections import Counter
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

    # 'prix_minimum' est optionnel : laissez-le vide (ou omettez-le) dans
    # config.yaml pour ne filtrer par aucun prix minimum.

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
#   - 'active'        : pas encore résolue — soit vue lors du dernier scan,
#                        soit toujours en attente de résolution (elle a pu
#                        glisser au-delà de la zone surveillée à cause de
#                        nouvelles annonces, sans que ce soit une vente : on
#                        continue alors à la vérifier directement de temps en
#                        temps, jusqu'à connaître son sort réel, plutôt que
#                        de l'abandonner — sinon la mesure "en combien de
#                        temps une annonce part" serait biaisée)
#   - 'vendu'          : disparue et confirmée comme vendue
#   - 'indisponible'   : disparue et confirmée comme retirée/supprimée
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
            image_url TEXT,
            premiere_observation TEXT NOT NULL,
            publiee_le TEXT,
            publication_bornee INTEGER NOT NULL DEFAULT 0,
            derniere_observation TEXT NOT NULL,
            statut TEXT NOT NULL DEFAULT 'active',
            certitude TEXT,
            disparition_detectee TEXT,
            derniere_verification TEXT,
            PRIMARY KEY (item_id, recherche)
        )
        """
    )
    # Migrations en douceur : si la base a été créée par une version antérieure
    # du script, on lui ajoute les colonnes manquantes sans rien perdre de
    # l'historique déjà accumulé. Une colonne déjà présente lève simplement une
    # OperationalError qu'on ignore.
    for definition_colonne in (
        "derniere_verification TEXT",
        "publiee_le TEXT",
        "publication_bornee INTEGER NOT NULL DEFAULT 0",
        "certitude TEXT",
        "image_url TEXT",
    ):
        try:
            connexion.execute(f"ALTER TABLE annonces ADD COLUMN {definition_colonne}")
        except sqlite3.OperationalError:
            pass  # la colonne existe déjà (base neuve, ou déjà migrée)

    # Index sur les deux chemins d'accès les plus sollicités (la file de
    # vérification et les comptages par recherche) : sans eux, chaque cycle
    # relit la table entière, ce qui devient sensible à partir de quelques
    # milliers d'annonces suivies.
    connexion.execute("CREATE INDEX IF NOT EXISTS idx_recherche_statut ON annonces (recherche, statut)")
    connexion.execute(
        "CREATE INDEX IF NOT EXISTS idx_recherche_verif ON annonces (recherche, derniere_verification)"
    )
    # Petite table générique "clé -> valeur", utilisée notamment pour retenir
    # où en est le mode rotation (quelles recherches restent à traiter dans
    # le tour en cours), afin que ça survive à un redémarrage du script.
    connexion.execute(
        """
        CREATE TABLE IF NOT EXISTS etat (
            cle TEXT PRIMARY KEY,
            valeur TEXT
        )
        """
    )
    connexion.commit()
    return connexion


def lire_etat(bd: sqlite3.Connection, cle: str) -> str | None:
    curseur = bd.execute("SELECT valeur FROM etat WHERE cle = ?", (cle,))
    ligne = curseur.fetchone()
    return ligne[0] if ligne else None


def sauvegarder_etat(bd: sqlite3.Connection, cle: str, valeur: str) -> None:
    bd.execute(
        """
        INSERT INTO etat (cle, valeur) VALUES (?, ?)
        ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur
        """,
        (cle, valeur),
    )
    bd.commit()


def obtenir_tous_les_ids_connus(bd: sqlite3.Connection, recherche: str) -> set[str]:
    """Renvoie l'ensemble des item_id déjà connus pour cette recherche, tous
    statuts confondus (active/vendu/indisponible) — sert uniquement à compter
    les "nouvelles" annonces d'un cycle dans le résumé des logs."""
    curseur = bd.execute("SELECT item_id FROM annonces WHERE recherche = ?", (recherche,))
    return {ligne[0] for ligne in curseur.fetchall()}


def enregistrer_observation(
    bd: sqlite3.Connection,
    recherche: str,
    annonce: dict,
    maintenant: str,
    publiee_le: str | None = None,
    publication_bornee: bool = False,
) -> None:
    """Ajoute une annonce vue pour la première fois, ou met à jour sa dernière
    observation si elle était déjà connue (upsert).

    publiee_le / publication_bornee : date de publication déduite du filigrane
    (voir annonces_publiees_depuis_le_filigrane). Ils ne sont écrits qu'à
    l'insertion initiale : une fois qu'on a daté une annonce, un passage
    ultérieur ne doit pas écraser cette information par une valeur moins
    précise."""
    bd.execute(
        """
        INSERT INTO annonces (
            item_id, recherche, titre, prix, url, image_url,
            premiere_observation, publiee_le, publication_bornee,
            derniere_observation, statut
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        ON CONFLICT(item_id, recherche) DO UPDATE SET
            derniere_observation = excluded.derniere_observation,
            titre = excluded.titre,
            prix = excluded.prix,
            -- L'extraction de la photo peut rater ponctuellement (page pas
            -- entièrement chargée, etc.) : on garde alors la dernière connue
            -- plutôt que de l'effacer.
            image_url = COALESCE(excluded.image_url, image_url),
            statut = 'active'
        """,
        (
            annonce["id"],
            recherche,
            annonce["titre"],
            annonce["prix"],
            annonce["url"],
            annonce.get("image_url"),
            maintenant,
            publiee_le,
            1 if publication_bornee else 0,
            maintenant,
        ),
    )
    bd.commit()


def obtenir_annonces_a_surveiller(bd: sqlite3.Connection, recherche: str) -> list[dict]:
    """Renvoie toutes les annonces pas encore résolues (statut 'active') pour
    cette recherche. On remonte aussi les colonnes de date nécessaires au
    calcul de priorité.

    L'ordre définitif de la file de vérification est décidé par
    prioriser_file_de_verification(). L'ORDER BY conservé ici sert de base
    stable (jamais vérifiée d'abord, puis la plus anciennement vérifiée) :
    comme le tri Python est stable, il départage de façon déterministe les
    annonces de même priorité, au lieu de dépendre de l'ordre interne de
    SQLite."""
    curseur = bd.execute(
        """
        SELECT item_id, titre, prix, url, image_url,
               premiere_observation, publiee_le, publication_bornee,
               derniere_verification
        FROM annonces
        WHERE recherche = ? AND statut = 'active'
        ORDER BY derniere_verification IS NOT NULL, derniere_verification ASC
        """,
        (recherche,),
    )
    colonnes = [description[0] for description in curseur.description]
    return [dict(zip(colonnes, ligne)) for ligne in curseur.fetchall()]


def age_annonce(annonce: dict, maintenant_dt: datetime) -> float:
    """Âge de l'annonce en minutes, mesuré depuis sa publication quand celle-ci
    a pu être bornée par le filigrane, sinon depuis notre première observation
    (moins précis, mais c'est la meilleure information disponible)."""
    reference = annonce.get("publiee_le") or annonce["premiere_observation"]
    try:
        return (maintenant_dt - datetime.fromisoformat(reference)).total_seconds() / 60
    except (ValueError, TypeError):
        return 0.0


def prioriser_file_de_verification(annonces: list[dict], config: dict, maintenant_dt: datetime) -> list[dict]:
    """Trie la file de vérification par UTILITÉ, pas par ancienneté.

    Le budget de vérification par cycle est petit (quelques dizaines de pages
    au maximum) alors que le nombre d'annonces suivies peut atteindre plusieurs
    milliers. Le tri « la plus anciennement vérifiée d'abord » traite toutes
    les annonces à égalité, ce qui gaspille ce budget : une annonce publiée il
    y a trois jours et toujours en ligne ne produira jamais une « vente
    rapide », la vérifier n'apprend presque rien sur l'objectif poursuivi.

    On classe donc en trois groupes, du plus utile au moins utile :
      1. les annonces dont l'âge est DANS la fenêtre de vente rapide — ce sont
         les seules qui peuvent encore produire le signal recherché ;
      2. celles qui viennent juste de dépasser la fenêtre (jusqu'à 2x le
         maximum) — les résoudre ferme proprement leur suivi et alimente les
         statistiques ;
      3. tout le reste, du plus ancien au plus récent.
    À l'intérieur de chaque groupe, les annonces jamais vérifiées passent en
    premier, puis les moins récemment vérifiées : aucune annonce n'est donc
    jamais abandonnée, elle est seulement servie plus tard."""
    seuil = config.get("notifications", {}).get("seuil_vente_rapide") or {}
    minimum = seuil.get("min_minutes") or 0
    maximum = seuil.get("max_minutes") or 120

    def cle_de_tri(annonce: dict) -> tuple:
        age = age_annonce(annonce, maintenant_dt)

        if minimum <= age <= maximum:
            groupe = 0
        elif age < minimum:
            # Trop jeune pour être une vente rapide : on la laisse vivre encore
            # un peu plutôt que de dépenser une vérification tout de suite.
            groupe = 2
        elif age <= maximum * 2:
            groupe = 1
        else:
            groupe = 3

        jamais_verifiee = annonce.get("derniere_verification") is None
        return (groupe, not jamais_verifiee, annonce.get("derniere_verification") or "")

    return sorted(annonces, key=cle_de_tri)


# ------------------------------------------------------------------------
# Filigrane : dater les publications sans dépendre du HTML de Vinted
# ------------------------------------------------------------------------
# Les pages de résultats sont toujours chargées avec order=newest_first, donc
# la page 1 est le flux des annonces de la plus récente à la plus ancienne.
# En mémorisant l'identifiant de l'annonce la plus récente vue au cycle
# précédent (le « filigrane »), toute annonce située AVANT lui dans le flux du
# cycle suivant a nécessairement été publiée entre les deux passages : sa date
# de publication est donc bornée à la durée d'un cycle (quelques minutes), au
# lieu d'être totalement inconnue.
#
# L'intérêt est double : c'est bien plus précis que « l'instant où je l'ai
# remarquée » (une annonce découverte en page 2 peut déjà avoir une heure), et
# surtout cela ne repose sur AUCUN sélecteur CSS ni aucune supposition sur le
# HTML de Vinted — seulement sur un ordre de tri que nous imposons nous-mêmes.
# Si le filigrane est introuvable (trop d'annonces publiées entre deux
# passages), on ne devine pas : les annonces sont marquées « publication
# inconnue » et exclues des mesures qui exigent une date fiable.
# ------------------------------------------------------------------------

def lire_filigrane(bd: sqlite3.Connection, recherche: str) -> tuple[str | None, str | None]:
    """Renvoie (item_id du filigrane, horodatage de sa pose) pour cette
    recherche, ou (None, None) si aucun filigrane n'a encore été posé."""
    valeur = lire_etat(bd, f"filigrane:{recherche}")
    if not valeur or "|" not in valeur:
        return (None, None)
    item_id, horodatage = valeur.split("|", 1)
    return (item_id or None, horodatage or None)


def sauvegarder_filigrane(bd: sqlite3.Connection, recherche: str, item_id: str, horodatage: str) -> None:
    sauvegarder_etat(bd, f"filigrane:{recherche}", f"{item_id}|{horodatage}")


def annonces_publiees_depuis_le_filigrane(
    annonces_page_1: list[dict], filigrane_id: str | None
) -> set[str]:
    """Renvoie les identifiants des annonces publiées depuis le dernier
    passage, d'après leur position dans le flux trié du plus récent au plus
    ancien : ce sont celles qui apparaissent AVANT le filigrane.

    Si le filigrane est absent de la page (premier cycle, ou trop de nouvelles
    annonces depuis le dernier passage pour qu'il soit encore visible), on
    renvoie un ensemble vide : mieux vaut ne rien dater que dater à tort."""
    if not filigrane_id:
        return set()

    identifiants = [annonce["id"] for annonce in annonces_page_1]
    if filigrane_id not in identifiants:
        return set()

    return set(identifiants[: identifiants.index(filigrane_id)])


# Mots trop courants dans les titres Vinted pour être un signal de tendance
# utile (état, taille, mots de liaison...). Liste volontairement simple :
# à compléter si vous remarquez d'autres mots parasites récurrents.
MOTS_IGNORES_TENDANCES = {
    "état", "etat", "taille", "neuf", "bon", "très", "tres", "avec", "sans",
    "pour", "dans", "les", "des", "une", "un", "de", "du", "et", "sur",
    "vintage", "cuir", "coton", "occasion", "comme", "petit", "petite",
    "grand", "grande", "taches", "tache", "porté", "porte", "fois",
}


def mots_du_titre(titre: str | None) -> set[str]:
    """Mots retenus pour l'analyse de tendance dans un titre d'annonce.
    On travaille en ENSEMBLE (pas en liste) : un mot répété deux fois dans le
    même titre ne doit peser que pour une annonce."""
    if not titre:
        return set()
    return {
        mot
        for mot in re.findall(r"[a-zàâäéèêëïîôöùûüç]{4,}", titre.lower())
        if mot not in MOTS_IGNORES_TENDANCES
    }


def calculer_tendances(
    bd: sqlite3.Connection,
    recherche: str | None = None,
    limite: int = 10,
    minimum_ventes: int = 3,
) -> list[dict]:
    """Calcule les tendances par « lift » plutôt que par simple comptage.

    Le comptage brut des mots des annonces vendues répond en réalité à la
    question « quelles marques sont fréquentes dans cette catégorie ? », pas
    « lesquelles partent vite ? ». Si Nike représente 40 % du catalogue, Nike
    sortira en tête des tendances même si Nike se vend PLUS LENTEMENT que la
    moyenne : on redécouvre la composition du catalogue, pas une tendance.

    On compare donc, pour chaque mot, sa fréquence chez les annonces vendues
    à sa fréquence chez TOUTES les annonces suivies :

        lift = (part du mot chez les vendues) / (part du mot chez toutes)

    Un lift de 2 signifie « ce mot apparaît deux fois plus souvent chez les
    annonces qui partent que dans le catalogue en général » — ça, c'est une
    vraie tendance. Un lift proche de 1 est du bruit, même pour un mot très
    fréquent. Les mots vus dans moins de `minimum_ventes` ventes sont écartés
    (sur 2 ou 3 ventes, un lift élevé n'est que du hasard).

    Seules les disparitions de certitude 'confirmee' sont comptées : les lots
    douteux (voir marquer_lot_douteux) sont exclus pour ne pas laisser le
    ménage d'un vendeur polluer les tendances.

    Renvoie une liste de dictionnaires {mot, ventes, lift}, du lift le plus
    fort au plus faible."""
    if recherche:
        toutes = bd.execute("SELECT titre, statut, certitude FROM annonces WHERE recherche = ?", (recherche,))
    else:
        toutes = bd.execute("SELECT titre, statut, certitude FROM annonces")

    compteur_global: Counter = Counter()
    compteur_ventes: Counter = Counter()
    total_annonces = 0
    total_ventes = 0

    for titre, statut, certitude in toutes.fetchall():
        mots = mots_du_titre(titre)
        if not mots:
            continue

        total_annonces += 1
        for mot in mots:
            compteur_global[mot] += 1

        # Une disparition sans certitude renseignée provient d'une version
        # antérieure du script : on la traite comme confirmée pour ne pas
        # perdre l'historique déjà accumulé.
        if statut in ("vendu", "indisponible") and (certitude or "confirmee") == "confirmee":
            total_ventes += 1
            for mot in mots:
                compteur_ventes[mot] += 1

    if not total_ventes or not total_annonces:
        return []

    tendances = []
    for mot, ventes in compteur_ventes.items():
        if ventes < minimum_ventes:
            continue
        part_ventes = ventes / total_ventes
        part_globale = compteur_global[mot] / total_annonces
        if not part_globale:
            continue
        tendances.append({"mot": mot, "ventes": ventes, "lift": part_ventes / part_globale})

    tendances.sort(key=lambda t: t["lift"], reverse=True)
    return tendances[:limite]


def marquer_statut(
    bd: sqlite3.Connection,
    recherche: str,
    item_id: str,
    statut: str,
    maintenant: str,
    certitude: str = "confirmee",
) -> None:
    bd.execute(
        """
        UPDATE annonces SET statut = ?, disparition_detectee = ?, certitude = ?
        WHERE item_id = ? AND recherche = ?
        """,
        (statut, maintenant, certitude, item_id, recherche),
    )
    bd.commit()


def marquer_lot_douteux(bd: sqlite3.Connection, recherche: str, identifiants: list[str]) -> None:
    """Dégrade la certitude d'un lot de disparitions détectées dans un même
    cycle.

    Une disparition confirmée ne prouve pas une vente : elle prouve seulement
    que l'annonce n'est plus en ligne. Un vendeur qui fait le ménage et retire
    quarante annonces d'un coup produit exactement la même signature qu'une
    vague de ventes-éclair — et polluerait durablement les tendances avec ses
    marques. Une rafale anormale dans un seul cycle est donc marquée
    'douteuse' : l'information est conservée et reste visible, mais elle est
    exclue du calcul des tendances."""
    if not identifiants:
        return
    marqueurs = ",".join("?" for _ in identifiants)
    bd.execute(
        f"UPDATE annonces SET certitude = 'douteuse' WHERE recherche = ? AND item_id IN ({marqueurs})",
        (recherche, *identifiants),
    )
    bd.commit()


def marquer_verifie_toujours_actif(bd: sqlite3.Connection, recherche: str, item_id: str, maintenant: str) -> None:
    """L'annonce vient d'être vérifiée directement et est toujours en ligne
    (elle a juste glissé hors de la zone de pages surveillée à cause de
    nouvelles annonces) : ce n'est PAS une vente. On ne l'abandonne pas pour
    autant — elle reste 'active' et sera re-proposée plus tard dans la file
    de vérification (les moins récemment vérifiées passent en premier), afin
    de finir par connaître son sort réel."""
    bd.execute(
        "UPDATE annonces SET derniere_verification = ? WHERE item_id = ? AND recherche = ?",
        (maintenant, item_id, recherche),
    )
    bd.commit()


def choisir_recherches_du_cycle(config: dict, bd: sqlite3.Connection) -> list[dict]:
    """Détermine quelle(s) recherche(s) traiter lors de ce cycle.

    - Mode normal (rotation: false, par défaut) : TOUTES les recherches
      configurées sont traitées à chaque cycle. Adapté à une poignée de
      recherches qu'on veut surveiller en continu.

    - Mode rotation (rotation: true) : UNE SEULE recherche est traitée par
      cycle, choisie tour à tour parmi toutes celles configurées. L'ordre est
      remélangé à chaque tour complet (pas un simple 1,2,3,1,2,3...), pour
      couvrir un grand nombre de catégories dans le temps sans que chaque
      cycle devienne interminable. La position dans la rotation est
      sauvegardée en base : un redémarrage du script ne repart pas de zéro.
    """
    toutes_les_recherches = config["recherches"]

    if not config.get("rotation", False):
        return toutes_les_recherches

    noms_disponibles = [r["nom"] for r in toutes_les_recherches]

    file_json = lire_etat(bd, "file_rotation")
    file_actuelle = json.loads(file_json) if file_json else []

    # Ne garde que les noms encore présents dans la config actuelle (au cas
    # où des recherches ont été ajoutées/retirées depuis le dernier cycle).
    file_actuelle = [nom for nom in file_actuelle if nom in noms_disponibles]

    if not file_actuelle:
        # File vide (premier lancement, ou tour complet terminé) : on repart
        # pour un nouveau tour, avec un ordre remélangé.
        file_actuelle = noms_disponibles.copy()
        random.shuffle(file_actuelle)

    nom_choisi = file_actuelle.pop(0)
    sauvegarder_etat(bd, "file_rotation", json.dumps(file_actuelle, ensure_ascii=False))

    recherche_choisie = next(r for r in toutes_les_recherches if r["nom"] == nom_choisi)
    return [recherche_choisie]


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
            pause_aleatoire(0.2, 0.6)
    except Exception as erreur:
        logging.debug(f"Défilement interrompu sans conséquence : {erreur}")


# ========================================================================
# SECTION 5 — NAVIGATEUR (lancement Playwright et gestion des cookies)
# ========================================================================

def lancer_navigateur(playwright, config: dict):
    cfg_nav = config.get("navigateur", {})
    parametres = {
        "headless": cfg_nav.get("headless", True),
        # Désactive l'accélération graphique : sur certaines machines (pilote
        # graphique absent/instable), Chromium en mode headless peut planter
        # en cours de route à cause d'erreurs GPU (visible dans les logs sous
        # la forme "No available adapters" / erreurs ANGLE). On n'a de toute
        # façon besoin d'aucun rendu graphique pour lire une page web.
        "args": ["--disable-gpu"],
    }

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

def construire_url_recherche(url_base: str, prix_min: int | None, numero_page: int) -> str:
    """Reconstruit l'URL de recherche Vinted en forçant :
       - le tri par "Plus récent"  -> order=newest_first
       - le prix minimum du cycle -> price_from (si prix_min n'est pas None)
       - le numéro de page voulu  -> page
    Tous les autres filtres déjà présents dans l'URL fournie par l'utilisateur
    (catégorie, marque, taille, texte recherché, etc.) sont conservés tels quels.
    """
    morceaux = urlparse(url_base)
    parametres = parse_qs(morceaux.query)

    parametres["order"] = ["newest_first"]
    parametres["page"] = [str(numero_page)]

    if prix_min is None:
        # Pas de prix minimum configuré : on retire aussi celui qui traînerait
        # dans l'URL d'origine, pour ne rien filtrer du tout.
        parametres.pop("price_from", None)
    else:
        parametres["price_from"] = [str(prix_min)]

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

def extraire_url_photo(lien) -> str | None:
    """Essaie de récupérer l'URL de la photo miniature d'une annonce, à partir
    de l'élément <img> trouvé dans son lien. Reste volontairement tolérant :
    plusieurs attributs sont essayés (les images sont souvent chargées en
    différé via 'data-src' plutôt que 'src'), et l'absence de photo n'est
    jamais une erreur — juste une alerte sans image plus tard."""
    try:
        image = lien.locator("img").first
        for attribut in ("src", "data-src", "srcset"):
            valeur = image.get_attribute(attribut)
            if valeur:
                # srcset peut contenir plusieurs tailles ("url1 1x, url2 2x") :
                # on garde la première URL.
                return valeur.split(",")[0].strip().split(" ")[0]
    except Exception:
        pass
    return None


def extraire_annonces_de_la_page(page, url_de_base: str) -> list[dict]:
    """Extrait les annonces visibles sur la page de résultats actuellement
    affichée. Retourne une liste de dictionnaires {id, titre, prix, url,
    image_url}. image_url est une estimation "au mieux" (voir
    extraire_url_photo) : None si elle n'a pas pu être trouvée, ce qui n'empêche
    jamais de suivre normalement l'annonce."""
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
            image_url = extraire_url_photo(lien)

            annonces.append(
                {
                    "id": item_id,
                    "titre": titre,
                    "prix": prix,
                    "url": url_absolue(href, url_de_base),
                    "image_url": url_absolue(image_url, url_de_base) if image_url else None,
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
    """Visite la page d'une annonce précise pour déterminer si elle a
    réellement disparu de Vinted (page supprimée/introuvable) ou si elle est
    encore en ligne.

    Cette vérification directe évite les faux positifs liés à la pagination :
    une annonce qui a simplement glissé au-delà de la dernière page
    surveillée (parce que de nouvelles annonces sont arrivées devant elle)
    n'est PAS une disparition réelle.

    Important — limite connue et assumée : ce script NE TENTE PAS de
    distinguer "vendue" de "retirée par le vendeur" en cherchant un mot
    comme "vendu" dans la page. Une première version le faisait, et ça s'est
    révélé peu fiable en pratique (le mot apparaît ailleurs sur la page —
    vraisemblablement les statistiques du profil du vendeur, du type
    « X articles vendus » — même quand CETTE annonce précise est encore en
    vente), ce qui provoquait de fausses alertes. Mieux vaut ne pas conclure
    que se tromper : si l'annonce est encore accessible normalement, elle
    reste simplement suivie (voir marquer_verifie_toujours_actif) plutôt que
    d'être déclarée vendue à tort.

    Renvoie : "active", "indisponible" ou "inconnu".
    """
    try:
        page.goto(url_annonce, wait_until="domcontentloaded", timeout=20000)
    except Exception as erreur:
        logging.debug(f"   ↳ Impossible d'ouvrir l'annonce pour vérification : {erreur}")
        return "inconnu"

    pause_aleatoire(0.6, 1.5)

    if page_bloquee_ou_captcha(page):
        raise BlocageDetecte()

    # --- Signal 1 : le TITRE de l'onglet. Une page supprimée/introuvable a
    # souvent un titre distinctif ("Page introuvable", "404"...), assez fiable
    # car un titre de page change rarement pour une raison anodine.
    try:
        titre_page = (page.title() or "").lower()
    except Exception:
        titre_page = ""

    titres_indisponible = [
        "page introuvable",
        "page non trouvée",
        "404",
        "not found",
        "oups",
    ]
    if any(t in titre_page for t in titres_indisponible):
        return "indisponible"

    # --- Signal 2 : phrases explicites dans le texte réellement VISIBLE à
    # l'écran (pas tout le code source, pour limiter le risque de faux
    # positif — voir la mésaventure du mot "vendu" ci-dessus). Ces
    # formulations sont une estimation raisonnable et peuvent nécessiter un
    # ajustement si Vinted change le texte exact de ses messages d'erreur.
    try:
        texte_visible = page.inner_text("body").lower()
    except Exception:
        return "inconnu"

    indicateurs_indisponible = [
        "cet article n'est plus disponible",
        "cette annonce n'existe plus",
        "n'existe pas ou n'est plus disponible",
        "page introuvable",
        "page non trouvée",
    ]
    if any(indicateur in texte_visible for indicateur in indicateurs_indisponible):
        return "indisponible"

    # --- Signal 3 (corroborant seulement, jamais utilisé seul) : sur une
    # annonce active normale, le bouton "Acheter" et un prix affiché sont
    # quasiment toujours présents dans le texte visible. Leur absence
    # SIMULTANÉE est un indice supplémentaire, mais un seul signal fragile
    # (deviné, non vérifié sur une vraie page Vinted) ne suffit jamais à lui
    # seul à conclure — voir la note plus haut sur le mot "vendu" : mieux
    # vaut exiger la convergence de plusieurs signaux que se fier à un seul.
    correspondance_prix = re.search(r"\d+[.,]\d{2}\s?€", texte_visible)
    a_bouton_achat = "acheter" in texte_visible
    if not a_bouton_achat and not correspondance_prix:
        return "indisponible"

    return "active"


def traiter_annonces_disparues(page, nom_recherche: str, ids_vus_ce_cycle: set, config: dict, bd: sqlite3.Connection) -> tuple[int, int]:
    """Compare les annonces suivies en base à celles vues pendant ce cycle et
    vérifie individuellement celles qui semblent avoir disparu.

    Renvoie (nombre de "suspectes" ce cycle, nombre confirmées disparues ce
    cycle) pour le résumé affiché dans les logs."""
    maintenant_dt = datetime.now(timezone.utc)
    suivies = obtenir_annonces_a_surveiller(bd, nom_recherche)
    absentes = [a for a in suivies if a["item_id"] not in ids_vus_ce_cycle]
    nb_suspectes = len(absentes)

    if not absentes:
        return (0, 0)

    logging.info(f"   🔍 {nb_suspectes} annonce(s) suspecte(s) (absente(s) de la zone surveillée), vérification...")

    cfg_verif = config.get("verification_avant_disparition", {})
    nb_confirmees = 0

    if not cfg_verif.get("active", True):
        # Mode rapide : on ne vérifie pas individuellement, on considère les
        # annonces absentes comme vendues directement (plus rapide, mais plus
        # sensible aux faux positifs liés à la pagination).
        for annonce in absentes:
            confirmer_disparition(bd, nom_recherche, annonce, "vendu (non vérifié)", config)
            nb_confirmees += 1
        return (nb_suspectes, nb_confirmees)

    max_verifs = cfg_verif.get("max_verifications_par_cycle", 8)

    # La file est triée par utilité (fenêtre de vente rapide d'abord), pas par
    # ancienneté : avec un budget de quelques vérifications par cycle face à
    # potentiellement des milliers d'annonces suivies, l'ordre décide de la
    # productivité réelle du système. Aucune annonce n'est abandonnée pour
    # autant : celles qui sortent de la fenêtre restent dans la file et
    # finissent par être servies.
    file_prioritaire = prioriser_file_de_verification(absentes, config, maintenant_dt)

    confirmees_ce_cycle: list[str] = []

    for annonce in file_prioritaire[:max_verifs]:
        pause_aleatoire(1.0, 2.5)
        statut = verifier_statut_annonce(page, annonce["url"])  # BlocageDetecte remonte naturellement

        if statut == "indisponible":
            # On ne peut pas distinguer avec certitude "vendue" de "retirée par
            # le vendeur" (voir verifier_statut_annonce) : le libellé reste
            # volontairement neutre plutôt que d'affirmer une vente à tort.
            confirmer_disparition(bd, nom_recherche, annonce, "disparue (vendue probable, ou retirée par le vendeur)", config)
            confirmees_ce_cycle.append(annonce["item_id"])
            nb_confirmees += 1
        elif statut == "active":
            maintenant = datetime.now(timezone.utc).isoformat()
            marquer_verifie_toujours_actif(bd, nom_recherche, annonce["item_id"], maintenant)
            logging.debug(
                f"   ↳ Annonce {annonce['item_id']} toujours active, hors zone surveillée pour l'instant "
                "(sera re-vérifiée plus tard)."
            )
        # "inconnu" (erreur réseau, etc.) : on ne change rien, nouvelle tentative au prochain cycle

    # Garde anti-rafale : au-delà d'une certaine proportion de disparitions
    # confirmées dans un seul cycle, le motif ressemble davantage à un vendeur
    # qui retire son stock qu'à une vague de ventes simultanées. On conserve
    # l'information mais on la marque douteuse pour qu'elle ne fausse pas les
    # tendances.
    seuil_rafale = cfg_verif.get("seuil_rafale_douteuse", 0.8)
    verifiees = min(len(file_prioritaire), max_verifs)
    if verifiees >= 5 and nb_confirmees >= verifiees * seuil_rafale:
        logging.warning(
            f"   ⚠️  {nb_confirmees} disparitions sur {verifiees} vérifications dans le même cycle : "
            "rafale anormale (retrait en masse par un vendeur ?). Ce lot est marqué douteux et "
            "exclu du calcul des tendances."
        )
        marquer_lot_douteux(bd, nom_recherche, confirmees_ce_cycle)

    return (nb_suspectes, nb_confirmees)


def vente_rapide(duree, config: dict) -> bool:
    """Détermine si une disparition confirmée compte comme une "vente rapide"
    au sens de config.yaml (notifications.seuil_vente_rapide : min_minutes et
    max_minutes) : une fourchette, pas juste un plafond. En dessous du
    minimum, c'est le plus souvent le signe d'un faux positif (annonce
    supprimée puis republiée par exemple) plutôt qu'une vraie vente-éclair ;
    au-dessus du maximum, ce n'est plus vraiment "rapide". Si la section
    n'est pas configurée, tout est considéré comme rapide (comportement
    précédent, notifie toujours)."""
    cfg = config.get("notifications", {}).get("seuil_vente_rapide")
    if not cfg:
        return True

    minutes = duree.total_seconds() / 60
    minimum = cfg.get("min_minutes")
    maximum = cfg.get("max_minutes")

    if minimum is not None and minutes < minimum:
        return False
    if maximum is not None and minutes > maximum:
        return False
    return True


def confirmer_disparition(bd: sqlite3.Connection, nom_recherche: str, annonce: dict, raison: str, config: dict) -> None:
    """Enregistre la disparition confirmée d'une annonce, affiche une alerte et
    envoie les notifications externes si configurées.

    La durée est mesurée depuis la publication quand celle-ci a pu être bornée
    par le filigrane (précision de l'ordre du cycle), sinon depuis notre
    première observation — auquel cas la durée est une borne SUPÉRIEURE (nous
    ne savons pas depuis combien de temps l'annonce était déjà en ligne avant
    qu'on la remarque), ce que l'affichage signale explicitement."""
    maintenant_dt = datetime.now(timezone.utc)
    maintenant = maintenant_dt.isoformat()

    statut = "vendu" if raison.startswith("vendu") else "indisponible"
    marquer_statut(bd, nom_recherche, annonce["item_id"], statut, maintenant)

    publication_bornee = bool(annonce.get("publication_bornee")) and annonce.get("publiee_le")
    reference = annonce["publiee_le"] if publication_bornee else annonce["premiere_observation"]

    duree = maintenant_dt - datetime.fromisoformat(reference)
    duree_str = formater_duree(duree)
    if not publication_bornee:
        # On ne connaît pas la date de publication : la durée mesurée part de
        # notre première observation, donc la vraie durée de vie de l'annonce
        # est forcément plus longue. Le "<" évite de faire passer une
        # estimation pour une mesure.
        duree_str = f"< {duree_str}"

    afficher_alerte_vente(annonce, duree_str, raison, config)

    # Seules les annonces dont la publication est datée de façon fiable
    # peuvent déclencher une alerte "vente rapide" : sans cela, on notifierait
    # des annonces simplement remarquées tard, ce qui est précisément le biais
    # que le filigrane sert à corriger.
    if publication_bornee and vente_rapide(duree, config):
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
    """Envoie l'alerte sous forme d'« embed » Discord : titre cliquable (lien
    direct vers l'annonce), prix et vitesse en champs, et la photo de
    l'annonce en grand si elle a pu être récupérée au moment où l'annonce a
    été vue (image_url) — l'annonce n'étant souvent plus disponible au moment
    de l'alerte, on ne peut pas aller la re-télécharger à cet instant-là."""
    prix_str = f"{annonce['prix']:.2f} €" if annonce.get("prix") is not None else "inconnu"

    embed = {
        "title": annonce["titre"] or "Annonce Vinted",
        "url": annonce["url"],
        "description": f"🔥 Annonce disparue ({raison})",
        "color": 0x00C08B,  # vert Vinted
        "fields": [
            {"name": "💶 Prix", "value": prix_str, "inline": True},
            {"name": "⏱️ Vitesse estimée", "value": duree_str, "inline": True},
        ],
    }
    if annonce.get("image_url"):
        embed["image"] = {"url": annonce["image_url"]}

    try:
        reponse = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
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


def decalage_nocturne(cfg: dict, jour: str) -> tuple[int, int]:
    """Décalage aléatoire (en minutes) appliqué aux bornes de la pause
    nocturne pour un jour donné.

    S'arrêter à 00:00:00 pile et reprendre à 07:00:00 pile chaque jour est en
    soi une signature : aucun humain n'est aussi ponctuel. On décale donc
    chaque borne de quelques dizaines de minutes. Le tirage est dérivé de la
    DATE (et non d'un random appelé à chaque test), pour que les bornes
    restent stables toute la nuit au lieu de changer à chaque vérification —
    sinon le script oscillerait entre "c'est la nuit" et "ce n'est plus la
    nuit" d'un appel à l'autre."""
    amplitude = cfg.get("flou_minutes", 25)
    if not amplitude:
        return (0, 0)
    tirage = random.Random(f"{jour}:{cfg.get('heure_debut', 0)}:{cfg.get('heure_fin', 7)}")
    return (tirage.randint(-amplitude, amplitude), tirage.randint(-amplitude, amplitude))


def est_periode_nuit(config: dict) -> bool:
    cfg = config.get("pause_nocturne", {})
    if not cfg.get("active", True):
        return False

    fuseau = ZoneInfo(config.get("fuseau_horaire", "Europe/Paris"))
    maintenant = datetime.now(fuseau)

    decalage_debut, decalage_fin = decalage_nocturne(cfg, maintenant.strftime("%Y-%m-%d"))
    # On raisonne en minutes depuis minuit pour pouvoir appliquer le décalage.
    minute_actuelle = maintenant.hour * 60 + maintenant.minute
    debut = cfg.get("heure_debut", 0) * 60 + decalage_debut
    fin = cfg.get("heure_fin", 7) * 60 + decalage_fin

    if debut <= fin:
        return debut <= minute_actuelle < fin
    # Cas d'une plage traversant minuit (ex: 22h -> 6h)
    return minute_actuelle >= debut or minute_actuelle < fin


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

def obtenir_prix_minimum(config: dict) -> int | None:
    """Renvoie le prix minimum fixe à appliquer, défini une fois pour toutes
    dans config.yaml ('prix_minimum'). Le même prix minimum s'applique à
    toutes les recherches, à chaque cycle. Renvoie None si non configuré
    (aucun filtre de prix minimum n'est alors appliqué)."""
    return config.get("prix_minimum")


def traiter_recherche(page, recherche: dict, prix_min: int, config: dict, bd: sqlite3.Connection) -> None:
    """Parcourt toutes les pages configurées pour une recherche donnée."""
    nom = recherche["nom"]
    page_debut = recherche["page_debut"]
    page_fin = recherche["page_fin"]

    prix_min_str = f"{prix_min} €" if prix_min is not None else "(aucun filtre)"
    logging.info(f"📄 Recherche « {nom} » — pages {page_debut} à {page_fin} — prix ≥ {prix_min_str}")

    ids_deja_connus = obtenir_tous_les_ids_connus(bd, nom)
    ids_vus_ce_cycle: set[str] = set()
    nb_nouvelles = 0
    maintenant_dt = datetime.now(timezone.utc)
    maintenant = maintenant_dt.isoformat()

    # Filigrane : identifiant de l'annonce la plus récente vue au cycle
    # précédent, et heure de ce passage. Tout ce qui apparaîtra avant lui dans
    # le flux trié "plus récent d'abord" a été publié entre les deux passages.
    filigrane_id, filigrane_horodatage = lire_filigrane(bd, nom)
    ids_publies_depuis: set[str] = set()
    nouveau_filigrane: str | None = None

    # Date attribuée aux annonces publiées depuis le dernier passage : le
    # milieu de l'intervalle [dernier passage, maintenant], ce qui borne
    # l'erreur à une demi-durée de cycle au lieu de la laisser inconnue.
    date_publication_estimee = maintenant
    if filigrane_horodatage:
        try:
            precedent_dt = datetime.fromisoformat(filigrane_horodatage)
            date_publication_estimee = (precedent_dt + (maintenant_dt - precedent_dt) / 2).isoformat()
        except ValueError:
            pass

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

        pause_aleatoire(0.8, 1.8)

        if page_bloquee_ou_captcha(page):
            raise BlocageDetecte()

        gerer_bandeau_cookies(page)
        defilement_naturel(page)

        annonces = extraire_annonces_de_la_page(page, recherche["url"])
        annonces = filtrer_par_mots_cles(annonces, recherche)

        logging.info(f"   Page {numero_page} : {len(annonces)} annonce(s) retenue(s)")

        # La page la plus haute de la plage porte le flux le plus récent :
        # c'est elle qui sert à poser le filigrane et à dater les publications.
        if numero_page == page_debut and annonces:
            ids_publies_depuis = annonces_publiees_depuis_le_filigrane(annonces, filigrane_id)
            nouveau_filigrane = annonces[0]["id"]
            if filigrane_id and not ids_publies_depuis and filigrane_id != nouveau_filigrane:
                logging.debug(
                    "   ↳ Filigrane précédent introuvable dans le flux : trop d'annonces publiées "
                    "depuis le dernier passage pour dater celles-ci de façon fiable."
                )

        for annonce in annonces:
            ids_vus_ce_cycle.add(annonce["id"])
            if annonce["id"] not in ids_deja_connus:
                nb_nouvelles += 1
                ids_deja_connus.add(annonce["id"])  # évite un double comptage si revue plus loin dans ce même cycle

            publiee_depuis_dernier_passage = annonce["id"] in ids_publies_depuis
            enregistrer_observation(
                bd,
                nom,
                annonce,
                maintenant,
                publiee_le=date_publication_estimee if publiee_depuis_dernier_passage else None,
                publication_bornee=publiee_depuis_dernier_passage,
            )

        pause_aleatoire(1.0, 2.5)  # pause "lecture" avant de passer à la page suivante

    if nouveau_filigrane:
        sauvegarder_filigrane(bd, nom, nouveau_filigrane, maintenant)

    nb_suspectes, nb_confirmees = traiter_annonces_disparues(page, nom, ids_vus_ce_cycle, config, bd)

    logging.info(
        f"   📊 Résumé « {nom} » : {nb_nouvelles} nouvelle(s) annonce(s) "
        f"(dont {len(ids_publies_depuis)} datée(s) précisément), "
        f"{nb_suspectes} suspecte(s), {nb_confirmees} confirmée(s) disparue(s)"
    )


def executer_cycle(config: dict, bd: sqlite3.Connection, chemin_storage_state: str) -> bool:
    """Exécute un cycle complet : ouvre un navigateur, parcourt toutes les
    recherches configurées, puis referme le navigateur. Renvoie True si un
    blocage a été détecté (pour que la boucle principale attende plus longtemps)."""
    logging.info("🔎 Nouveau cycle")

    recherches_du_cycle = choisir_recherches_du_cycle(config, bd)
    if config.get("rotation", False):
        logging.info(
            f"🔁 Mode rotation ({len(config['recherches'])} catégorie(s) au total) — "
            f"celle traitée ce cycle : « {recherches_du_cycle[0]['nom']} »"
        )

    bloque = False

    with sync_playwright() as p:
        navigateur = lancer_navigateur(p, config)
        contexte = creer_contexte(navigateur, config, chemin_storage_state)
        page = contexte.new_page()

        try:
            prix_min = obtenir_prix_minimum(config)
            for recherche in recherches_du_cycle:
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
            # Le navigateur a pu planter/se fermer tout seul entre-temps (crash
            # du processus, par exemple) : dans ce cas .close() lève une erreur
            # sans intérêt puisqu'il n'y a de toute façon plus rien à fermer.
            try:
                contexte.close()
            except Exception as erreur:
                logging.debug(f"Fermeture du contexte déjà inutile : {erreur}")
            try:
                navigateur.close()
            except Exception as erreur:
                logging.debug(f"Fermeture du navigateur déjà inutile : {erreur}")

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
    prix_min_demarrage = config.get("prix_minimum")
    logging.info(f"   Prix minimum (fixe)      : {prix_min_demarrage} €" if prix_min_demarrage is not None else "   Prix minimum (fixe)      : aucun")
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
