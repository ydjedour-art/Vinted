#!/bin/bash
# ==========================================================================
# Lance le tableau de bord web (macOS/Linux) et ouvre votre navigateur dessus.
# Peut tourner en même temps que demarrer.sh, dans une autre fenêtre.
#
# Utilisation :
#   chmod +x tableau_de_bord.sh   (une seule fois)
#   ./tableau_de_bord.sh
# Arrêt : Ctrl+C.
# ==========================================================================

cd "$(dirname "$0")" || exit 1

if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

python3 tableau_de_bord.py
