#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
========================================================================
 TABLEAU DE BORD — vue web du moniteur Vinted
========================================================================

Petite application séparée qui n'a qu'un seul rôle : LIRE le fichier
vinted_monitor.db (créé et mis à jour par moniteur_vinted.py) et vous
présenter son contenu joliment dans votre navigateur, plutôt que de devoir
lire des lignes de texte dans un terminal.

Elle n'écrit jamais dans la base de données, et n'a aucune action sur
Vinted : elle tourne à côté de moniteur_vinted.py, complètement
indépendante. Vous pouvez la lancer, la fermer, la relancer, sans que ça
affecte la surveillance en cours.

Utilisation :
    python tableau_de_bord.py
Puis votre navigateur s'ouvre automatiquement sur http://localhost:8080
(sinon, ouvrez ce lien vous-même). La page se rafraîchit toute seule.

Arrêt : Ctrl+C dans cette fenêtre (ou fermez-la simplement).
========================================================================
"""

from __future__ import annotations

import http.server
import os
import socketserver
import sqlite3
import webbrowser
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

PORT = 8080
CHEMIN_CONFIG = "config.yaml"
INTERVALLE_RAFRAICHISSEMENT_SECONDES = 30


# ========================================================================
# Configuration (juste pour retrouver le fichier de base de données et le
# fuseau horaire — on réutilise config.yaml sans avoir besoin de tout
# comprendre de moniteur_vinted.py).
# ========================================================================

def charger_reglages() -> dict:
    reglages = {"base_donnees": "vinted_monitor.db", "fuseau_horaire": "Europe/Paris"}
    if os.path.exists(CHEMIN_CONFIG):
        try:
            with open(CHEMIN_CONFIG, "r", encoding="utf-8") as fichier:
                config = yaml.safe_load(fichier) or {}
            reglages["base_donnees"] = config.get("stockage", {}).get("base_donnees", reglages["base_donnees"])
            reglages["fuseau_horaire"] = config.get("fuseau_horaire", reglages["fuseau_horaire"])
        except Exception:
            pass  # on garde les valeurs par défaut si config.yaml pose problème
    return reglages


# ========================================================================
# Lecture des données
# ========================================================================

def formater_duree_minutes(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"{minutes:.0f} min"
    heures = minutes / 60
    if heures < 24:
        return f"{heures:.1f} h"
    return f"{heures / 24:.1f} j"


def formater_date(iso_str: str | None, fuseau: ZoneInfo) -> str:
    if not iso_str:
        return "—"
    try:
        return datetime.fromisoformat(iso_str).astimezone(fuseau).strftime("%d/%m %H:%M")
    except Exception:
        return "—"


def calculer_duree_minutes(debut_iso: str | None, fin_iso: str | None) -> float | None:
    if not debut_iso or not fin_iso:
        return None
    try:
        debut = datetime.fromisoformat(debut_iso)
        fin = datetime.fromisoformat(fin_iso)
        return (fin - debut).total_seconds() / 60
    except Exception:
        return None


def recuperer_donnees(chemin_bd: str) -> dict | None:
    """Lit tout ce qu'il faut dans la base pour construire le tableau de
    bord. Renvoie None si la base n'existe pas encore (script jamais lancé)."""
    if not os.path.exists(chemin_bd):
        return None

    connexion = sqlite3.connect(chemin_bd)
    connexion.row_factory = sqlite3.Row
    curseur = connexion.cursor()

    curseur.execute("SELECT DISTINCT recherche FROM annonces ORDER BY recherche")
    noms_recherches = [ligne["recherche"] for ligne in curseur.fetchall()]

    stats_par_categorie = []
    toutes_durees: list[float] = []
    total_actives = 0
    total_resolues = 0

    for nom in noms_recherches:
        curseur.execute(
            "SELECT COUNT(*) AS n FROM annonces WHERE recherche = ? AND statut = 'active'", (nom,)
        )
        n_actives = curseur.fetchone()["n"]

        curseur.execute(
            """
            SELECT premiere_observation, disparition_detectee
            FROM annonces
            WHERE recherche = ? AND statut IN ('vendu', 'indisponible')
            """,
            (nom,),
        )
        durees_categorie = [
            d for d in (
                calculer_duree_minutes(ligne["premiere_observation"], ligne["disparition_detectee"])
                for ligne in curseur.fetchall()
            ) if d is not None
        ]

        stats_par_categorie.append(
            {
                "nom": nom,
                "actives": n_actives,
                "resolues": len(durees_categorie),
                "vitesse_moyenne": (sum(durees_categorie) / len(durees_categorie)) if durees_categorie else None,
            }
        )
        total_actives += n_actives
        total_resolues += len(durees_categorie)
        toutes_durees.extend(durees_categorie)

    curseur.execute(
        """
        SELECT titre, prix, url, recherche, premiere_observation, disparition_detectee, statut
        FROM annonces
        WHERE statut IN ('vendu', 'indisponible')
        ORDER BY disparition_detectee DESC
        LIMIT 25
        """
    )
    dernieres_disparitions = [dict(ligne) for ligne in curseur.fetchall()]

    connexion.close()

    return {
        "total_actives": total_actives,
        "total_resolues": total_resolues,
        "vitesse_moyenne_globale": (sum(toutes_durees) / len(toutes_durees)) if toutes_durees else None,
        "stats_par_categorie": stats_par_categorie,
        "dernieres_disparitions": dernieres_disparitions,
    }


# ========================================================================
# Génération de la page HTML
# ========================================================================

STYLE = """
:root {
  --bg: #f4f5f7; --carte: #ffffff; --texte: #1a1a2e; --texte-att: #6b7280;
  --accent: #4f46e5; --accent-clair: #eef2ff; --succes: #059669; --succes-bg: #ecfdf5;
  --bordure: #e5e7eb;
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  background: var(--bg); color: var(--texte); margin: 0; padding: 2rem 2.5rem;
}
.conteneur { max-width: 1100px; margin: 0 auto; }
header { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: .5rem; margin-bottom: 2rem; }
h1 { font-size: 1.4rem; margin: 0; }
.maj { color: var(--texte-att); font-size: .85rem; }
.cartes { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
.carte { background: var(--carte); border: 1px solid var(--bordure); border-radius: 12px; padding: 1.1rem 1.25rem; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
.carte .valeur { font-size: 1.9rem; font-weight: 700; color: var(--accent); line-height: 1.1; }
.carte .label { color: var(--texte-att); font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; margin-top: .3rem; }
section { margin-bottom: 2rem; }
h2 { font-size: 1.02rem; margin: 0 0 .75rem; }
table { width: 100%; border-collapse: collapse; background: var(--carte); border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.05); border: 1px solid var(--bordure); }
th { text-align: left; padding: .65rem .9rem; background: var(--accent-clair); color: var(--accent); font-size: .72rem; text-transform: uppercase; letter-spacing: .03em; }
td { padding: .65rem .9rem; border-top: 1px solid var(--bordure); font-size: .87rem; vertical-align: top; }
tbody tr:hover td { background: #fafafa; }
.badge { background: var(--succes-bg); color: var(--succes); padding: .15rem .55rem; border-radius: 999px; font-size: .72rem; font-weight: 600; white-space: nowrap; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.vide { color: var(--texte-att); padding: 2.5rem; text-align: center; background: var(--carte); border-radius: 12px; border: 1px dashed var(--bordure); }
.titre-annonce { max-width: 320px; }
"""


def page_attente(chemin_bd: str) -> str:
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{INTERVALLE_RAFRAICHISSEMENT_SECONDES}">
<title>Tableau de bord Vinted</title>
<style>{STYLE}
body {{ display: flex; align-items: center; justify-content: center; height: 100vh; }}
</style></head><body>
<div class="vide" style="max-width:420px">
  <h2>⏳ Pas encore de données</h2>
  <p>Le fichier <code>{chemin_bd}</code> n'existe pas encore.</p>
  <p>Lancez d'abord le moniteur (<code>demarrer.bat</code> ou
  <code>python moniteur_vinted.py</code>) — cette page se mettra à jour
  toute seule dès qu'il aura des données.</p>
</div>
</body></html>"""


def generer_page(donnees: dict, fuseau: ZoneInfo) -> str:
    maintenant = datetime.now(fuseau).strftime("%d/%m/%Y à %H:%M:%S")

    lignes_categories = "".join(
        f"""<tr>
            <td>{c['nom']}</td>
            <td>{c['actives']}</td>
            <td>{c['resolues']}</td>
            <td>{formater_duree_minutes(c['vitesse_moyenne'])}</td>
        </tr>"""
        for c in donnees["stats_par_categorie"]
    ) or '<tr><td colspan="4" style="text-align:center;color:var(--texte-att)">Aucune catégorie observée pour l’instant</td></tr>'

    lignes_ventes = ""
    for a in donnees["dernieres_disparitions"]:
        duree_min = calculer_duree_minutes(a["premiere_observation"], a["disparition_detectee"])
        prix = f"{a['prix']:.2f} €" if a.get("prix") is not None else "—"
        rapide = duree_min is not None and duree_min <= 180
        badge = f'<span class="badge">⚡ rapide</span>' if rapide else ""
        titre = (a.get("titre") or "Titre indisponible")
        lignes_ventes += f"""<tr>
            <td class="titre-annonce"><a href="{a['url']}" target="_blank" rel="noopener">{titre}</a> {badge}</td>
            <td>{prix}</td>
            <td>{a['recherche']}</td>
            <td>{formater_date(a['premiere_observation'], fuseau)}</td>
            <td>{formater_date(a['disparition_detectee'], fuseau)}</td>
            <td>{formater_duree_minutes(duree_min)}</td>
        </tr>"""

    if not lignes_ventes:
        lignes_ventes = '<tr><td colspan="6" style="text-align:center;color:var(--texte-att)">Aucune disparition détectée pour l’instant</td></tr>'

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{INTERVALLE_RAFRAICHISSEMENT_SECONDES}">
<title>Tableau de bord Vinted</title>
<style>{STYLE}</style>
</head><body>
<div class="conteneur">
  <header>
    <h1>🕵️ Moniteur Vinted</h1>
    <span class="maj">Mis à jour le {maintenant} · actualisation automatique toutes les {INTERVALLE_RAFRAICHISSEMENT_SECONDES}s</span>
  </header>

  <div class="cartes">
    <div class="carte"><div class="valeur">{donnees['total_actives']}</div><div class="label">Annonces suivies</div></div>
    <div class="carte"><div class="valeur">{donnees['total_resolues']}</div><div class="label">Disparitions détectées</div></div>
    <div class="carte"><div class="valeur">{formater_duree_minutes(donnees['vitesse_moyenne_globale'])}</div><div class="label">Vitesse moyenne</div></div>
  </div>

  <section>
    <h2>Par catégorie</h2>
    <table>
      <thead><tr><th>Catégorie</th><th>Actives</th><th>Disparitions</th><th>Vitesse moyenne</th></tr></thead>
      <tbody>{lignes_categories}</tbody>
    </table>
  </section>

  <section>
    <h2>Dernières disparitions (vendues probable, ou retirées)</h2>
    <table>
      <thead><tr><th>Annonce</th><th>Prix</th><th>Catégorie</th><th>Vue la 1ère fois</th><th>Disparue le</th><th>Vitesse</th></tr></thead>
      <tbody>{lignes_ventes}</tbody>
    </table>
  </section>
</div>
</body></html>"""


# ========================================================================
# Petit serveur web local
# ========================================================================

class Gestionnaire(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        reglages = charger_reglages()
        donnees = recuperer_donnees(reglages["base_donnees"])

        if donnees is None:
            contenu = page_attente(reglages["base_donnees"])
        else:
            fuseau = ZoneInfo(reglages["fuseau_horaire"])
            contenu = generer_page(donnees, fuseau)

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(contenu.encode("utf-8"))

    def log_message(self, format, *args) -> None:  # noqa: A002 - signature imposée par la classe parente
        pass  # silence les lignes "GET / HTTP/1.1" à chaque rafraîchissement


def main() -> None:
    try:
        serveur = socketserver.TCPServer(("127.0.0.1", PORT), Gestionnaire)
    except OSError as erreur:
        raise SystemExit(
            f"❌ Impossible de démarrer sur le port {PORT} : {erreur}\n"
            f"   Un autre programme l'utilise peut-être déjà (ou une ancienne\n"
            f"   fenêtre du tableau de bord est encore ouverte quelque part).\n"
            f"   Fermez-la, ou modifiez la valeur de PORT en haut de ce fichier."
        )

    with serveur:
        url = f"http://localhost:{PORT}"
        print("=" * 60)
        print("🕵️  Tableau de bord Vinted")
        print(f"   Disponible sur : {url}")
        print("   Laissez cette fenêtre ouverte. Ctrl+C pour arrêter.")
        print("=" * 60)
        try:
            webbrowser.open(url)
        except Exception:
            pass  # tant pis, l'utilisateur ouvrira le lien lui-même

        try:
            serveur.serve_forever()
        except KeyboardInterrupt:
            print("\n✅ Tableau de bord arrêté proprement.")


if __name__ == "__main__":
    main()
