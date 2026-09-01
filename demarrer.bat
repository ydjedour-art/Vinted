@echo off
REM ==========================================================================
REM Lance le moniteur Vinted en continu (Windows), et le redemarre
REM automatiquement s'il s'arrete de facon inattendue (plantage).
REM
REM Un arret volontaire (Ctrl+C, puis confirmer avec O) est detecte comme
REM normal et n'entraine PAS de redemarrage.
REM
REM Utilisation : double-cliquez sur ce fichier, ou lancez-le depuis une
REM invite de commandes.
REM ==========================================================================

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

:boucle
python moniteur_vinted.py
if %ERRORLEVEL% EQU 0 (
    echo Arret normal du moniteur.
    goto fin
)

echo.
echo Le script s'est arrete de facon inattendue (code %ERRORLEVEL%).
echo Si cela se reproduit tout de suite en boucle, verifiez vinted_monitor.log :
echo c'est probablement une erreur de configuration.
echo Nouvelle tentative dans 30 secondes... (fermez cette fenetre pour tout arreter)
timeout /t 30 /nobreak >nul
goto boucle

:fin
pause
