"""
FORMULAIRE — section « CV, lettre de motivation et photo » : transforme les documents du
candidat en gabarit du robot (config/cv_gabarit/), à la même structure que celui d'origine.

Principe de fiabilité : DeepSeek ne RÉÉCRIT jamais le document. Il se contente d'y poser des
marques <<EMPLACEMENT|texte d'origine>>. Le programme vérifie qu'une fois les marques retirées,
le LaTeX est IDENTIQUE à l'original (aucun mot changé, ajouté ou perdu), puis il en tire :
    gabarit.tex (CV à trous), reference_en.json (textes variables d'origine = longueurs cibles),
    fixe_fr.json / fixe_en.json (textes fixes et leur traduction),
    lettre.tex (lettre à trous), lettre_fixe.json (textes fixes FR/EN + longueurs des 3 paragraphes),
    et les 3 messages privés envoyés à DeepSeek (config/deepseek/nouveaux/).
Un CV / une lettre en PDF est d'abord recréé en LaTeX par DeepSeek (aperçu à valider).
Tout se termine par un APERÇU PDF que le candidat valide.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from utils import charger_config

RACINE = Path(__file__).resolve().parent.parent
GABARIT = RACINE / "config" / "cv_gabarit"
MESSAGES = RACINE / "config" / "deepseek" / "nouveaux"
ENTREE = RACINE / "input"
MARQUE = re.compile(r"<<([A-Z][A-Z0-9_]*)\|(.*?)>>", re.DOTALL)
VARIABLE = re.compile(r"^(PROFIL|E\d+_(TITRE|P\d*)|P\d+_(TITRE|TECH|P)|SK\d+(_LABEL)?)$")
CLES_LETTRE = ["L_TITRE_DOC", "L_SERVICE", "L_DATE", "L_OBJET", "L_SALUTATION", "L_FORMULE", "L_VILLE_SIGNATURE"]
MOIS = {"fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
               "novembre", "décembre"],
        "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December"]}

CONVENTION_CV = r"""
Recopie mon CV LaTeX CI-DESSOUS À L'IDENTIQUE, caractère pour caractère, en ajoutant SEULEMENT des
marques autour de certains textes : <<NOM|texte d'origine>>. Ne corrige, ne traduis, ne reformate
RIEN : si on retire les marques, on doit retrouver exactement mon fichier.

Emplacements VARIABLES (réécrits pour chaque offre) — marque le TEXTE seul, pas les commandes autour :
  PROFIL                 le paragraphe de profil / résumé (s'il existe)
  E1_TITRE, E1_P         intitulé du poste et description de la 1re expérience (E2_…, E3_… ensuite) ;
                         si une expérience a plusieurs puces : E1_P, E1_P2, E1_P3…
  P1_TITRE, P1_TECH, P1_P  titre, technologies et description du 1er projet (P2_…, P3_… ensuite)
  SK1_LABEL, SK1         libellé et contenu de chaque ligne de compétences (SK2_…)
Emplacements FIXES (jamais réécrits, seulement traduits entre français et anglais) :
  TITRE                  le titre sous mon nom ; ECOLE, STAGE : autres lignes d'en-tête (école, objectif)
  TELEPHONE              le libellé devant le téléphone, s'il y en a un
  S_PROFIL, S_EXPERIENCE, S_PROJETS, S_FORMATION, S_COMPETENCES, S_LANGUES, S_CERTIFICATIONS,
  S_SOFTSKILLS           les titres de section (autres : S_ + un nom en majuscules)
  STAGE1, PERIODE1       entreprise (avec son lieu) et période de chaque expérience (STAGE2, PERIODE2…)
  F1, F2…                chaque ligne de formation ; L_1, L_2… : chaque langue ; SS1, SS2… : chaque soft skill
  BABEL                  l'option de langue de \usepackage[…]{babel}, s'il y en a une
  BABEL_REGLAGES         une ligne de réglage propre à cette langue (ex. \frenchsetup{…}), s'il y en a une
Tout le reste (nom, coordonnées, liens, certifications, mise en page, commandes) reste tel quel, SANS marque.
Chaque NOM n'est utilisé qu'UNE fois. Ne mets jamais de marque à l'intérieur d'une autre.
Réponds avec le document complet dans UN SEUL bloc ```latex, rien d'autre.
"""

CONVENTION_LM = r"""
Recopie ma lettre de motivation LaTeX CI-DESSOUS À L'IDENTIQUE, caractère pour caractère, en ajoutant
SEULEMENT des marques <<NOM|texte d'origine>>. Ne corrige, ne traduis, ne reformate RIEN.

  LM_P1, LM_P2, LM_P3    le corps de la lettre en EXACTEMENT 3 marques (si le corps a plus de 3
                         paragraphes, LM_P2 englobe tous les paragraphes du milieu)
  L_ENTREPRISE           le nom de l'entreprise destinataire (remplacé à chaque offre)
  L_VILLE                la ville du destinataire, AVEC le « \\ » qui la précède s'il y en a un
  L_SERVICE              le service destinataire (ex. « Service Recrutement »), s'il y en a un
  L_DATE                 la ligne de lieu et date (ex. « Paris, le 3 mars 2026 »)
  L_OBJET                la ligne d'objet
  L_SALUTATION           la formule d'appel (« Madame, Monsieur, »)
  L_FORMULE              la formule de politesse finale
  L_TITRE_DOC            le titre du document (ex. « Lettre de motivation »), s'il y en a un
  L_VILLE_SIGNATURE      la ville de l'expéditeur dans la signature, s'il y en a une
Tout le reste (mon nom, mes coordonnées, la mise en page) reste tel quel, SANS marque.
Réponds avec le document complet dans UN SEUL bloc ```latex, rien d'autre.
"""


class ErreurDocuments(Exception):
    pass


# ----------------------------------------------------------------------------------------------
# Outils
# ----------------------------------------------------------------------------------------------

def _norm(tex: str) -> str:
    return re.sub(r"\s+", " ", tex).strip()


def bloc_code(texte: str, langage: str = "") -> str:
    """Contenu du dernier bloc ``` (sinon le texte entier), sans l'en-tête affiché par le chat."""
    m = re.findall(r"```(?:" + langage + r")?[^\n]*\n(.*?)```", texte, re.DOTALL)
    brut = m[-1] if m else texte
    lignes = brut.splitlines()
    while lignes and re.fullmatch(r"\s*(latex|tex|text|json|copy|copier|download|télécharger)\s*", lignes[0], re.I):
        lignes.pop(0)
    return "\n".join(lignes).strip("\n")


def retirer_marques(tex: str) -> str:
    return MARQUE.sub(lambda m: m.group(2), tex)


def premier_ecart(a: str, b: str) -> str:
    a, b = _norm(a), _norm(b)
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
    return f"original : « …{a[max(0, i - 60):i + 60]}… »  |  ta version : « …{b[max(0, i - 60):i + 60]}… »"


def texte_pdf(pdf: Path) -> str:
    import pymupdf
    doc = pymupdf.open(pdf)
    t = "\n".join(p.get_text() for p in doc)
    doc.close()
    return t


def pages_pdf(pdf: Path) -> int:
    import pymupdf
    doc = pymupdf.open(pdf)
    n = len(doc)
    doc.close()
    return n


def compiler(tex: str, nom: str, dossier: Path) -> Path:
    from latex_compiler import compiler as compiler_latex
    dossier.mkdir(parents=True, exist_ok=True)
    f = dossier / f"{nom}.tex"
    f.write_text(tex, encoding="utf-8")
    return compiler_latex(f, dossier, dossiers_ressources=[ENTREE])


def ouvrir(fichier: Path) -> None:
    try:
        os.startfile(str(fichier))            # Windows : lecteur PDF / Bloc-notes par défaut
    except Exception:  # noqa: BLE001
        print(f"     (ouvre-le toi-même : {fichier})")


# ----------------------------------------------------------------------------------------------
# DeepSeek (navigateur 1, conversation dédiée)
# ----------------------------------------------------------------------------------------------

class Assistant:
    SYSTEME = ("Tu prépares les documents de candidature d'un candidat pour un robot. Tu suis les "
               "consignes À LA LETTRE, tu ne modifies jamais le texte du candidat, et tu réponds "
               "uniquement dans le format demandé.")

    def __init__(self):
        import copy
        import logging
        from deepseek_formulaire import ClientDeepSeekFormulaire
        from navigateur import Navigateur
        config, _ = charger_config()
        config = copy.deepcopy(config)
        cfg = config.setdefault("deepseek_formulaire", {})
        cfg["timeout_reponse_secondes"] = 900          # un CV entier à recopier : réponse longue
        cfg["envoyer_captures"] = False
        logger = logging.getLogger("job_automation")
        self.nav = Navigateur(config, logger)
        self.nav.demarrer(premier_plan=False)
        self.chat = ClientDeepSeekFormulaire(config, self.nav, logger)

    def demander(self, message: str) -> str:
        return self.chat.generer(self.SYSTEME, message, images=None, tache=None)

    def nouvelle_conversation(self) -> None:
        """Conversation neuve : les longs documents déjà échangés ne pèsent plus sur la réponse."""
        try:
            self.chat.fermer()
        except Exception:  # noqa: BLE001
            pass

    def fermer(self) -> None:
        try:
            self.chat.fermer()
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------------------------------
# Étapes
# ----------------------------------------------------------------------------------------------

def demander_fichier(question: str, extensions: tuple[str, ...]) -> Path:
    while True:
        s = input(f"  {question}\n     chemin du fichier ({', '.join(extensions)}) > ").strip().strip('"')
        p = Path(s)
        if p.is_file() and p.suffix.lower() in extensions:
            return p
        print("     ✗ fichier introuvable ou de mauvais type (glisse le fichier dans cette fenêtre pour coller son chemin)")


def pdf_vers_latex(assistant: Assistant, pdf: Path, genre: str) -> str:
    texte = texte_pdf(pdf)
    print(f"  Recréation de ton {genre} en LaTeX par DeepSeek (1 à 3 minutes)…")
    rep = assistant.demander(
        f"Voici le texte extrait de mon {genre} au format PDF. Écris un document LaTeX COMPLET (compilable "
        "avec pdflatex, paquets standards seulement : geometry, enumitem, xcolor, hyperref, graphicx, titlesec, "
        "babel, multicol) qui reproduit ce document le plus fidèlement possible : même texte mot pour mot, "
        "même ordre, même structure (sections, puces), sur le même nombre de pages. S'il y a une photo, "
        "utilise \\includegraphics[width=2.8cm]{photo.jpg}. Réponds avec le document dans UN SEUL bloc ```latex.\n\n"
        f"TEXTE DU PDF :\n{texte[:12000]}")
    tex = bloc_code(rep, "latex")
    if "\\documentclass" not in tex or "\\end{document}" not in tex:
        raise ErreurDocuments(f"DeepSeek n'a pas renvoyé un document LaTeX complet pour le {genre}")
    return tex


def marquer(assistant: Assistant, tex: str, convention: str, genre: str) -> str:
    """Document marqué dont le texte, marques retirées, est IDENTIQUE à l'original (3 essais)."""
    document = f"\n\nMON {genre.upper()} :\n```latex\n{tex}\n```"
    message, refus = convention + document, ""
    for essai in range(1, 4):
        if essai > 1:
            # Conversation NEUVE à chaque essai : les longs documents des essais ratés embrouillent
            # la suite (réponses sans marques, ancienne réponse relue…). Le document est renvoyé
            # en entier, avec la raison du refus.
            assistant.nouvelle_conversation()
            message = f"{convention}\nATTENTION, un essai précédent a été refusé : {refus}{document}"
        print(f"  Repérage des emplacements de ton {genre} par DeepSeek (essai {essai}/3)…")
        brut = assistant.demander(message)
        (RACINE / "logs").mkdir(exist_ok=True)
        (RACINE / "logs" / f"formulaire_reperage_{genre.split()[0].lower()}_{essai}.txt").write_text(brut, encoding="utf-8")
        marque = bloc_code(brut, "latex")
        noms = [m.group(1) for m in MARQUE.finditer(marque)]
        if _norm(retirer_marques(marque)) != _norm(tex):
            refus = ("le document n'était PAS identique à l'original une fois les marques retirées "
                     f"({premier_ecart(tex, retirer_marques(marque))}). Recopie EXACTEMENT, avec seulement les marques en plus.")
        elif len(noms) < 3:
            refus = "le document ne contenait (presque) aucune marque <<NOM|texte>> : il faut marquer TOUS les emplacements décrits."
        elif sorted({n for n in noms if noms.count(n) > 1}):
            refus = f"chaque NOM doit être unique ; ceux-ci apparaissaient plusieurs fois : {sorted({n for n in noms if noms.count(n) > 1})}."
        else:
            return marque
    raise ErreurDocuments(f"impossible d'obtenir un {genre} marqué fidèle à l'original après 3 essais")


def traduire(assistant: Assistant, textes: dict[str, str], vers: str) -> dict[str, str]:
    if not textes:
        return {}
    langue = "anglais" if vers == "en" else "français"
    assistant.nouvelle_conversation()
    res, reste, brut = {}, dict(textes), ""
    for essai in range(1, 3):
        lignes = "\n".join(f"{k} ::: {v}" for k, v in reste.items())
        brut = assistant.demander(
            f"Traduis en {langue} chacun de ces textes de CV / lettre (garde les commandes LaTeX telles quelles, "
            "garde « {date} » et « {poste} » tels quels, style CV professionnel). Réponds dans UN SEUL bloc ```text, "
            f"une ligne par texte, au format NOM ::: traduction, pour TOUS les noms ci-dessous.\n\n{lignes}")
        for ligne in bloc_code(brut, "text").splitlines():
            if ":::" in ligne:
                k, v = ligne.split(":::", 1)
                if k.strip() in reste:
                    res[k.strip()] = v.strip()
        reste = {k: v for k, v in textes.items() if k not in res}
        if not reste:
            return res
    (RACINE / "logs").mkdir(exist_ok=True)
    (RACINE / "logs" / "formulaire_traduction.txt").write_text(brut, encoding="utf-8")
    raise ErreurDocuments(f"traduction incomplète (manquent : {list(reste)}) — réponse gardée dans "
                          "logs/formulaire_traduction.txt")


def construire_cv(assistant: Assistant, tex: str, langue: str, dossier_travail: Path) -> dict:
    marque = marquer(assistant, tex, CONVENTION_CV, "CV")
    valeurs = {m.group(1): m.group(2) for m in MARQUE.finditer(marque)}
    reference = {k: v for k, v in valeurs.items() if VARIABLE.match(k)}
    fixes = {k: v for k, v in valeurs.items() if not VARIABLE.match(k)}
    if not any(k.startswith(("E", "P")) or k == "PROFIL" for k in reference):
        raise ErreurDocuments("aucun emplacement variable (profil, expériences, projets) repéré dans le CV")
    autre = "en" if langue == "fr" else "fr"
    a_traduire = {k: v for k, v in fixes.items() if k not in ("BABEL", "BABEL_REGLAGES")}
    print(f"  Traduction des textes fixes du CV en {'anglais' if autre == 'en' else 'français'}…")
    fixes_autre = traduire(assistant, a_traduire, autre)
    if "BABEL" in fixes:
        fixes_autre["BABEL"] = "english" if autre == "en" else "french"
    if "BABEL_REGLAGES" in fixes:
        fixes_autre["BABEL_REGLAGES"] = "" if autre == "en" else fixes["BABEL_REGLAGES"]
    gabarit = MARQUE.sub(lambda m: f"<<{m.group(1)}>>", marque)
    # Contrôles : l'original et le gabarit rempli donnent le même PDF ; l'autre langue compile.
    pdf_orig = compiler(tex, "original", dossier_travail)
    pdf_gab = compiler(_remplir(gabarit, {**fixes, **reference}), "gabarit_rempli", dossier_travail)
    if _norm(texte_pdf(pdf_orig)) != _norm(texte_pdf(pdf_gab)):
        raise ErreurDocuments("le gabarit rempli ne redonne pas exactement ton CV")
    pdf_autre = compiler(_remplir(gabarit, {**fixes_autre, **reference}), f"gabarit_{autre}", dossier_travail)
    return {"gabarit": gabarit, "reference": reference, langue: fixes, autre: fixes_autre,
            "pages": pages_pdf(pdf_orig), "pdf": pdf_orig, "pdf_autre": pdf_autre}


# La ville = derniers mots en texte simple, commençant après un espace, une accolade ou au début
# (jamais le nom d'une commande LaTeX comme « \hfill »). Ce qui la précède est gardé tel quel.
VILLE = re.compile(r"(.*?)(?<![^\s{}])([^\s\\{}]+(?:[ \-'][^\s\\{}]+)*)\}?\s*$", re.DOTALL)


def format_de_ville(texte: str) -> str:
    """« \\\\ \\hfill Lyon » -> « \\\\ \\hfill {ville} » (forme de la ville dans la lettre du candidat)."""
    m = VILLE.match(texte or "")
    if not m:
        return "{ville}"
    reste = texte[m.end(2):]
    return m.group(1) + "{ville}" + reste.rstrip()


def construire_lettre(assistant: Assistant, tex: str, langue: str, objectif, dossier_travail: Path) -> dict:
    marque = marquer(assistant, tex, CONVENTION_LM, "lettre de motivation")
    valeurs = {m.group(1): m.group(2) for m in MARQUE.finditer(marque)}
    for k in ("LM_P1", "LM_P2", "LM_P3"):
        if k not in valeurs:
            raise ErreurDocuments(f"la lettre doit avoir 3 paragraphes marqués (manque {k})")
    from cv_gabarit import visible_latex
    maximums = {k: len(visible_latex(valeurs[k])) for k in ("LM_P1", "LM_P2", "LM_P3")}
    fixe = {k: valeurs.get(k, "") for k in CLES_LETTRE}
    # Ville du destinataire : même forme que dans SA lettre (ce qui précède la ville est gardé)
    format_ville = format_de_ville(valeurs.get("L_VILLE", ""))
    # Objet et date : construits pour chaque offre (« {poste} », « {date} »)
    reperes = objectif.reperes()
    objet = {"fr": f"Objet : Candidature -- {reperes['OBJET_LM']} -- {{poste}}",
             "en": f"Subject: Application -- {objectif.maj(objectif.objet_en)} -- {{poste}}"}
    a_traduire = {k: v for k, v in fixe.items() if v and k not in ("L_OBJET",)}
    if fixe["L_DATE"]:
        ville = re.split(r",", fixe["L_DATE"])[0].strip()
        a_traduire["L_DATE"] = f"{ville}, le {{date}}" if langue == "fr" else f"{ville}, {{date}}"
        fixe["L_DATE"] = a_traduire["L_DATE"]
    autre = "en" if langue == "fr" else "fr"
    print(f"  Traduction des textes fixes de la lettre en {'anglais' if autre == 'en' else 'français'}…")
    fixe_autre = {**{k: "" for k in CLES_LETTRE}, **traduire(assistant, a_traduire, autre)}
    if valeurs.get("L_OBJET"):
        fixe["L_OBJET"], fixe_autre["L_OBJET"] = objet[langue], objet[autre]
    lettre = MARQUE.sub(lambda m: f"<<{m.group(1)}>>", marque)
    return {"lettre": lettre, langue: {**fixe, "MOIS": MOIS[langue], "L_VILLE_FORMAT": format_ville},
            autre: {**fixe_autre, "MOIS": MOIS[autre], "L_VILLE_FORMAT": format_ville}, "maximums": maximums}


def _remplir(gabarit: str, valeurs: dict) -> str:
    for k, v in valeurs.items():
        gabarit = gabarit.replace(f"<<{k}>>", v)
    return gabarit


def messages_prives(assistant: Assistant, cv: dict, tex_cv: str, tex_lm: str, objectif, profil: dict) -> dict:
    exp = sorted({k.split("_")[0] for k in cv["reference"] if re.match(r"E\d+_", k)}, key=lambda x: int(x[1:]))
    proj = sorted({k.split("_")[0] for k in cv["reference"] if re.match(r"P\d+_", k)}, key=lambda x: int(x[1:]))
    fixes = cv.get("fr") or cv.get("en") or {}
    entreprises = [re.sub(r"\\[a-zA-Z]+|[{}]", "", fixes.get(f"STAGE{e[1:]}", "")).strip() for e in exp]
    entete = " ; ".join(f"« {re.sub(chr(92) + '[a-zA-Z]+|[{}]', '', fixes[k]).strip()} »"
                        for k in ("TITRE", "ECOLE", "STAGE") if fixes.get(k))
    ident = profil.get("identite", {}) or {}
    m1 = (RACINE / "config" / "modeles" / "message_1.modele.txt").read_text(encoding="utf-8")
    m1 = (m1.replace("{{PRESENTATION}}", f"{ident.get('prenom', '')} {ident.get('nom', '')}, "
                                            f"{objectif.presentation_fr[:1].lower() + objectif.presentation_fr[1:]}")
            .replace("{{OBJECTIF}}", f"{objectif.un(objectif.nom_fr)} {objectif.formule_fr}")
            .replace("{{ENTETE}}", entete or "les lignes d'en-tête")
            .replace("{{NB_EXPERIENCES}}", str(len(exp)))
            .replace("{{EXPERIENCES}}", ", ".join(e for e in entreprises if e) or "voir mes faits réels")
            .replace("{{NB_PROJETS}}", str(len(proj))))
    m3 = (RACINE / "config" / "modeles" / "message_3.modele.txt").read_text(encoding="utf-8")
    cles = lambda liste: " à ".join(f"« {x}_P »" for x in (liste[:1] + liste[-1:] if len(liste) > 1 else liste))  # noqa: E731
    m3 = m3.replace("{{CLES_EXPERIENCES}}", cles(exp) or "« E…_P »").replace("{{CLES_PROJETS}}", cles(proj) or "« P…_P »")
    print("  Rédaction de tes « faits réels » (message 2) par DeepSeek, à partir de ton CV et de ta lettre…")
    rep = assistant.demander(
        "À partir de mon CV et de ma lettre ci-dessous, écris le message « MES FAITS RÉELS — la seule matière "
        "autorisée pour mon CV et ma lettre. » Structure : FORMATION (chaque ligne avec dates) ; MES EXPÉRIENCES "
        "(pour chacune : entreprise, lieu, période, puis TOUTES les réalisations réelles avec leurs technologies "
        "et leurs chiffres EXACTS tels qu'écrits) ; MES PROJETS (titre, technologies, ce qui a été fait) ; "
        "COMPÉTENCES ; SOFT SKILLS ; LANGUES. N'ajoute RIEN qui ne soit pas écrit dans mes documents, ne change "
        "aucun chiffre. Termine par : « Réponds « OK » si tu as bien noté mes faits. » Réponds dans UN SEUL bloc ```text.\n\n"
        f"MON CV (LaTeX) :\n{tex_cv[:9000]}\n\nMA LETTRE (LaTeX) :\n{tex_lm[:5000]}")
    m2 = bloc_code(rep, "text")
    if len(m2) < 300:
        raise ErreurDocuments("le message des faits réels est vide ou trop court")
    return {"message_1.txt": m1, "message_2.txt": m2, "message_3.txt": m3}


# ----------------------------------------------------------------------------------------------
# Section
# ----------------------------------------------------------------------------------------------

def section(r: dict) -> None:
    import yaml
    import formulaire_modeles as M
    from formulaire import sauvegarder_existants
    from objectif import Objectif
    profil = yaml.safe_load((RACINE / "config" / "profile.yaml").read_text(encoding="utf-8"))
    objectif = Objectif(profil)

    print("\n  Donne ton CV et ta lettre de motivation (LaTeX recommandé : ils sont utilisés tels quels ;")
    print("  un PDF est recréé en LaTeX par DeepSeek), et ta photo. Glisse chaque fichier dans cette fenêtre.")
    f_cv = demander_fichier("Ton CV", (".tex", ".pdf"))
    f_lm = demander_fichier("Ta lettre de motivation", (".tex", ".pdf"))
    f_photo = demander_fichier("Ta photo", (".jpg", ".jpeg", ".png"))
    langue = "en" if input("  Ton CV est-il en anglais ? (o/N) > ").strip().lower() in ("o", "oui", "y", "yes") else "fr"

    travail = Path(tempfile.mkdtemp(prefix="formulaire_docs_"))
    assistant = Assistant()
    try:
        # Ressources du LaTeX (images, .cls…) : copiées dans input/ (là où le robot compile)
        for f in (f_cv, f_lm):
            if f.suffix.lower() == ".tex":
                for voisin in f.parent.iterdir():
                    if voisin.is_file() and voisin.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".cls", ".sty", ".eps") \
                            and voisin.stat().st_size < 5_000_000:
                        shutil.copy2(voisin, ENTREE / voisin.name)
        tex_cv = f_cv.read_text(encoding="utf-8") if f_cv.suffix.lower() == ".tex" else pdf_vers_latex(assistant, f_cv, "CV")
        tex_lm = f_lm.read_text(encoding="utf-8") if f_lm.suffix.lower() == ".tex" else pdf_vers_latex(assistant, f_lm, "lettre de motivation")
        # La photo prend le nom attendu par le LaTeX du CV (sinon photo.jpg)
        images = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex_cv)
        noms_photo = [i for i in images if not re.search(r"github|linkedin|logo|icon", i, re.I)] or ["photo.jpg"]
        nom_photo = noms_photo[0] if Path(noms_photo[0]).suffix else noms_photo[0] + f_photo.suffix.lower()
        shutil.copy2(f_photo, ENTREE / Path(nom_photo).name)

        cv = construire_cv(assistant, tex_cv, langue, travail / "cv")
        lm = construire_lettre(assistant, tex_lm, langue, objectif, travail / "lm")
        messages = messages_prives(assistant, cv, tex_cv, tex_lm, objectif, profil)
    finally:
        assistant.fermer()

    # Aperçus à valider AVANT d'écrire quoi que ce soit dans le projet
    apercu_lm = _remplir(lm["lettre"], {
        **{k: v for k, v in lm[langue].items() if k not in ("MOIS", "L_VILLE_FORMAT")},
        "L_ENTREPRISE": "Entreprise Exemple", "L_VILLE": lm[langue]["L_VILLE_FORMAT"].replace("{ville}", "Paris"),
        "L_DATE": lm[langue]["L_DATE"].format(date="1er mars 2026") if lm[langue]["L_DATE"] else "",
        "L_OBJET": lm[langue]["L_OBJET"].format(poste="Poste exemple") if lm[langue]["L_OBJET"] else "",
    })
    pdf_lm = compiler(apercu_lm.replace("<<LM_P1>>", "Premier paragraphe d'exemple.")
                      .replace("<<LM_P2>>", "Deuxième paragraphe d'exemple.")
                      .replace("<<LM_P3>>", "Troisième paragraphe d'exemple."), "apercu_lettre", travail / "lm")
    print("\n  APERÇUS : ton CV reconstruit par le robot et la lettre à trous (paragraphes d'exemple).")
    ouvrir(cv["pdf"])
    ouvrir(cv["pdf_autre"])
    ouvrir(pdf_lm)
    print("  Aussi : les faits réels que DeepSeek utilisera (message 2) — relis-les bien :")
    faits = travail / "message_2_a_relire.txt"
    faits.write_text(messages["message_2.txt"], encoding="utf-8")
    ouvrir(faits)
    input("  Tu peux corriger le fichier des faits réels dans le Bloc-notes et l'enregistrer. Entrée quand c'est fait… ")
    messages["message_2.txt"] = faits.read_text(encoding="utf-8")
    if input("  Les aperçus et les faits réels sont-ils corrects ? (O/n) > ").strip().lower() in ("n", "non"):
        raise ErreurDocuments("aperçus refusés : relance cette section (les fichiers du projet n'ont pas été modifiés)")

    # Écriture (avec sauvegarde de l'existant)
    dossier = sauvegarder_existants([GABARIT, MESSAGES, ENTREE / "cv_original.tex", ENTREE / "lm_original.tex"])
    GABARIT.mkdir(parents=True, exist_ok=True)
    MESSAGES.mkdir(parents=True, exist_ok=True)
    (GABARIT / "gabarit.tex").write_text(cv["gabarit"], encoding="utf-8")
    M.ecrire(GABARIT / "reference_en.json", json.dumps(cv["reference"], ensure_ascii=False, indent=1))
    M.ecrire(GABARIT / "fixe_fr.json", json.dumps(cv["fr"], ensure_ascii=False, indent=1))
    M.ecrire(GABARIT / "fixe_en.json", json.dumps(cv["en"], ensure_ascii=False, indent=1))
    (GABARIT / "lettre.tex").write_text(lm["lettre"], encoding="utf-8")
    M.ecrire(GABARIT / "lettre_fixe.json", json.dumps({"fr": lm["fr"], "en": lm["en"], "maximums": lm["maximums"]},
                                                      ensure_ascii=False, indent=1))
    for nom, pdf in (("apercu_cv_" + langue, cv["pdf"]), ("apercu_cv_" + ("en" if langue == "fr" else "fr"), cv["pdf_autre"])):
        shutil.copy2(pdf, GABARIT / f"{nom}.pdf")
    for nom, texte in messages.items():
        (MESSAGES / nom).write_text(texte, encoding="utf-8")
    (ENTREE / "cv_original.tex").write_text(tex_cv, encoding="utf-8")
    (ENTREE / "lm_original.tex").write_text(tex_lm, encoding="utf-8")
    _maj_pages_max(cv["pages"])
    print(f"\n  ✓ Gabarit du CV et de la lettre, et messages privés écrits (CV sur {cv['pages']} page(s))."
          + (f"\n    Anciennes versions sauvegardées dans {dossier.relative_to(RACINE)}" if dossier else ""))


def _maj_pages_max(pages: int) -> None:
    import formulaire_modeles as M
    f = RACINE / "config" / "config.yaml"
    texte = re.sub(r"^(  pages_max_cv: )\d+", rf"\g<1>{pages}", f.read_text(encoding="utf-8"), count=1, flags=re.M)
    M.ecrire(f, texte)
