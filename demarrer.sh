#!/bin/bash
# ==========================================================================
# Lance le moniteur Vinted en continu (macOS / Linux), et le redémarre
# automatiquement s'il s'arrête de façon inattendue (plantage).
#
# Un arrêt volontaire (Ctrl+C) est détecté comme normal et n'entraîne PAS
# de redémarrage.
#
# Utilisation :
#   chmod +x demarrer.sh     (une seule fois)
#   ./demarrer.sh
#
# Pour que ça continue même en fermant le terminal, lancez-le dans une
# session tmux ou screen (voir le README, section "Déploiement").
# ==========================================================================

cd "$(dirname "$0")" || exit 1

if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

while true; do
    python moniteur_vinted.py
    code_sortie=$?

    if [ $code_sortie -eq 0 ]; then
        echo "Arrêt normal du moniteur."
        break
    fi

    echo "⚠️  Le script s'est arrêté de façon inattendue (code $code_sortie)."
    echo "    Si cela se reproduit tout de suite en boucle, vérifiez"
    echo "    vinted_monitor.log : c'est probablement une erreur de configuration."
    echo "    Nouvelle tentative dans 30 secondes... (Ctrl+C pour tout arrêter)"
    sleep 30
done
