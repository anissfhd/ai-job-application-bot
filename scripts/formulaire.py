"""
FORMULAIRE.bat — installe le projet pour UN candidat : toutes ses informations, à la structure
EXACTE des fichiers d'origine (config/modeles/*.modele.*), sans jamais toucher au code.

Sections (toutes obligatoires ; on peut s'arrêter et reprendre, ou refaire une seule section) :
   1. Identité              2. Liens                  3. Formation
   4. Objectif de recherche (stage PFE / PFA, alternance, emploi…)
   5. Langues               6. Réponses types des formulaires (visa, autorisation de travail…)
   7. Mot de passe des sites et clés (.env)
   8. Navigateurs : connexions LinkedIn / Gmail / DeepSeek, vérifiées automatiquement
   9. CV, lettre de motivation et photo -> gabarit du robot (DeepSeek), avec aperçu à valider
  10. Domaine recherché et liens de recherche LinkedIn (scraping)
  11. Vérification finale

Chaque réponse est contrôlée (email, téléphone, date, lien, Oui/Non…). Avant tout
remplacement, les fichiers existants sont sauvegardés dans config/sauvegardes_formulaire/.
"""
from __future__ import annotations

import getpass
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "scripts"))

import yaml  # noqa: E402

import formulaire_modeles as M  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

PROGRESSION = RACINE / "config" / "formulaire_reponses.json"
SAUVEGARDES = RACINE / "config" / "sauvegardes_formulaire"
PROFIL = RACINE / "config" / "profile.yaml"
OBJECTIF = RACINE / "config" / "objectif.yaml"
ENV = RACINE / ".env"
ENV_SCRAPING = RACINE / "input" / "Scraping" / ".env"
LIENS = RACINE / "input" / "Scraping" / "liens.txt"
NOPECHA = RACINE / "extensions" / "nopecha" / "manifest.json"
CONFIG = RACINE / "config" / "config.yaml"
PY = sys.executable

MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
           "octobre", "novembre", "décembre"]
MOIS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]
NIVEAUX = ["Native or bilingual", "Full professional", "Professional", "Limited working", "Elementary", ""]


# ----------------------------------------------------------------------------------------------
# Saisie contrôlée
# ----------------------------------------------------------------------------------------------

class Q:
    """Une question : où va la réponse, comment la contrôler."""

    def __init__(self, cle: str, texte: str, exemple: str = "", genre: str = "texte",
                 choix: list[str] | None = None, optionnel: bool = False, si=None):
        self.cle, self.texte, self.exemple, self.genre = cle, texte, exemple, genre
        self.choix, self.optionnel, self.si = choix or [], optionnel, si


def valider(q: Q, brut: str):
    """(valeur, erreur) — erreur vide si la réponse est acceptée."""
    s = brut.strip()
    if not s:
        return ([] if q.genre == "liste" else ""), ("" if q.optionnel else "réponse obligatoire")
    if q.genre == "email":
        ok = re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}", s)
        return s, "" if ok else "email invalide (ex. prenom.nom@gmail.com)"
    if q.genre == "tel":
        ok = re.fullmatch(r"\+?[\d\s.\-()]{6,20}", s)
        return s, "" if ok else "numéro invalide (chiffres, espaces et + seulement)"
    if q.genre == "date":
        try:
            datetime.strptime(s, "%d/%m/%Y")
            return s, ""
        except ValueError:
            return s, "date invalide (format JJ/MM/AAAA, ex. 01/03/2027)"
    if q.genre == "url":
        return s, "" if re.match(r"https?://\S+\.\S+", s) else "lien invalide (doit commencer par https://)"
    if q.genre == "oui_non":
        m = s.lower()
        if m in ("o", "oui", "y", "yes"):
            return "Oui", ""
        if m in ("n", "non", "no"):
            return "Non", ""
        return s, "réponds Oui ou Non"
    if q.genre == "choix":
        for c in q.choix:
            if s.lower() == c.lower():
                return c, ""
        if s.isdigit() and 1 <= int(s) <= len(q.choix):
            return q.choix[int(s) - 1], ""
        return s, "choisis dans la liste (numéro ou texte exact)"
    if q.genre == "liste":
        return [x.strip() for x in s.split(";") if x.strip()], ""
    return s, ""


def affiche(v) -> str:
    return "; ".join(v) if isinstance(v, list) else str(v)


def demander(q: Q, actuel=None):
    print(f"\n  {q.texte}")
    if q.exemple:
        print(f"     ex. : {q.exemple}")
    if q.choix:
        for i, c in enumerate(q.choix, start=1):
            print(f"     {i}. {c or '(aucun)'}")
    if q.genre == "liste":
        print("     (plusieurs valeurs : sépare-les par des « ; »)")
    if actuel not in (None, "", []):
        print(f"     Actuel : {affiche(actuel)}   (Entrée = garder)")
    elif q.optionnel:
        print("     (facultatif : Entrée = aucun)")
    while True:
        brut = input("     > ")
        if not brut.strip() and actuel not in (None, "", []):
            return actuel
        valeur, erreur = valider(q, brut)
        if not erreur:
            return valeur
        print(f"     ✗ {erreur}")


# ----------------------------------------------------------------------------------------------
# Questions (même structure que config/profile.yaml et config/objectif.yaml)
# ----------------------------------------------------------------------------------------------

def est_stage(r: dict) -> bool:
    return r.get("o.type") == "stage"


SECTIONS: dict[str, tuple[str, list[Q]]] = {
    "identite": ("Identité", [
        Q("p.identite.civilite", "Civilité", genre="choix", choix=["M.", "Mme", ""]),
        Q("p.identite.genre", "Genre (certains formulaires le demandent)", genre="choix", choix=["Homme", "Femme"]),
        Q("p.identite.prenom", "Prénom", "Sarah"),
        Q("p.identite.nom", "Nom de famille", "Martin"),
        Q("p.identite.email", "Email de candidature (celui du compte Google connecté dans les navigateurs)",
          "prenom.nom@gmail.com", "email"),
        Q("p.identite.telephone", "Téléphone complet, avec l'indicatif du pays", "+33 6 12 34 56 78", "tel"),
        Q("p.identite.adresse", "Adresse (quartier / rue)", "12 rue de la République, Lyon"),
        Q("p.identite.code_postal", "Code postal", "69002"),
        Q("p.identite.ville", "Ville", "Lyon"),
        Q("p.identite.pays", "Pays de résidence", "France"),
        Q("p.identite.nationalite", "Nationalité", "Française"),
        Q("p.identite.date_naissance", "Date de naissance (JJ/MM/AAAA)", "14/05/2004", "date", optionnel=True),
    ]),
    "liens": ("Liens", [
        Q("p.liens.linkedin", "Profil LinkedIn", "https://www.linkedin.com/in/prenom-nom/", "url"),
        Q("p.liens.github", "GitHub", "https://www.github.com/pseudo/", "url", optionnel=True),
        Q("p.liens.portfolio", "Portfolio / site personnel", "https://monsite.fr", "url", optionnel=True),
    ]),
    "formation": ("Formation", [
        Q("p.formation.ecole", "École / université (avec la ville)", "INSA Lyon (Institut National des Sciences Appliquées), Lyon"),
        Q("p.formation.diplome", "Diplôme préparé ou obtenu", "Diplôme d'ingénieur / Master 2"),
        Q("p.formation.specialite", "Spécialité", "Informatique et Intelligence Artificielle"),
        Q("p.formation.annee_en_cours", "Année en cours (ou « Diplômé »)", "Dernière année du cycle ingénieur"),
        Q("p.formation.date_obtention_prevue", "Année d'obtention (prévue ou réelle)", "2027"),
    ]),
    "objectif": ("Objectif de recherche", [
        Q("o.type", "Que cherches-tu ?", genre="choix", choix=["stage", "alternance", "emploi"]),
        Q("o.nom_fr", "Ce que tu cherches, en quelques mots, en minuscules",
          "stage de fin d'études / stage de fin d'année / emploi de développeur / alternance en data"),
        Q("o.nom_en", "La même chose en anglais", "end-of-studies internship / software developer position"),
        Q("o.sigle", "Sigle, s'il y en a un", "PFE, PFA", optionnel=True),
        Q("o.contrats_acceptes", "Types de contrat acceptés (stage, alternance, cdi, cdd, freelance)",
          "stage   ou   cdi; cdd", "liste"),
        Q("o.duree_fr", "Durée recherchée (vide s'il n'y en a pas, ex. CDI)", "6 mois", optionnel=True),
        Q("o.date_debut", "Date de début souhaitée (JJ/MM/AAAA ; vide = dès que possible)", "01/03/2027",
          "date", optionnel=True),
        Q("o.conventionne", "Stage sous convention de ton école ?", genre="oui_non", si=est_stage),
        Q("o.statut_fr", "Ta situation, en 2-3 mots (minuscules)", "élève ingénieur / étudiant en master / jeune diplômé"),
        Q("o.statut_long_fr", "Ta situation, un peu plus précise", "élève-ingénieur en dernière année"),
        Q("o.presentation_fr", "Qui tu es, en une demi-phrase (utilisée dans l'email de candidature)",
          "Élève-ingénieure en informatique à l'INSA Lyon"),
        Q("p.stage.pays_cibles", "Pays où tu cherches", "France", "liste"),
        Q("p.stage.mobilite", "Ta mobilité", "Mobilité internationale, disponible pour s'installer en France pendant 6 mois"),
        Q("p.stage.convention_de_stage", "Convention de stage (phrase pour les formulaires)",
          "Oui, stage conventionné (convention fournie par l'école)", si=est_stage),
    ]),
    "langues": ("Langues", [
        Q("p.langues", "Tes langues et niveaux", "Français : langue maternelle; Anglais : courant; Espagnol : intermédiaire", "liste"),
        Q("p.reponses_standard.niveau_anglais", "Niveau d'ANGLAIS pour les menus des formulaires", genre="choix", choix=NIVEAUX),
        Q("p.reponses_standard.niveau_francais", "Niveau de FRANÇAIS pour les menus des formulaires", genre="choix", choix=NIVEAUX),
        Q("p.reponses_standard.niveau_arabe", "Niveau d'ARABE pour les menus des formulaires ((aucun) si tu ne le parles pas)",
          genre="choix", choix=NIVEAUX),
    ]),
    "reponses": ("Réponses types des formulaires", [
        Q("p.reponses_standard.autorisation_travail", "« Êtes-vous autorisé(e) à travailler en France ? » — réponse complète",
          "Oui, je suis citoyen(ne) français(e).   ou   Non, stage conventionné avec visa « stagiaire »…"),
        Q("p.reponses_standard.autorisation_travail_oui_non", "Même question, réponse courte", genre="oui_non"),
        Q("p.reponses_standard.autorisation_travail_france_ou_ue_oui_non",
          "« Autorisé(e) à travailler en France / dans l'UE / en Europe ? »", genre="oui_non"),
        Q("p.reponses_standard.autorisation_travail_maroc_oui_non",
          "« Autorisé(e) à travailler dans ton pays de résidence (ci-dessus) ? »", genre="oui_non"),
        Q("p.reponses_standard.besoin_visa", "« Aurez-vous besoin d'un visa / d'un parrainage ? » — réponse complète",
          "Non, aucun visa n'est nécessaire.   ou   Oui : visa long séjour « stagiaire »…"),
        Q("p.reponses_standard.besoin_visa_oui_non", "Même question, réponse courte", genre="oui_non"),
        Q("p.reponses_standard.telephone_sans_indicatif", "Téléphone SANS indicatif, tel que les formulaires l'attendent",
          "612345678   (sans le 0 si les formulaires ajoutent l'indicatif à part)", "tel"),
        Q("p.reponses_standard.telephone_avec_indicatif", "Téléphone AVEC indicatif, sans espaces", "+33612345678", "tel"),
        Q("p.reponses_standard.gratification_souhaitee", "Rémunération / gratification souhaitée", "Selon la grille de l'entreprise"),
        Q("p.reponses_standard.gratification_souhaitee_montant",
          "La même en CHIFFRES : montant mensuel brut en euros (pour les champs qui n'acceptent qu'un nombre)",
          "700", optionnel=True),
        Q("p.reponses_standard.comment_avez_vous_connu", "« Comment avez-vous connu l'offre ? »", "Site carrière de l'entreprise"),
        Q("p.reponses_standard.consentement_recontact", "Accepter d'être recontacté(e) pour d'autres offres ? (phrase)",
          "Oui, j'accepte d'être recontacté(e) pour de futures opportunités"),
        Q("p.reponses_standard.handicap", "Question sur le handicap (phrase)", "Je ne souhaite pas répondre"),
        Q("p.reponses_standard.permis_conduire", "Permis de conduire", "Permis B", optionnel=True),
    ]),
}
ORDRE = ["identite", "liens", "formation", "objectif", "langues", "reponses",
         "secrets", "navigateurs", "documents", "domaine", "verification"]
# Une section ne se lance qu'une fois ses prérequis faits (ex. les documents ont besoin de DeepSeek).
PREREQUIS = {
    "navigateurs": ["identite"],                                            # email de candidature
    "documents": ["identite", "liens", "formation", "objectif", "langues", "reponses", "navigateurs"],
    "verification": ["identite", "liens", "formation", "objectif", "langues", "reponses", "secrets",
                     "navigateurs", "documents", "domaine"],
}
TITRES = {"secrets": "Mot de passe des sites et clés", "navigateurs": "Navigateurs et connexions",
          "documents": "CV, lettre de motivation et photo", "domaine": "Domaine recherché et liens de recherche",
          "verification": "Vérification finale"}


# ----------------------------------------------------------------------------------------------
# Progression
# ----------------------------------------------------------------------------------------------

def charger() -> dict:
    try:
        return json.loads(PROGRESSION.read_text(encoding="utf-8")) if PROGRESSION.exists() else {}
    except ValueError:
        return {}


def sauver(r: dict) -> None:
    M.ecrire(PROGRESSION, json.dumps(r, ensure_ascii=False, indent=1))


def faites(r: dict) -> list[str]:
    return list(r.get("_sections_faites", []))


def marquer(r: dict, section: str) -> None:
    f = faites(r)
    if section not in f:
        f.append(section)
    r["_sections_faites"] = f
    sauver(r)


# ----------------------------------------------------------------------------------------------
# Écriture des fichiers (structure exacte des modèles)
# ----------------------------------------------------------------------------------------------

def sauvegarder_existants(fichiers: list[Path]) -> Path | None:
    """Copie des fichiers AVANT remplacement (jamais d'écrasement sans sauvegarde)."""
    presents = [f for f in fichiers if Path(f).exists()]
    if not presents:
        return None
    dossier = SAUVEGARDES / datetime.now().strftime("%Y-%m-%d_%Hh%M%S")
    dossier.mkdir(parents=True, exist_ok=True)
    for f in presents:
        f = Path(f)
        dest = dossier / f.relative_to(RACINE)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if f.is_dir():
            shutil.copytree(f, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(f, dest)
    return dossier


def dates(jjmmaaaa: str) -> tuple[str, str, str]:
    if not jjmmaaaa:
        return "", "", ""
    d = datetime.strptime(jjmmaaaa, "%d/%m/%Y")
    jour = "1er" if d.day == 1 else str(d.day)
    return (f"{jour} {MOIS_FR[d.month - 1]} {d.year}", f"{MOIS_EN[d.month - 1]} {d.day}, {d.year}",
            f"{MOIS_FR[d.month - 1]} {d.year}")


def durees_en(duree_fr: str) -> tuple[str, str] | None:
    """« 6 mois » -> (« 6 months », « 6-month ») ; None si la forme n'est pas reconnue."""
    m = re.fullmatch(r"\s*(\d+)\s*(mois|ans?|années?|semaines?)\s*", duree_fr or "")
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    unite = "month" if u == "mois" else ("week" if u.startswith("semaine") else "year")
    return f"{n} {unite}{'s' if n > 1 else ''}", f"{n}-{unite}"


def valeurs_objectif(r: dict) -> dict:
    fr, en, mois = dates(r.get("o.date_debut", ""))
    duree = r.get("o.duree_fr", "")
    d_en = durees_en(duree) if duree else ("", "")
    if d_en is None:
        d_en = (r.get("o.duree_en", ""), r.get("o.duree_adjectif_en", ""))
    return {
        "type": r["o.type"], "contrats_acceptes": [c.lower() for c in r["o.contrats_acceptes"]],
        "nom_fr": r["o.nom_fr"], "nom_en": r["o.nom_en"], "sigle": r.get("o.sigle", ""),
        "duree_fr": duree, "duree_en": d_en[0], "duree_adjectif_en": d_en[1],
        "date_debut_fr": fr, "date_debut_en": en, "mois_debut_fr": mois,
        "conventionne": r.get("o.conventionne") == "Oui",
        "statut_fr": r["o.statut_fr"], "statut_long_fr": r["o.statut_long_fr"],
        "presentation_fr": r["o.presentation_fr"],
    }


def valeurs_profil(r: dict) -> dict:
    modele = M.lire_modele("profile.modele.yaml")
    v = {c: r.get("p." + c, "") for c in M.reperes(modele)}
    o = valeurs_objectif(r)
    type_mot = o["type"] if o["type"] in ("stage", "alternance") else o["nom_fr"]
    v["stage.type"] = (type_mot[:1].upper() + type_mot[1:] + (f" {o['sigle']}" if o["sigle"] else "")).strip()
    v["stage.duree"] = o["duree_fr"]
    v["stage.date_debut_souhaitee"] = o["date_debut_fr"] or "Dès que possible"
    v["stage.convention_de_stage"] = r.get("p.stage.convention_de_stage", "") if o["type"] == "stage" else ""
    for liste in ("langues", "stage.pays_cibles"):
        v[liste] = list(r.get("p." + liste) or [])
    return v


def ecrire_profil_et_objectif(r: dict) -> None:
    dossier = sauvegarder_existants([PROFIL, OBJECTIF])
    profil = M.remplir(M.lire_modele("profile.modele.yaml"), valeurs_profil(r))
    objectif = M.remplir(M.lire_modele("objectif.modele.yaml"), valeurs_objectif(r))
    M.ecrire(PROFIL, profil)
    M.ecrire(OBJECTIF, objectif)
    # Le filtre de confidentialité et l'objectif relisent les NOUVELLES valeurs
    import confidentialite
    import objectif as module_objectif
    confidentialite.courante.cache_clear()
    module_objectif.courant.cache_clear()
    # Contrôle : même structure que le modèle (mêmes clés, même ordre)
    vide = yaml.safe_load(M.remplir(M.lire_modele("profile.modele.yaml"),
                                    M.valeurs_vides(M.lire_modele("profile.modele.yaml"), ("langues", "stage.pays_cibles"))))
    assert list(yaml.safe_load(profil)) == list(vide), "structure du profil différente du modèle"
    print("\n  ✓ config/profile.yaml et config/objectif.yaml écrits (structure identique au modèle)."
          + (f"\n    Anciennes versions sauvegardées dans {dossier.relative_to(RACINE)}" if dossier else ""))


def maj_env(fichier: Path, valeurs: dict) -> None:
    """Met à jour / ajoute des lignes NOM=valeur, sans toucher aux autres."""
    lignes = fichier.read_text(encoding="utf-8").splitlines() if fichier.exists() else []
    vus = set()
    for i, l in enumerate(lignes):
        nom = l.split("=", 1)[0].strip()
        if nom in valeurs:
            lignes[i] = f"{nom}={valeurs[nom]}"
            vus.add(nom)
    lignes += [f"{n}={v}" for n, v in valeurs.items() if n not in vus]
    M.ecrire(fichier, "\n".join(lignes) + "\n")


def maj_garde_fous_cv(chaines: list[str]) -> None:
    """config.yaml -> garde_fous.chaines_obligatoires_cv (seul ce bloc est réécrit)."""
    texte = CONFIG.read_text(encoding="utf-8")
    bloc = re.compile(r"(  chaines_obligatoires_cv:\n)((?:    - .*\n)*)")
    nouveau = "".join(f"    - {json.dumps(c, ensure_ascii=False)}\n" for c in chaines)
    if chaines:
        texte = bloc.sub(lambda m: m.group(1) + nouveau, texte, count=1)
    else:
        texte = bloc.sub("  chaines_obligatoires_cv: []\n", texte, count=1)
    M.ecrire(CONFIG, texte)


# ----------------------------------------------------------------------------------------------
# Sections
# ----------------------------------------------------------------------------------------------

def section_questions(r: dict, nom: str) -> None:
    titre, questions = SECTIONS[nom]
    for q in questions:
        if q.si and not q.si(r):
            r.pop(q.cle, None)
            continue
        r[q.cle] = demander(q, r.get(q.cle))
        if q.cle == "o.duree_fr" and r[q.cle] and durees_en(r[q.cle]) is None:
            r["o.duree_en"] = demander(Q("o.duree_en", "Cette durée en anglais", "6 months"), r.get("o.duree_en"))
            r["o.duree_adjectif_en"] = demander(Q("o.duree_adjectif_en", "En anglais, comme adjectif", "6-month"),
                                                r.get("o.duree_adjectif_en"))
        if q.cle == "o.type" and not r.get("o.contrats_acceptes"):
            r["o.contrats_acceptes"] = {"stage": ["stage"], "alternance": ["alternance"]}.get(r[q.cle], ["cdi", "cdd"])
        sauver(r)


def generer_mot_de_passe(longueur: int, speciaux: str) -> str:
    """
    Mot de passe de secours, conforme aux politiques les plus strictes des sites carrière :
    au moins 2 majuscules, 2 minuscules, 2 chiffres, 2 caractères spéciaux (pris dans
    `speciaux`), sans caractère ambigu (0/O, 1/l/I), sans deux caractères identiques de suite
    ni suite de trois (abc, 123).
    """
    import secrets
    maj, minu, chif = "ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnpqrstuvwxyz", "23456789"
    tous = maj + minu + chif + speciaux
    while True:
        mdp = [secrets.choice(maj), secrets.choice(maj), secrets.choice(minu), secrets.choice(minu),
               secrets.choice(chif), secrets.choice(chif), secrets.choice(speciaux), secrets.choice(speciaux)]
        mdp += [secrets.choice(tous) for _ in range(longueur - len(mdp))]
        secrets.SystemRandom().shuffle(mdp)
        texte = "".join(mdp)
        if any(a == b for a, b in zip(texte, texte[1:])):
            continue
        if any(ord(b) - ord(a) == 1 and ord(c) - ord(b) == 1 for a, b, c in zip(texte, texte[1:], texte[2:])):
            continue
        if not texte[0].isalpha():          # certains sites refusent un 1er caractère spécial
            continue
        return texte


def mots_de_passe_de_secours() -> list[tuple[str, str]]:
    """
    Crée dans .env les mots de passe n° 2 et n° 3 s'ils manquent (utilisés quand un site refuse
    le précédent : trop court, pas assez complexe…). Renvoie [(clé, valeur)] des nouveaux.
    """
    existants = {}
    if ENV.exists():
        for l in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in l and not l.lstrip().startswith("#"):
                nom, val = l.split("=", 1)
                existants[nom.strip()] = val.strip().strip("'\"")
    nouveaux = []
    for cle, longueur, speciaux in (("MOT_DE_PASSE_SITES_2", 16, "!?"), ("MOT_DE_PASSE_SITES_3", 20, "@$")):
        if not existants.get(cle):
            nouveaux.append((cle, generer_mot_de_passe(longueur, speciaux)))
    if nouveaux:
        # Entre apostrophes : « $ » et « ! » y restent littéraux (python-dotenv n'y interprète rien).
        maj_env(ENV, {cle: f"'{valeur}'" for cle, valeur in nouveaux})
    return nouveaux


def section_secrets(r: dict) -> None:
    print("\n  Le robot crée et ouvre tes comptes candidat sur les sites des entreprises avec UN seul mot")
    print("  de passe (jamais affiché dans les logs ; DeepSeek ne voit que {MOT_DE_PASSE}).")
    print("  Choisis-en un solide (8 caractères ou plus, lettres + chiffres), différent de celui de ton email.")
    while True:
        mdp = getpass.getpass("     Mot de passe des sites (masqué) > ")
        if len(mdp) < 8:
            print("     ✗ 8 caractères minimum")
            continue
        if getpass.getpass("     Confirme-le (masqué) > ") != mdp:
            print("     ✗ les deux saisies ne correspondent pas")
            continue
        break
    maj_env(ENV, {"MOT_DE_PASSE_SITES": mdp})
    print("  ✓ Mot de passe enregistré dans .env")
    # Certains sites refusent un mot de passe trop court ou trop simple (ex. 15
    # caractères). Le robot essaie alors une 2e, puis une 3e version, créées ici une fois.
    nouveaux = mots_de_passe_de_secours()
    if nouveaux:
        print("\n  Mots de passe de SECOURS (si un site refuse le tien), créés et enregistrés dans .env.")
        print("  Note-les : ce sont ceux de tes comptes candidat sur les sites qui ont refusé le 1er.")
        for cle, valeur in nouveaux:
            print(f"     {'2e' if cle.endswith('_2') else '3e'} mot de passe : {valeur}")

    print("\n  Clés Apify pour le scraping automatique des offres (https://console.apify.com -> Settings -> API).")
    print("  Tu peux en donner jusqu'à 10 (une par compte) : si l'une n'a plus de crédit, la suivante prend le relais.")
    cles = demander(Q("apify", "Clés Apify", "apify_api_xxxxxxxx; apify_api_yyyyyyyy", "liste", optionnel=True))
    if cles:
        noms = ["APIFY_TOKEN"] + [f"APIFY_TOKEN_{i}" for i in range(2, 11)]
        maj_env(ENV_SCRAPING, {n: c for n, c in zip(noms, cles[:10])})
        print(f"  ✓ {min(len(cles), 10)} clé(s) Apify enregistrée(s) dans input/Scraping/.env")

    cle_nopecha = demander(Q("nopecha", "Clé NopeCHA (résolution des CAPTCHA ; https://nopecha.com)", "sub_xxxxxxxx",
                             optionnel=True))
    if cle_nopecha and NOPECHA.exists():
        manifest = json.loads(NOPECHA.read_text(encoding="utf-8"))
        manifest.setdefault("nopecha", {})["key"] = cle_nopecha
        M.ecrire(NOPECHA, json.dumps(manifest, ensure_ascii=False, indent=2))
        print("  ✓ Clé NopeCHA enregistrée dans l'extension")


def section_domaine(r: dict) -> None:
    print("\n  Le tri automatique des offres scrapées (robot verify_eligibility) a besoin de ton domaine.")
    spec = __import__("importlib.util").util.spec_from_file_location(
        "parametrer_domaine", RACINE / "input" / "Scraping" / "parametrer_domaine.py")
    module = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.main() != 0:
        raise RuntimeError("réglage du domaine non enregistré")

    print("\n  LIENS DE RECHERCHE LinkedIn (le scraping les surveille chaque jour).")
    print("  Colle un lien https://www.linkedin.com/jobs/search/?… par ligne, OU écris « mots-clés | lieu »")
    print("  (ex. « stage data science | France ») : le lien est construit pour toi. Ligne vide = terminé.")
    from urllib.parse import urlencode
    liens = []
    while True:
        s = input("     > ").strip()
        if not s:
            if liens:
                break
            print("     ✗ au moins un lien ou une recherche")
            continue
        if s.startswith("http"):
            if "linkedin.com/jobs/search" not in s:
                print("     ✗ un lien de recherche LinkedIn (…/jobs/search/?…) est attendu")
                continue
            liens.append(("", s))
        elif "|" in s:
            mots, lieu = (x.strip() for x in s.split("|", 1))
            liens.append((f"{mots} — {lieu}",
                          "https://www.linkedin.com/jobs/search/?" + urlencode({"keywords": mots, "location": lieu})))
        else:
            print("     ✗ format : un lien, ou « mots-clés | lieu »")
    sauvegarder_existants([LIENS])
    entete = [l for l in (LIENS.read_text(encoding="utf-8").splitlines() if LIENS.exists() else [])
              if l.startswith("#") and "keywords=" not in l][:14]
    corps = []
    for titre, url in liens:
        corps += ([f"# {titre}"] if titre else []) + [url, ""]
    M.ecrire(LIENS, "\n".join(entete + [""] + corps))
    print(f"  ✓ {len(liens)} recherche(s) enregistrée(s) dans input/Scraping/liens.txt")


def section_verification(r: dict) -> None:
    from utils import charger_config, chemin_projet
    config, _ = charger_config()
    excel = chemin_projet(config["chemins"]["excel"])
    if not excel.exists():
        subprocess.run([PY, str(RACINE / "scripts" / "excel_manager.py"), "--init"], cwd=RACINE)
    print("\n  Vérification du robot (configuration, profil, CV) :")
    code = subprocess.run([PY, str(RACINE / "scripts" / "orchestrator.py"), "--verifier"], cwd=RACINE).returncode
    if code != 0:
        raise RuntimeError("la vérification du robot signale un problème (voir ci-dessus)")


def lancer_section(r: dict, nom: str) -> None:
    titre = SECTIONS[nom][0] if nom in SECTIONS else TITRES[nom]
    print("\n" + "=" * 72 + f"\n  {ORDRE.index(nom) + 1}. {titre.upper()}\n" + "=" * 72)
    if nom in SECTIONS:
        section_questions(r, nom)
        marquer(r, nom)
        if all(s in faites(r) for s in ORDRE[:6]):      # profil réécrit à chaque section refaite
            ecrire_profil_et_objectif(r)
            maj_garde_fous_cv([])
    elif nom == "secrets":
        section_secrets(r)
    elif nom == "navigateurs":
        import formulaire_navigateurs
        formulaire_navigateurs.section(r)
    elif nom == "documents":
        import formulaire_documents
        formulaire_documents.section(r)
    elif nom == "domaine":
        section_domaine(r)
    elif nom == "verification":
        section_verification(r)
    marquer(r, nom)
    print(f"\n  ✓ Section « {titre} » terminée.")


# ----------------------------------------------------------------------------------------------
# Programme
# ----------------------------------------------------------------------------------------------

def protection(r: dict) -> bool:
    """Un profil rempli existe déjà et ce n'est pas une reprise : confirmation explicite."""
    if faites(r) or not PROFIL.exists():
        return True
    try:
        p = yaml.safe_load(PROFIL.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return True
    ident = p.get("identite") or {}
    nom = f"{ident.get('prenom', '')} {ident.get('nom', '')}".strip()
    if not nom:
        return True
    print(f"\n  ⚠ Ce projet contient DÉJÀ le profil de : {nom}.")
    print("    Le formulaire va REMPLACER ses informations (une sauvegarde sera faite avant).")
    print("    Pour une autre personne, utilise plutôt une copie vierge (EXPORTER_PROJET_VIERGE.bat).")
    return input("    Tape REMPLACER pour continuer, ou Entrée pour quitter : ").strip() == "REMPLACER"


def main() -> int:
    r = charger()
    print("=" * 72)
    print("  FORMULAIRE — installer le robot de candidatures pour UN candidat")
    print("=" * 72)
    if not protection(r):
        print("  Rien n'a été modifié.")
        return 0
    print("\n  Toutes les sections sont obligatoires. Tes réponses sont gardées au fur et à mesure :")
    print("  tu peux t'arrêter (Ctrl+C) et reprendre plus tard là où tu en étais.")
    while True:
        f = faites(r)
        print("\n  " + "-" * 60)
        for i, nom in enumerate(ORDRE, start=1):
            titre = SECTIONS[nom][0] if nom in SECTIONS else TITRES[nom]
            print(f"   {'✓' if nom in f else ' '} {i:>2}. {titre}")
        suivante = next((n for n in ORDRE if n not in f), None)
        print("\n   Entrée = " + (f"continuer ({ORDRE.index(suivante) + 1}. {SECTIONS[suivante][0] if suivante in SECTIONS else TITRES[suivante]})"
                                  if suivante else "quitter (tout est fait)"))
        print("   un numéro = refaire cette section    Q = quitter")
        choix = input("   > ").strip().lower()
        if choix == "q" or (not choix and not suivante):
            break
        nom = suivante if not choix else (ORDRE[int(choix) - 1] if choix.isdigit() and 1 <= int(choix) <= len(ORDRE) else None)
        if nom is None:
            continue
        manquantes = [n for n in PREREQUIS.get(nom, []) if n not in f]
        if manquantes:
            print("   ✗ termine d'abord : " + ", ".join(
                f"{ORDRE.index(n) + 1}. {SECTIONS[n][0] if n in SECTIONS else TITRES[n]}" for n in manquantes))
            continue
        try:
            lancer_section(r, nom)
        except KeyboardInterrupt:
            print("\n  Interrompu : tes réponses déjà données sont gardées.")
            return 1
        except Exception as e:  # noqa: BLE001
            print(f"\n  ✗ Section non terminée : {e}")
    if len(faites(r)) == len(ORDRE):
        print("\n  ✓ Installation terminée. Lance LANCER.bat -> 4 pour démarrer les candidatures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
