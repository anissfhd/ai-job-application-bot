"""
EXPORTER_PROJET_VIERGE.bat — crée une COPIE PROPRE du projet pour une autre personne, SANS
AUCUNE donnée du candidat actuel. Le dossier d'origine n'est jamais modifié (lecture seule).

La copie contient tout le robot (code, consignes, réglages, mémoire des sites) et, à la place
des données personnelles, des fichiers VIDES à la structure exacte (config/modeles/) :
profil, objectif, critères de domaine, liens de recherche, Excel neuf, .env sans secret.
La nouvelle personne lance Click_here_to_install_all_requirements.bat puis FORMULAIRE.bat.

Pour finir, la copie est FOUILLÉE : chaque nom, email, téléphone, ville, école, entreprise
de stage, clé ou mot de passe du candidat actuel y est recherché. Le moindre reste est listé.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "scripts"))

import yaml  # noqa: E402


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# Jamais copiés : données, historique, secrets, fichiers de travail
DOSSIERS_EXCLUS = {".venv", "__pycache__", ".git", "output", "logs", "rapports", "sauvegardes", "ancien",
                   "sauvegardes_formulaire", "cv_gabarit", "deepseek", "images"}
FICHIERS_EXCLUS = {".env", "entreprises.xlsx", "cv_original.tex", "lm_original.tex", "offres_scrapees.json",
                   "formulaire_reponses.json", "etat.json", "verify_eligibility.verrou"}
EXTENSIONS_EXCLUES = {".rar", ".zip", ".verrou", ".tmp", ".log", ".jpg", ".jpeg"}
# Images d'icônes génériques gardées (utilisées par des CV LaTeX)
ICONES = {"GitHub.png", "LinkedIn.png"}


def exclu(chemin: Path) -> bool:
    rel = chemin.relative_to(RACINE)
    if any(p in DOSSIERS_EXCLUS for p in rel.parts[:-1]) or (chemin.is_dir() and chemin.name in DOSSIERS_EXCLUS):
        return True
    if chemin.is_file():
        if chemin.name in FICHIERS_EXCLUS or chemin.suffix.lower() in EXTENSIONS_EXCLUES:
            return True
        if rel.parts[0] == "input" and len(rel.parts) == 2 and chemin.suffix.lower() in (".png", ".pdf", ".tex") \
                and chemin.name not in ICONES:
            return True                                   # photo, CV, lettre du candidat
        if ".enregistrement." in chemin.name or chemin.name.endswith(".tmp"):
            return True
    return False


def copier(dest: Path) -> int:
    n = 0
    for d, dossiers, fichiers in os.walk(RACINE):
        d = Path(d)
        if d != RACINE and exclu(d):
            dossiers[:] = []
            continue
        dossiers[:] = [x for x in dossiers if not exclu(d / x) and not (d / x).resolve() == dest.resolve()]
        for f in fichiers:
            src = d / f
            if exclu(src):
                continue
            cible = dest / src.relative_to(RACINE)
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, cible)
            n += 1
    return n


def secrets_et_identite() -> dict[str, list[str]]:
    """Ce qu'on recherchera dans la copie : tout ce qui identifie le candidat actuel."""
    p = yaml.safe_load((RACINE / "config" / "profile.yaml").read_text(encoding="utf-8")) or {}
    ident, liens, form = p.get("identite", {}) or {}, p.get("liens", {}) or {}, p.get("formation", {}) or {}
    rep = p.get("reponses_standard", {}) or {}
    mots = [ident.get("prenom"), ident.get("nom"), ident.get("email"), ident.get("adresse"),
            ident.get("date_naissance"), liens.get("linkedin"), liens.get("github")]
    tel = re.sub(r"\D", "", str(ident.get("telephone") or ""))
    mots += [tel[-9:] if len(tel) >= 9 else "", str(rep.get("telephone_sans_indicatif") or "")]
    for champ in ("ecole",):
        mots += [w for w in re.split(r"[\s,()]+", str(form.get(champ) or "")) if len(w) >= 4 and w.isupper()]
    # Entreprises des stages et handle des liens
    try:
        fixe = json.loads((RACINE / "config" / "cv_gabarit" / "fixe_fr.json").read_text(encoding="utf-8"))
        for k, v in fixe.items():
            if re.fullmatch(r"STAGE\d+", k):
                nom = re.sub(r"\\[a-zA-Z]+|[{}]", "", v)
                nom = re.sub(r"^(stage|internship)\s+(chez|at|au sein d[eu]s?|within)\s+", "", nom.strip(), flags=re.I)
                mots.append(nom.strip())
    except Exception:  # noqa: BLE001
        pass
    secrets = []
    for env in (RACINE / ".env", RACINE / "input" / "Scraping" / ".env"):
        if env.exists():
            for l in env.read_text(encoding="utf-8").splitlines():
                nom, _, val = l.partition("=")
                if val.strip() and len(val.strip()) >= 6 and not l.startswith("#"):
                    secrets.append(val.strip().strip('"').strip("'"))
    try:
        cle = json.loads((RACINE / "extensions" / "nopecha" / "manifest.json").read_text(encoding="utf-8"))["nopecha"]["key"]
        if cle:
            secrets.append(cle)
    except Exception:  # noqa: BLE001
        pass
    return {"identité": [m for m in dict.fromkeys(str(x).strip() for x in mots) if m and len(m) >= 4],
            "secret": [s for s in dict.fromkeys(secrets) if s]}


def ecrire_fichiers_vides(dest: Path) -> None:
    import formulaire_modeles as MM
    cfg = dest / "config"
    MM.ecrire(cfg / "profile.yaml", MM.remplir(MM.lire_modele("profile.modele.yaml"),
                                              MM.valeurs_vides(MM.lire_modele("profile.modele.yaml"),
                                                               ("langues", "stage.pays_cibles"))))
    vides = MM.valeurs_vides(MM.lire_modele("objectif.modele.yaml"), ("contrats_acceptes",))
    vides["conventionne"] = False
    MM.ecrire(cfg / "objectif.yaml", MM.remplir(MM.lire_modele("objectif.modele.yaml"), vides))
    (cfg / "cv_gabarit").mkdir(parents=True, exist_ok=True)
    (cfg / "deepseek" / "nouveaux").mkdir(parents=True, exist_ok=True)
    (cfg / "cv_gabarit" / "LISEZ-MOI.txt").write_text(
        "Ce dossier est rempli par FORMULAIRE.bat (section 9 : CV, lettre de motivation et photo).\n", encoding="utf-8")
    # config.yaml : garde-fous personnels et commentaire avec l'email retirés
    texte = (cfg / "config.yaml").read_text(encoding="utf-8")
    texte = re.sub(r"(  chaines_obligatoires_cv:\n)((?:    - .*\n)*)", "  chaines_obligatoires_cv: []\n", texte, count=1)
    texte = re.sub(r"\(\S+@\S+\)", "(ton email de candidature)", texte)
    MM.ecrire(cfg / "config.yaml", texte)
    for bat in dest.glob("*.bat"):                    # ex. « Google : <email> » dans LANCER.bat
        brut = bat.read_bytes().decode("utf-8")
        bat.write_bytes(re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "ton email de candidature", brut).encode("utf-8"))
    # Scraping : critères vides, liens vides, clés vides
    scr = dest / "input" / "Scraping"
    criteres = {"diplome": "", "domaine_souhaite": "", "inclure": "", "exclure": "", "cas_mixtes": "",
                "precisions": "", "modifie_le": ""}
    MM.ecrire(scr / "criteres_domaine.json", json.dumps(criteres, ensure_ascii=False, indent=2) + "\n")
    entete = [l for l in (RACINE / "input" / "Scraping" / "liens.txt").read_text(encoding="utf-8").splitlines()
              if l.startswith("#") and "keywords=" not in l][:14]
    (scr / "liens.txt").write_text("\n".join(entete) + "\n\n# (tes liens de recherche : FORMULAIRE.bat, section 10)\n",
                                   encoding="utf-8")
    (scr / ".env").write_text("# Clés Apify (FORMULAIRE.bat, section 7)\nAPIFY_TOKEN=\n", encoding="utf-8")
    (scr / "logs").mkdir(exist_ok=True)
    # .env du projet : le modèle, sans aucune valeur
    exemple = RACINE / ".env.example"
    (dest / ".env").write_text(re.sub(r"(?m)^([A-Z0-9_]+)=.*$", r"\1=", exemple.read_text(encoding="utf-8"))
                               if exemple.exists() else "MOT_DE_PASSE_SITES=\n", encoding="utf-8")
    # Clé NopeCHA retirée de l'extension
    man = dest / "extensions" / "nopecha" / "manifest.json"
    if man.exists():
        d = json.loads(man.read_text(encoding="utf-8"))
        d.setdefault("nopecha", {})["key"] = ""
        MM.ecrire(man, json.dumps(d, ensure_ascii=False, indent=2))
    # Mémoire des sites (utile à tous) : gardée ; état personnel : vidé
    (dest / "memoire").mkdir(exist_ok=True)
    (dest / "memoire" / "etat.json").write_text("{}\n", encoding="utf-8")
    # Dossiers de travail vides + Excel neuf
    for d in ("output/Candidatures", "logs", "rapports", "input/sauvegardes"):
        (dest / d).mkdir(parents=True, exist_ok=True)
    from excel_manager import creer_modele
    creer_modele(dest / "input" / "entreprises.xlsx", avec_exemple=False)


def nettoyer_memoire(dest: Path, mots: list[str]) -> int:
    """Les leçons des sites servent à tous ; on en retire seulement les données du candidat."""
    n = 0
    for f in (dest / "memoire").glob("*.json"):
        texte = f.read_text(encoding="utf-8")
        for m in mots:
            motif = re.compile(re.escape(m), re.IGNORECASE)
            n += len(motif.findall(texte))
            texte = motif.sub("<donnée du candidat>", texte)
        f.write_text(texte, encoding="utf-8")
    return n


def fouiller(dest: Path, cherche: dict[str, list[str]]) -> list[str]:
    restes = []
    for f in dest.rglob("*"):
        if not f.is_file() or f.suffix.lower() in (".xlsx", ".png", ".pdf", ".exe", ".dll", ".pyd", ".zip"):
            continue
        try:
            texte = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for genre, mots in cherche.items():
            for m in mots:
                if m and m.lower() in texte.lower():
                    for i, l in enumerate(texte.splitlines(), start=1):
                        if m.lower() in l.lower():
                            montre = "•••" if genre == "secret" else m
                            restes.append(f"{f.relative_to(dest)}:{i} — {genre} « {montre} »")
                            break
    return restes


def main() -> int:
    horodatage = datetime.now().strftime("%Y-%m-%d_%Hh%M")
    dest = RACINE.parent / f"{RACINE.name}_VIERGE_{horodatage}"
    print("=" * 72)
    print("  EXPORT D'UNE COPIE VIERGE DU PROJET (pour une autre personne)")
    print("=" * 72)
    print(f"  Ton dossier n'est PAS modifié. Copie créée : {dest}")
    cherche = secrets_et_identite()
    n = copier(dest)
    ecrire_fichiers_vides(dest)
    retires = nettoyer_memoire(dest, cherche["identité"])
    print(f"  {n} fichiers copiés ; fichiers personnels remplacés par des modèles vides ;"
          f" {retires} donnée(s) personnelle(s) retirée(s) de la mémoire des sites.")
    restes = fouiller(dest, cherche)
    if restes:
        print(f"\n  ⚠ {len(restes)} trace(s) du candidat actuel restent dans la copie :")
        for r in restes[:40]:
            print("     " + r)
    else:
        print("\n  ✓ Fouille terminée : aucune donnée personnelle ni aucun secret du candidat actuel dans la copie.")
    archive = dest.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for f in dest.rglob("*"):
            z.write(f, Path(dest.name) / f.relative_to(dest))
    print(f"  ✓ Archive à donner : {archive} ({archive.stat().st_size / 1e6:.1f} Mo)")
    print("\n  Sur l'autre machine : extraire, lancer Click_here_to_install_all_requirements.bat, puis FORMULAIRE.bat.")
    return 1 if restes else 0


if __name__ == "__main__":
    sys.exit(main())
