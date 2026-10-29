@echo off
chcp 65001 >nul
title Reglage du domaine recherche (tri des offres scrapees)
cd /d "%~dp0"

rem Python du projet s'il existe, sinon celui du systeme.
set PY=%~dp0..\..\.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
set PYTHONIOENCODING=utf-8

"%PY%" parametrer_domaine.py
echo.
pause
