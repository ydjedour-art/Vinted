#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
========================================================================
 OUTIL DE RÉVISION — corriger les faux positifs détectés avant la mise à
 jour du signal 3 (vérification trop rapide, avant que la page de l'annonce
 n'ait fini de charger son prix/bouton d'achat).
========================================================================

Ce script est un outil PONCTUEL, séparé du moniteur : il ne fait qu'une
chose à la fois, sur votre demande explicite. Il ne se lance jamais tout
seul et ne touche à rien d'automatique.

Étape 1 — Regarder les disparitions confirmées récemment :

    python revoir_disparitions.py

Ça affiche un tableau avec, pour chacune, son identifiant, sa durée, son
titre et son LIEN. Cliquez sur quelques liens (en particulier ceux dont la
durée est très courte et proche les unes des autres) pour vérifier
vous-même, dans un navigateur, si l'annonce est encore en vente.

Étape 2 — Si vous confirmez qu'une annonce est encore en vente (donc que
sa disparition était un faux positif), remettez-la en suivi normal avec
son identifiant (visible dans la première colonne du tableau) :

    python revoir_disparitions.py --remettre-actif 123456789 987654321

Elle repart en statut "active" et sera re-vérifiée avec la version
corrigée (qui attend maintenant que la page ait fini de charger avant de
conclure, et exige deux vérifications concordantes avant de confirmer une
disparition sur ce signal).
========================================================================
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime


def formater_duree(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f} min"
    if minutes < 24 * 60:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / (24 * 60):.1f} j"


def calculer_duree_et_precision(premiere: str | None, disparition: str | None, publiee_le, bornee) -> tuple[str, bool]:
    reference = publiee_le if (bornee and publiee_le) else premiere
    try:
        duree_min = (datetime.fromisoformat(disparition) - datetime.fromisoformat(reference)).total_seconds() / 60
        return (formater_duree(duree_min), bool(bornee and publiee_le))
    except (TypeError, ValueError):
        return ("?", False)


def afficher_disparitions(bd: sqlite3.Connection, limite: int) -> None:
    curseur = bd.execute(
        """
        SELECT item_id, recherche, titre, url, premiere_observation,
               disparition_detectee, publiee_le, publication_bornee
        FROM annonces
        WHERE statut IN ('vendu', 'indisponible')
        ORDER BY disparition_detectee DESC
        LIMIT ?
        """,
        (limite,),
    )
    lignes = curseur.fetchall()

    if not lignes:
        print("Aucune disparition confirmée en base pour l'instant.")
        return

    print(f"{'ID':<14} {'Catégorie':<28} {'Durée':<10} Titre")
    print("-" * 100)
    for item_id, recherche, titre, url, premiere, disparition, publiee_le, bornee in lignes:
        duree_str, precise = calculer_duree_et_precision(premiere, disparition, publiee_le, bornee)
        if not precise:
            duree_str = f"< {duree_str}"
        print(f"{item_id:<14} {(recherche or '')[:28]:<28} {duree_str:<10} {(titre or '')[:55]}")
        print(f"{'':<14} -> {url}")
    print()
    print(f"({len(lignes)} disparition(s) affichée(s), les plus récentes d'abord)")
    print()
    print("Cliquez sur quelques liens pour vérifier si l'annonce est encore en vente,")
    print("en particulier celles dont la durée est très courte ET très proche les unes")
    print("des autres (signature typique du bug corrigé : plusieurs annonces vérifiées")
    print("au même moment, toutes déclarées disparues alors que leur page n'avait")
    print("simplement pas fini de charger).")
    print()
    print("Pour remettre une annonce encore en vente en suivi normal :")
    print("    python revoir_disparitions.py --remettre-actif ID1 ID2 ...")


def remettre_actif(bd: sqlite3.Connection, identifiants: list[str]) -> None:
    for item_id in identifiants:
        try:
            curseur = bd.execute(
                """
                UPDATE annonces
                SET statut = 'active', disparition_detectee = NULL, certitude = NULL,
                    suspicion_depuis = NULL, derniere_verification = NULL
                WHERE item_id = ?
                """,
                (item_id,),
            )
        except sqlite3.OperationalError:
            # Base pas encore migrée (moniteur_vinted.py pas encore relancé
            # depuis la mise à jour) : on se rabat sur les colonnes qui
            # existent déjà, la prochaine migration ajoutera le reste.
            curseur = bd.execute(
                "UPDATE annonces SET statut = 'active', disparition_detectee = NULL, "
                "certitude = NULL, derniere_verification = NULL WHERE item_id = ?",
                (item_id,),
            )
        if curseur.rowcount:
            print(f"✅ {item_id} remise en 'active' — sera re-vérifiée avec la version corrigée.")
        else:
            print(f"⚠️  {item_id} introuvable en base (identifiant incorrect ?).")
    bd.commit()


def main() -> None:
    parseur = argparse.ArgumentParser(
        description="Revoir et corriger les disparitions confirmées avant la vérification robuste.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument(
        "--remettre-actif", nargs="+", metavar="ID",
        help="identifiant(s) (1ère colonne du tableau) à remettre en 'active'",
    )
    parseur.add_argument(
        "--limite", type=int, default=40,
        help="nombre de disparitions à afficher (par défaut 40)",
    )
    parseur.add_argument(
        "--base-donnees", default="vinted_monitor.db",
        help="chemin vers la base (par défaut vinted_monitor.db, dans ce dossier)",
    )
    args = parseur.parse_args()

    bd = sqlite3.connect(args.base_donnees)
    try:
        if args.remettre_actif:
            remettre_actif(bd, args.remettre_actif)
        else:
            afficher_disparitions(bd, args.limite)
    finally:
        bd.close()


if __name__ == "__main__":
    main()
