@echo off
chcp 65001 >nul
title Scraping des offres (Apify -^> Excel)
cd /d "%~dp0"

rem Python du projet s'il existe (openpyxl y est deja installe), sinon celui du systeme.
set PY=%~dp0..\..\.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

"%PY%" scraping.py
echo.
pause
