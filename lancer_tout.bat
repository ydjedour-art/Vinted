@echo off
REM ==========================================================================
REM Lance TOUT en un seul double-clic : le moniteur ET le tableau de bord,
REM chacun dans sa propre fenetre. C'est le moyen le plus simple de demarrer :
REM vous n'avez qu'un seul fichier a retenir.
REM
REM Utilisation : double-cliquez sur ce fichier. C'est tout.
REM Pour tout arreter : fermez les 2 fenetres qui s'ouvrent (moniteur et
REM tableau de bord) - fermer CETTE fenetre-ci n'arrete rien d'autre.
REM ==========================================================================

cd /d "%~dp0"

echo ============================================================
echo   LANCEMENT COMPLET DU MONITEUR VINTED
echo ============================================================
echo.

echo [1/2] Ouverture de la fenetre du moniteur...
start "Moniteur Vinted (a garder ouverte)" demarrer.bat

echo     Attente de quelques secondes avant le tableau de bord...
timeout /t 4 /nobreak >nul

echo [2/2] Ouverture du tableau de bord (votre navigateur va s'ouvrir tout seul)...
start "Tableau de bord Vinted" tableau_de_bord.bat

echo.
echo ============================================================
echo   C'EST FAIT : 2 nouvelles fenetres viennent de s'ouvrir :
echo     - "Moniteur Vinted"       : a garder ouverte, c'est elle qui surveille
echo     - "Tableau de bord Vinted": votre navigateur va s'ouvrir dessus
echo.
echo   Vous pouvez fermer CETTE fenetre-ci (celle-la meme, avec ce texte) :
echo   les 2 autres continuent de tourner independamment.
echo ============================================================
echo.
pause
