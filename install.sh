#!/usr/bin/env bash
# =====================================================================
#  Installation Linux / macOS — job-automation
#  Lancer depuis le dossier du projet :   bash install.sh
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"

ok()   { printf '\033[32m[OK]\033[0m     %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m      %s\n' "$*"; }
err()  { printf '\033[31m[ERREUR]\033[0m %s\n' "$*"; }

# --- 1. Python >= 3.10 -------------------------------------------------
PY=""
for candidat in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidat" >/dev/null 2>&1 && \
     "$candidat" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidat"; break
  fi
done
if [ -z "$PY" ]; then
  err "Python 3.10+ introuvable."
  echo "   Ubuntu/Debian : sudo apt install python3 python3-venv python3-pip"
  echo "   macOS         : brew install python@3.12"
  exit 1
fi
ok "Python $($PY -c 'import platform; print(platform.python_version())') trouvé ($PY)"

# --- 2. Environnement virtuel + dépendances ---------------------------
if [ ! -x .venv/bin/python ]; then
  echo "Création de l'environnement virtuel .venv ..."
  "$PY" -m venv .venv || { err "Échec de venv (Ubuntu : sudo apt install python3-venv)"; exit 1; }
fi
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt --quiet
ok "Dépendances Python installées dans .venv"

# --- 3. LaTeX -----------------------------------------------------------
if command -v pdflatex >/dev/null 2>&1; then
  ok "pdflatex trouvé : $(command -v pdflatex)"
else
  warn "pdflatex introuvable. Installe-le :"
  echo "   Ubuntu/Debian : sudo apt install texlive-latex-extra texlive-fonts-recommended texlive-lang-french texlive-xetex"
  echo "   macOS         : brew install --cask mactex-no-gui"
fi

# --- 4. Chrome / Chromium ---------------------------------------------
if command -v google-chrome >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1 || \
   command -v chromium-browser >/dev/null 2>&1 || [ -d "/Applications/Google Chrome.app" ]; then
  ok "Chrome/Chromium trouvé"
else
  warn "Chrome introuvable. Ubuntu : wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && sudo apt install ./google-chrome-stable_current_amd64.deb"
fi

# --- 5. Dossiers et fichiers modèles ----------------------------------
mkdir -p input output/Candidatures logs config prompts
touch output/Candidatures/.gitkeep logs/.gitkeep
.venv/bin/python scripts/excel_manager.py --init
[ -f .env ] || { cp .env.example .env; ok "Fichier .env créé (à remplir)"; }
for f in input/cv_original.tex input/lm_original.tex; do
  [ -f "$f" ] || warn "À faire : dépose ton fichier $f"
done

echo
ok "Installation terminée."
echo "Étapes suivantes :"
echo "  1. Remplis config/config.yaml (clé API) et config/profile.yaml (tes infos)"
echo "  2. Dépose input/cv_original.tex et input/lm_original.tex"
echo "  3. Vérifie :   .venv/bin/python scripts/orchestrator.py --verifier"
echo "  4. Lance :     .venv/bin/python scripts/orchestrator.py"
