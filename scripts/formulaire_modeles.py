"""
Modèles de fichiers du candidat (config/modeles/*.modele.*) : la STRUCTURE exacte des fichiers
d'origine du projet (clés, ordre, commentaires), avec des emplacements {{chemin.de.la.cle}} à la place des
valeurs. FORMULAIRE.bat les remplit ; l'export vierge les écrit avec des valeurs vides.

Remplir un modèle avec les valeurs d'un fichier existant redonne ce fichier (même contenu YAML) :
c'est ce qui garantit que chaque candidat a EXACTEMENT la même structure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from utils import chemin_projet

DOSSIER_MODELES = chemin_projet("config/modeles")
REPERE = re.compile(r"\{\{([a-z0-9_.]+)\}\}")


def reperes(modele: str) -> list[str]:
    """Chemins des valeurs attendues par un modèle, dans l'ordre."""
    return list(dict.fromkeys(REPERE.findall(modele)))


def _yaml(valeur) -> str:
    """Valeur écrite sur UNE ligne, en YAML valide (chaîne entre guillemets, liste en ligne)."""
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    if isinstance(valeur, list):
        return json.dumps([str(v) for v in valeur], ensure_ascii=False)
    return json.dumps("" if valeur is None else str(valeur), ensure_ascii=False)


def remplir(modele: str, valeurs: dict) -> str:
    """
    Remplace chaque {{chemin}} par sa valeur. Un emplacement seul sur sa ligne et une liste
    donnent une liste « à tirets » (ex. langues) ; sinon la valeur est écrite en ligne.
    Toute valeur manquante est une erreur : un fichier n'est jamais écrit à moitié.
    """
    manquants = [c for c in reperes(modele) if c not in valeurs]
    if manquants:
        raise KeyError(f"Valeurs manquantes pour le modèle : {manquants}")
    lignes = []
    for ligne in modele.splitlines():
        seul = REPERE.fullmatch(ligne.strip())
        if seul and isinstance(valeurs[seul.group(1)], list) and ligne.strip() == ligne:
            lignes += [f"  - {json.dumps(str(v), ensure_ascii=False)}" for v in valeurs[seul.group(1)]]
            continue
        lignes.append(REPERE.sub(lambda m: _yaml(valeurs[m.group(1)]), ligne))
    return "\n".join(lignes) + "\n"


def valeurs_de(donnees: dict, modele: str) -> dict:
    """Valeurs d'un fichier existant, rangées par chemin (sens inverse de remplir)."""
    res = {}
    for chemin in reperes(modele):
        noeud = donnees
        for cle in chemin.split("."):
            noeud = (noeud or {}).get(cle) if isinstance(noeud, dict) else None
        res[chemin] = noeud if noeud is not None else ""
    return res


def valeurs_vides(modele: str, listes: tuple[str, ...] = ()) -> dict:
    return {c: ([] if c in listes else "") for c in reperes(modele)}


def lire_modele(nom: str) -> str:
    return (DOSSIER_MODELES / nom).read_text(encoding="utf-8")


def ecrire(fichier: Path, texte: str) -> None:
    """Écriture sûre : fichier temporaire complet, contrôlé (YAML/JSON lisible), puis remplacement."""
    fichier = Path(fichier)
    if fichier.suffix in (".yaml", ".yml"):
        yaml.safe_load(texte)
    elif fichier.suffix == ".json":
        json.loads(texte)
    tmp = fichier.with_name(fichier.name + ".tmp")
    tmp.write_text(texte, encoding="utf-8")
    tmp.replace(fichier)
