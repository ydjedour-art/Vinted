#!/bin/bash
# ==========================================================================
# Lance TOUT en une seule commande : le moniteur ET le tableau de bord, tous
# les deux en arrière-plan de ce terminal. C'est le moyen le plus simple de
# démarrer : une seule commande à retenir.
#
# Utilisation :
#   chmod +x lancer_tout.sh   (une seule fois)
#   ./lancer_tout.sh
#
# Pour tout arrêter : Ctrl+C dans ce terminal (arrête les deux d'un coup),
# ou fermez simplement ce terminal.
# ==========================================================================

cd "$(dirname "$0")" || exit 1

echo "============================================================"
echo "  LANCEMENT COMPLET DU MONITEUR VINTED"
echo "============================================================"
echo

echo "[1/2] Démarrage du moniteur en arrière-plan..."
chmod +x demarrer.sh tableau_de_bord.sh 2>/dev/null
./demarrer.sh &
PID_MONITEUR=$!

sleep 4

echo "[2/2] Démarrage du tableau de bord (le navigateur va s'ouvrir tout seul)..."
./tableau_de_bord.sh &
PID_DASHBOARD=$!

echo
echo "============================================================"
echo "  C'EST FAIT : le moniteur et le tableau de bord tournent."
echo "  Ctrl+C ici arrête les deux d'un coup."
echo "============================================================"
echo

# Arrête proprement les deux processus si on interrompt ce script.
trap 'echo; echo "Arrêt du moniteur et du tableau de bord..."; kill $PID_MONITEUR $PID_DASHBOARD 2>/dev/null; wait; exit 0' INT TERM

wait
