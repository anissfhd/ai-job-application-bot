"""
Réglage du DOMAINE recherché — lancé par PARAMETRER_SCRAPING.bat.

Chacun cherche dans son propre domaine (IT sauf cyber, marketing sans IT…). Les réponses sont
écrites dans criteres_domaine.json ; le robot séparé scripts/verify_eligibility.py les met
telles quelles dans la consigne envoyée à DeepSeek pour trier chaque offre scrapée :
gardée (« À traiter ») ou écartée (feuille « Écartées » de l'Excel).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent
CRITERES = DOSSIER / "criteres_domaine.json"
CONFIG = DOSSIER / "config_scraping.json"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

QUESTIONS = [
    ("diplome", "1. Quel DIPLÔME possèdes-tu (ou prépares-tu) exactement ?",
     "ex. « Diplôme d'ingénieur en informatique, 5e année, INSA Lyon, obtention 2027 »"),
    ("domaine_souhaite", "2. Quel DOMAINE (ou quels postes) veux-tu ?",
     "ex. « Informatique : développement, IA, data, cloud… » ou « Marketing digital »"),
    ("inclure", "3. Qu'est-ce qu'il faut INCLURE en plus (cas limites acceptés) ?",
     "ex. « un poste IT combiné avec un autre domaine est accepté »"),
    ("exclure", "4. Qu'est-ce qu'il faut EXCLURE ?",
     "ex. « la cybersécurité ; tout poste commercial, industriel ou marketing »"),
    ("cas_mixtes", "5. Que faire d'un poste MIXTE (un peu de ce que tu veux, un peu de ce que tu exclus) ?",
     "ex. « accepter si le cœur du poste est dans mon domaine, refuser sinon »"),
    ("precisions", "6. Autres précisions pour le tri (facultatif) ?",
     "ex. « les postes de chef de projet digital sont acceptés »"),
]


def lire_json(fichier: Path) -> dict:
    try:
        return json.loads(fichier.read_text(encoding="utf-8")) if fichier.exists() else {}
    except (OSError, ValueError):
        return {}


def ecrire_json(fichier: Path, donnees: dict) -> None:
    tmp = fichier.with_suffix(".tmp")
    tmp.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(fichier)


def main() -> int:
    criteres = lire_json(CRITERES)
    config = lire_json(CONFIG)
    print("=" * 70)
    print("  RÉGLAGE DU DOMAINE RECHERCHÉ (tri des offres scrapées par DeepSeek)")
    print("=" * 70)
    print("\nPour chaque question : écris ta réponse puis Entrée.")
    print("Entrée seule = garder la réponse actuelle.   « - » seul = vider la réponse.\n")
    for cle, question, exemple in QUESTIONS:
        actuel = str(criteres.get(cle, "")).strip()
        print(question)
        print(f"   ({exemple})")
        print(f"   Actuel : {actuel or '(vide)'}")
        saisie = input("   Ta réponse : ").strip()
        if saisie == "-":
            criteres[cle] = ""
        elif saisie:
            criteres[cle] = saisie
        print()
    if not str(criteres.get("domaine_souhaite", "")).strip():
        print("Le domaine souhaité (question 2) est obligatoire : rien n'a été enregistré.")
        return 1

    active = bool(config.get("verification_domaine", True))
    rep = input(f"Activer le tri automatique des offres scrapées ? (O/n) [actuel : "
                f"{'oui' if active else 'non'}] : ").strip().lower()
    if rep in ("o", "oui", "y", "yes"):
        active = True
    elif rep in ("n", "non", "no"):
        active = False

    criteres["modifie_le"] = datetime.now().strftime("%Y-%m-%d")
    ecrire_json(CRITERES, criteres)
    config["verification_domaine"] = active
    ecrire_json(CONFIG, config)

    print("\n" + "-" * 70)
    print("Enregistré. Consigne qui sera donnée à DeepSeek pour chaque offre :\n")
    for cle, question, _ in QUESTIONS:
        if str(criteres.get(cle, "")).strip():
            print(f"  • {question.split(' ', 1)[1]}\n      {criteres[cle]}")
    print(f"\nTri automatique : {'ACTIVÉ' if active else 'DÉSACTIVÉ (les offres arrivent directement en « À traiter »)'}")
    print("Il s'applique aux PROCHAINS scrapings ; les offres déjà dans l'Excel ne sont pas touchées.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
