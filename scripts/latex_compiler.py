"""
Compilation d'un fichier .tex en PDF (pdflatex, xelatex ou lualatex).

Utilisation en ligne de commande (pour tester ton CV) :
    python scripts/latex_compiler.py input/cv_original.tex
    python scripts/latex_compiler.py input/cv_original.tex --moteur xelatex
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

# Fichiers temporaires produits par LaTeX, supprimés après compilation
EXTENSIONS_TEMPORAIRES = [".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk", ".synctex.gz", ".nav", ".snm"]


class ErreurLatex(Exception):
    """La compilation a échoué. `extrait_log` contient les lignes d'erreur utiles."""

    def __init__(self, message: str, extrait_log: str = ""):
        super().__init__(message)
        self.extrait_log = extrait_log


# Emplacements habituels de MiKTeX / TeX Live, utilisés si le PATH n'est pas encore à jour
# (ex. terminal ouvert avant l'installation de MiKTeX)
DOSSIERS_LATEX = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64",
    Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "MiKTeX" / "miktex" / "bin" / "x64",
    Path("/Library/TeX/texbin"),
    Path("/usr/bin"),
]


def moteur_disponible(moteur: str = "pdflatex") -> str | None:
    """Renvoie le chemin de l'exécutable LaTeX, ou None s'il n'est pas installé."""
    trouve = shutil.which(moteur)
    if trouve:
        return trouve
    for dossier in DOSSIERS_LATEX:
        for nom in (f"{moteur}.exe", moteur):
            if (dossier / nom).is_file():
                return str(dossier / nom)
    return None


def extraire_erreurs(log: str, max_lignes: int = 40) -> str:
    """Garde uniquement les lignes d'erreur d'un .log LaTeX (commencent par « ! ») + contexte."""
    lignes = log.splitlines()
    extrait: list[str] = []
    for i, ligne in enumerate(lignes):
        if ligne.startswith("!") or re.search(r"(Fatal error|Emergency stop|Undefined control sequence)", ligne):
            extrait.extend(lignes[i:i + 4])
    if not extrait:
        extrait = lignes[-max_lignes:]
    return "\n".join(extrait[:max_lignes])


def compiler(chemin_tex: str | Path, dossier_sortie: str | Path | None = None,
             moteur: str = "pdflatex", passes: int = 2, timeout: int = 180,
             nettoyer: bool = True, dossiers_ressources: list | None = None) -> Path:
    """
    Compile `chemin_tex` et renvoie le chemin du PDF produit.

    - dossier_sortie : où écrire le PDF (par défaut : à côté du .tex)
    - passes : nombre de compilations (2 pour les références / numéros de page)
    - dossiers_ressources : dossiers où LaTeX cherche aussi images, .cls, .sty
      (ex. input/ quand le .tex généré est dans output/)
    - lève ErreurLatex si la compilation échoue
    """
    chemin_tex = Path(chemin_tex).resolve()
    if not chemin_tex.exists():
        raise FileNotFoundError(f"Fichier introuvable : {chemin_tex}")
    executable = moteur_disponible(moteur)
    if not executable:
        raise ErreurLatex(
            f"« {moteur} » est introuvable. Installe MiKTeX (Windows), MacTeX (Mac) "
            f"ou texlive (Linux) — voir README, section Prérequis."
        )

    dossier_sortie = Path(dossier_sortie).resolve() if dossier_sortie else chemin_tex.parent
    dossier_sortie.mkdir(parents=True, exist_ok=True)

    commande = [
        executable,
        "-interaction=nonstopmode",   # ne jamais attendre une saisie clavier
        "-halt-on-error",
        "-file-line-error",
        f"-output-directory={dossier_sortie}",
        chemin_tex.name,
    ]

    # TEXINPUTS : dossiers supplémentaires ; le séparateur final garde les chemins par défaut
    env = os.environ.copy()
    if dossiers_ressources:
        dossiers = [str(Path(d).resolve()) for d in dossiers_ressources]
        env["TEXINPUTS"] = os.pathsep.join([".", *dossiers]) + os.pathsep + env.get("TEXINPUTS", "")

    for numero in range(1, passes + 1):
        try:
            resultat = subprocess.run(
                commande, cwd=chemin_tex.parent,  # cwd = dossier du .tex (images, .cls, etc.)
                capture_output=True, timeout=timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            raise ErreurLatex(f"Compilation interrompue après {timeout} s (passe {numero}).")

        if resultat.returncode != 0:
            fichier_log = dossier_sortie / (chemin_tex.stem + ".log")
            log = (fichier_log.read_text(encoding="utf-8", errors="replace") if fichier_log.exists()
                   else resultat.stdout.decode("utf-8", errors="replace"))
            raise ErreurLatex(f"Échec de {moteur} sur {chemin_tex.name} (passe {numero}).",
                              extraire_erreurs(log))

    pdf = dossier_sortie / (chemin_tex.stem + ".pdf")
    if not pdf.exists():
        raise ErreurLatex(f"{moteur} n'a produit aucun PDF pour {chemin_tex.name}.")

    if nettoyer:
        for ext in EXTENSIONS_TEMPORAIRES:
            (dossier_sortie / (chemin_tex.stem + ext)).unlink(missing_ok=True)
    return pdf


if __name__ == "__main__":
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")  # accents corrects dans la console
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Compile un fichier .tex en PDF")
    parser.add_argument("fichier_tex")
    parser.add_argument("--moteur", default="pdflatex", choices=["pdflatex", "xelatex", "lualatex"])
    parser.add_argument("--sortie", default=None, help="dossier de sortie (défaut : à côté du .tex)")
    args = parser.parse_args()
    try:
        print(f"PDF généré : {compiler(args.fichier_tex, args.sortie, args.moteur)}")
    except ErreurLatex as e:
        print(f"ERREUR : {e}\n{e.extrait_log}")
        raise SystemExit(1)
