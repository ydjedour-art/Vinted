@echo off
REM ==========================================================================
REM Lance le tableau de bord web (Windows) et ouvre votre navigateur dessus.
REM Peut tourner en meme temps que demarrer.bat, dans une autre fenetre.
REM
REM Utilisation : double-cliquez sur ce fichier.
REM Arret : fermez cette fenetre, ou Ctrl+C.
REM ==========================================================================

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

python tableau_de_bord.py
pause
