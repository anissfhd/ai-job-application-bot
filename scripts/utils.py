"""
Fonctions utilitaires partagées par tous les scripts :
chargement de la configuration, journalisation, nettoyage de texte, JSON…
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import yaml

# Racine du projet = dossier parent de scripts/
RACINE = Path(__file__).resolve().parent.parent

# Variables d'environnement qui remplacent les clés du config.yaml si elles existent
VARIABLES_ENV = {
    "OPENAI_API_KEY": ("llm", "openai", "cle_api"),
    "GEMINI_API_KEY": ("llm", "gemini", "cle_api"),
    "GROQ_API_KEY": ("llm", "groq", "cle_api"),
    "ANTHROPIC_API_KEY": ("llm", "claude", "cle_api"),
    "SMTP_PASSWORD": ("email", "mot_de_passe"),
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def charger_yaml(chemin: Path) -> dict:
    """Lit un fichier YAML et renvoie un dictionnaire (vide si le fichier est vide)."""
    with open(chemin, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def chemin_projet(relatif: str | Path) -> Path:
    """Transforme un chemin relatif (depuis config.yaml) en chemin absolu."""
    p = Path(os.path.expanduser(str(relatif)))
    return p if p.is_absolute() else RACINE / p


def charger_config() -> tuple[dict, dict]:
    """
    Charge config/config.yaml et config/profile.yaml.
    Les clés API présentes dans l'environnement (ou dans .env) sont prioritaires.
    """
    # Antivirus (Avast, Kaspersky…) qui inspectent le HTTPS : on utilise le magasin de
    # certificats du système, sinon les appels API échouent (CERTIFICATE_VERIFY_FAILED).
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

    try:
        from dotenv import load_dotenv
        load_dotenv(RACINE / ".env")
    except ImportError:
        pass

    config = charger_yaml(RACINE / "config" / "config.yaml")
    profil = charger_yaml(RACINE / "config" / "profile.yaml")

    for variable, cles in VARIABLES_ENV.items():
        valeur = os.environ.get(variable)
        if valeur:
            noeud = config
            for cle in cles[:-1]:
                noeud = noeud.setdefault(cle, {})
            noeud[cles[-1]] = valeur
    return config, profil


# ---------------------------------------------------------------------------
# Journalisation
# ---------------------------------------------------------------------------

def configurer_logs(dossier_logs: Path, nom: str = "orchestrator") -> logging.Logger:
    """
    Crée un logger qui écrit à la fois dans la console et dans
    logs/<nom>.log (un nouveau fichier chaque jour, 30 jours conservés).
    """
    dossier_logs.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("job_automation")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    format_log = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

    fichier = TimedRotatingFileHandler(dossier_logs / f"{nom}.log", when="midnight",
                                       backupCount=30, encoding="utf-8")
    fichier.setFormatter(format_log)
    logger.addHandler(fichier)

    # Console en UTF-8 (sinon les accents plantent sous Windows)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(format_log)
    logger.addHandler(console)
    return logger


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------

def slugifier(texte: str, longueur_max: int = 50) -> str:
    """« Société Générale (Paris) » -> « Societe_Generale_Paris » (utilisable comme nom de dossier)."""
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    texte = re.sub(r"[^A-Za-z0-9]+", "_", texte).strip("_")
    return (texte or "sans_nom")[:longueur_max]


def normaliser_espaces(texte: str) -> str:
    """Remplace toute suite d'espaces / retours à la ligne par un espace unique."""
    return re.sub(r"\s+", " ", texte or "").strip()


def retirer_balises_code(texte: str) -> str:
    """Supprime les ```latex ... ``` que certains LLM ajoutent autour de leur réponse."""
    texte = texte.strip()
    bloc = re.search(r"```[a-zA-Z]*\s*\n(.*?)```", texte, re.DOTALL)
    if bloc:
        return bloc.group(1).strip()
    return texte


def reparer_guillemets(texte: str) -> str:
    """
    Échappe les guillemets restés NUS à l'intérieur des chaînes d'un JSON.

    Les chats web (DeepSeek) affichent leur réponse en Markdown, et le Markdown mange la
    barre de \\" : une option comme  my "année de Césure"  revient sans échappement, et le
    JSON, pourtant juste à l'origine, ne se décode plus. Un guillemet ne FERME une chaîne
    que s'il est suivi de  :  }  ]  ou d'une virgule qui ouvre l'élément suivant ; tous les
    autres sont des guillemets du texte, qu'on échappe. N'est appelée que si le JSON est
    déjà invalide : un JSON correct n'est jamais touché.
    """
    sortie, dans_chaine, i, n = [], False, 0, len(texte)
    while i < n:
        c = texte[i]
        if dans_chaine and c == "\\":
            sortie.append(texte[i:i + 2])
            i += 2
            continue
        if c == '"':
            if not dans_chaine:
                dans_chaine = True
            else:
                j = i + 1
                while j < n and texte[j].isspace():
                    j += 1
                suivant = texte[j] if j < n else ""
                ferme = suivant in ("", ":", "}", "]")
                if suivant == ",":
                    k = j + 1
                    while k < n and texte[k].isspace():
                        k += 1
                    ferme = k >= n or texte[k] in '"{[-0123456789tfn'
                if ferme:
                    dans_chaine = False
                else:
                    sortie.append('\\"')
                    i += 1
                    continue
        sortie.append(c)
        i += 1
    return "".join(sortie)


def extraire_json(texte: str) -> Any:
    """
    Extrait le premier objet JSON d'une réponse de LLM (même entouré de texte
    ou de balises ```json). Lève ValueError si rien n'est exploitable.
    """
    texte = retirer_balises_code(texte)
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        pass
    debut = texte.find("{")
    fin = texte.rfind("}")
    if debut != -1 and fin > debut:
        try:
            return json.loads(texte[debut:fin + 1])
        except json.JSONDecodeError as e:
            try:   # guillemets du texte dont le Markdown du chat a mangé l'échappement
                return json.loads(reparer_guillemets(texte[debut:fin + 1]))
            except json.JSONDecodeError:
                raise ValueError(f"JSON invalide renvoyé par le LLM : {e}") from e
    raise ValueError("Aucun JSON trouvé dans la réponse du LLM.")


def latex_vers_texte(source: str) -> str:
    """Conversion approximative LaTeX -> texte brut (pour donner la LM au LLM du formulaire)."""
    corps = source.split(r"\begin{document}", 1)[-1].split(r"\end{document}", 1)[0]
    corps = re.sub(r"(?<!\\)%.*", "", corps)                                          # commentaires
    corps = re.sub(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", "", corps, flags=re.S)  # dessins
    corps = re.sub(r"\\(vspace|hspace)\*?\{[^}]*\}", "", corps)                        # espacements
    corps = re.sub(r"\\textcolor\{[^}]*\}", "", corps)                                 # couleur (garde le texte)
    corps = re.sub(r"\\href\{[^}]*\}", "", corps)                                      # lien (garde le texte)
    corps = re.sub(r"\\\\(\[[^\]]*\])?", "\n", corps)                                  # retours à la ligne
    corps = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", corps)                         # autres commandes
    corps = corps.replace("{", "").replace("}", "").replace("~", " ")
    lignes = [re.sub(r"[ \t]+", " ", ligne).strip() for ligne in corps.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lignes)).strip()


# ---------------------------------------------------------------------------
# Divers
# ---------------------------------------------------------------------------

def maintenant() -> str:
    """Date et heure actuelles au format lisible dans Excel."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def pause_aleatoire(intervalle: list | tuple, logger: logging.Logger | None = None) -> None:
    """Attend un nombre aléatoire de secondes dans [min, max] (comportement plus « humain »)."""
    valeurs = list(intervalle) if isinstance(intervalle, (list, tuple)) else [intervalle]
    mini, maxi = valeurs[0], valeurs[-1]
    duree = random.uniform(float(mini), float(maxi))
    if logger:
        logger.info(f"Pause de {duree:.0f} s avant la prochaine offre…")
    time.sleep(duree)
