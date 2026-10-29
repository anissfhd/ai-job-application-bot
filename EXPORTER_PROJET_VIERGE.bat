@echo off
rem =====================================================================
rem  Cree une COPIE VIERGE du projet (sans AUCUNE de tes donnees) pour une
rem  autre personne, a cote de ce dossier (+ une archive .zip a lui donner).
rem  Ton dossier n'est pas modifie.
rem =====================================================================
chcp 65001 >nul
cd /d "%~dp0"
title Export d'une copie vierge du projet
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
set PYTHONIOENCODING=utf-8
"%PY%" scripts\exporter_vierge.py
echo.
pause
