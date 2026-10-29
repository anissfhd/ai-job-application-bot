@echo off
rem =====================================================================
rem  INSTALLATION COMPLETE — double-clique sur ce fichier.
rem
rem  Sur une machine neuve, installe TOUT ce dont le projet a besoin :
rem     Python 3.14.6 (64 bits) . Google Chrome . MiKTeX (pdflatex)
rem     + les paquets Python aux versions EXACTES de la machine d'origine
rem       (requirements-verrouille.txt)
rem
rem  Rien a installer soi-meme. Compter 10 a 20 minutes la premiere fois.
rem =====================================================================
chcp 65001 >nul
cd /d "%~dp0"
title Installation - job-automation

rem --- Droits administrateur : necessaires pour installer Chrome et MiKTeX ---
net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo.
    echo  Cette installation a besoin des droits administrateur.
    echo  Une fenetre de confirmation Windows va s'ouvrir : reponds "Oui".
    echo.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cls
echo ==========================================================
echo    INSTALLATION COMPLETE - CANDIDATURES AUTOMATIQUES
echo ==========================================================
echo.
echo  Ce script va installer, s'ils manquent :
echo     - Python 3.14.6 (64 bits)
echo     - Google Chrome
echo     - MiKTeX (pdflatex, pour le CV et la lettre)
echo     - les paquets Python, aux versions exactes de la machine d'origine
echo.
echo  Ce qui est deja present et a jour n'est pas retouche.
echo  Duree : 10 a 20 minutes selon la connexion.
echo.
pause

if not exist "scripts\installer_complet.ps1" (
    echo.
    echo  ERREUR : scripts\installer_complet.ps1 est introuvable.
    echo  L'archive a-t-elle ete extraite en entier ? Ce fichier doit rester
    echo  dans le meme dossier que LANCER.bat.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\installer_complet.ps1"

echo.
pause
