@echo off
rem =====================================================================
rem  FORMULAIRE - installe le robot pour UN candidat : identite, objectif,
rem  CV / lettre / photo, mot de passe des sites, navigateurs, domaine.
rem  Toutes les sections sont obligatoires ; on peut s'arreter et reprendre.
rem =====================================================================
chcp 65001 >nul
cd /d "%~dp0"
title Formulaire - installation du candidat
set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
    echo.
    echo  L'environnement Python n'est pas installe : lance d'abord
    echo  Click_here_to_install_all_requirements.bat
    echo.
    pause
    exit /b 1
)
set PYTHONIOENCODING=utf-8
"%PY%" scripts\formulaire.py
echo.
pause
