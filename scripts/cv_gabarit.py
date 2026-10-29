"""
CV et lettre par « gabarit à trous » (config/cv_gabarit).

La mise en page ne vient JAMAIS du modèle : DeepSeek ne renvoie que des TEXTES (JSON), que le
programme place dans des gabarits LaTeX fixes (gabarit.tex pour le CV, lettre.tex pour la
lettre). Le programme mesure ensuite le résultat et redemande des corrections ciblées :
  - chaque section du CV ne dépasse jamais son maximum de caractères (celui du CV de
    référence, reference_en.json) et le total reste dans [N - 20, N] ;
  - chaque texte garde le MÊME NOMBRE DE LIGNES que dans le CV de référence (mesuré dans le
    PDF) -> hauteur identique -> une page ;
  - chaque paragraphe de la lettre respecte son maximum et la lettre tient sur une page.
"""
from __future__ import annotations

import json
import logging
import math
import re
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

from objectif import courant as objectif_courant
from utils import chemin_projet

DOSSIER = chemin_projet("config/cv_gabarit")


class ErreurGabarit(Exception):
    """Le CV ou la lettre n'a pas pu être rendu conforme (ex. plus d'une page)."""


# ------------------------------------------------------------------ texte
def visible_latex(t: str) -> str:
    """Texte affiché d'un fragment LaTeX (pour mesurer les textes de référence)."""
    t = re.sub(r"\\text(bf|it|superscript)\{([^}]*)\}", r"\2", t).replace("--", "–")
    t = re.sub(r"\\([&%_#$])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def visible(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def echapper(t: str) -> str:
    """Texte simple (JSON du modèle) -> LaTeX sûr."""
    t = visible(t).replace("\\", "\0")
    for c in "&%_#$":
        t = t.replace(c, "\\" + c)
    return t.replace("{", r"\{").replace("}", r"\}").replace("\0", r"\textbackslash{}")


def normaliser(t: str) -> str:
    """Forme comparable d'un texte du PDF (sans espaces, minuscules, ligatures dépliées)."""
    t = unicodedata.normalize("NFKC", t).replace("’", "'").replace("–", "-").replace("—", "-")
    # Police LaTeX T1 : tirets et ligatures extraits comme caractères de contrôle.
    for code, lettres in (("\x15", "-"), ("\x14", "-"), ("\x1b", "ff"), ("\x1c", "fi"),
                          ("\x1d", "fl"), ("\x1e", "ffi"), ("\x1f", "ffl")):
        t = t.replace(code, lettres)
    return re.sub(r"\s+", "", t).lower()


def lignes_pdf(pdf: Path) -> tuple[int, list[str]]:
    """(nombre de pages, lignes de la page 1 dans l'ordre de lecture ; même hauteur = même ligne)."""
    import pymupdf
    doc = pymupdf.open(pdf)
    morceaux = []
    for bloc in doc[0].get_text("dict")["blocks"]:
        for ligne in bloc.get("lines", []):
            txt = "".join(s["text"] for s in ligne["spans"])
            if txt.strip():
                morceaux.append((round(ligne["bbox"][1] / 3), ligne["bbox"][0], txt))
    par_hauteur: dict = {}
    for y, _x, txt in sorted(morceaux):
        par_hauteur.setdefault(y, []).append(txt)
    pages = len(doc)
    doc.close()
    return pages, [" ".join(v) for _, v in sorted(par_hauteur.items())]


def _mots(car: int) -> str:
    n = max(1, round(car / 7))
    return "1 mot" if n == 1 else f"{n} mots"


# ------------------------------------------------------------------ gabarit
class GabaritCV:
    MAX_TOURS = 7
    TOLERANCE_TOTAL = 20
    MANQUE_ACCEPTE = 40      # arrêt dès que tout est dans les limites et le total ≥ N - 40

    def __init__(self, compiler, logger: logging.Logger | None = None):
        """`compiler(tex, dossier, nom) -> Path du PDF` : la compilation du robot (liens neutralisés)."""
        self._compiler = compiler
        self.logger = logger or logging.getLogger("job_automation")
        self.gabarit = (DOSSIER / "gabarit.tex").read_text(encoding="utf-8")
        self.ref_latex = json.loads((DOSSIER / "reference_en.json").read_text(encoding="utf-8"))
        self.ref = {k: visible_latex(v) for k, v in self.ref_latex.items()}
        self.MAX = {k: len(v) for k, v in self.ref.items()}
        self.N = sum(self.MAX.values())
        self.ordre = [k for k in re.findall(r"<<([A-Z0-9_]+)>>", self.gabarit) if k in self.MAX]
        self.sections = {
            "Titre": ["TITRE"], "Profil": ["PROFIL"],
            "Expérience": [k for k in self.ordre if k.startswith("E")],
            "Projets": [k for k in self.ordre if k[0] == "P" and k[1].isdigit()],
            "Compétences": [k for k in self.ordre if k.startswith("SK")],
            "Soft skills": [k for k in self.ordre if k.startswith("SS")],
        }
        # Titre et soft skills sont FIXES (fixe_fr/en.json) : sections vides écartées.
        self.sections = {nom: [k for k in cles if k in self.MAX]
                         for nom, cles in self.sections.items()}
        self.sections = {nom: cles for nom, cles in self.sections.items() if cles}
        self.max_section = {nom: sum(self.MAX[k] for k in cles) for nom, cles in self.sections.items()}
        self.section_de = {k: nom for nom, cles in self.sections.items() for k in cles}
        # Textes qui peuvent passer à la ligne (…_TECH / …_LABEL partagent une ligne fixe).
        self.mesures = [k for k in self.ordre if not k.endswith(("_TECH", "_LABEL"))]
        self.lettre = (DOSSIER / "lettre.tex").read_text(encoding="utf-8")
        self.lettre_fixe = json.loads((DOSSIER / "lettre_fixe.json").read_text(encoding="utf-8"))
        self.LM_MAX = dict(self.lettre_fixe["maximums"])
        self._lignes_ref: dict | None = None
        self._largeur = 110

    # -- remplissage -------------------------------------------------------------
    def fixes(self, langue: str) -> dict:
        return json.loads((DOSSIER / f"fixe_{langue}.json").read_text(encoding="utf-8"))

    def remplir_cv(self, textes: dict, langue: str, latex_brut: bool = False) -> str:
        tex = self.gabarit
        for k, v in self.fixes(langue).items():
            tex = tex.replace(f"<<{k}>>", v)
        for k in self.MAX:
            if latex_brut:
                tex = tex.replace(f"<<{k}>>", textes[k])
                continue
            v = visible(textes.get(k, ""))
            if k.endswith("_LABEL") and v and not v.endswith(":"):
                v += ":"          # libellé de compétences toujours suivi de « : »
            tex = tex.replace(f"<<{k}>>", echapper(v))
        return tex

    def remplir_lettre(self, textes: dict, offre: dict, langue: str) -> str:
        fixe = self.lettre_fixe[langue]
        maintenant = datetime.now()
        mois = fixe["MOIS"][maintenant.month - 1]
        date = (f"{maintenant.day} {mois} {maintenant.year}" if langue == "fr"
                else f"{mois} {maintenant.day}, {maintenant.year}")
        ville = visible(offre.get("lieu", ""))
        valeurs = {
            "L_TITRE_DOC": fixe["L_TITRE_DOC"],
            "L_ENTREPRISE": echapper(offre.get("nom_entreprise", "")),
            "L_SERVICE": fixe["L_SERVICE"],
            # Format de la ville du destinataire : celui de la lettre du candidat (lettre_fixe.json,
            # « L_VILLE_FORMAT », écrit par FORMULAIRE.bat) ; sinon un saut de ligne puis la ville.
            "L_VILLE": (fixe.get("L_VILLE_FORMAT", "\\\\{} {ville}").replace("{ville}", echapper(ville))
                        if ville else ""),
            "L_DATE": fixe["L_DATE"].format(date=date),
            "L_OBJET": fixe["L_OBJET"].format(poste=echapper(offre.get("titre_poste", ""))),
            "L_SALUTATION": fixe["L_SALUTATION"],
            "L_FORMULE": fixe["L_FORMULE"],
            "L_VILLE_SIGNATURE": fixe["L_VILLE_SIGNATURE"],
        }
        tex = self.lettre
        for k, v in valeurs.items():
            tex = tex.replace(f"<<{k}>>", v)
        for k in self.LM_MAX:
            tex = tex.replace(f"<<{k}>>", echapper(textes.get(k, "")))
        return tex

    # -- mesure --------------------------------------------------------------------
    def mesurer(self, textes: dict, lignes: list[str]) -> dict:
        """{clé: (nb de lignes occupées, longueur de sa dernière ligne)}."""
        res, curseur = {}, 0
        brutes = [normaliser(l) for l in lignes]
        for k in self.mesures:
            t = normaliser(visible(textes.get(k, "")))
            if not t:
                continue
            tete, queue = t[:40], t[-4:]
            debut = next((i for i in range(curseur, len(brutes)) if tete in brutes[i]), None)
            if debut is None:
                continue
            fin = next((i for i in range(debut, min(debut + 8, len(brutes)))
                        if queue in brutes[i][(brutes[i].find(tete) if i == debut else 0):]), None)
            if fin is None:
                continue
            res[k] = (fin - debut + 1, len(lignes[fin].strip()))
            curseur = fin + 1
        return res

    def lignes_reference(self) -> dict:
        """Nombre de lignes de chaque texte dans le CV de référence (compilé une fois)."""
        if self._lignes_ref is None:
            with tempfile.TemporaryDirectory() as tmp:
                pdf = self._compiler(self.remplir_cv(self.ref_latex, "en", latex_brut=True),
                                     Path(tmp), "reference")
                _, lignes = lignes_pdf(pdf)
            self._lignes_ref = {k: v[0] for k, v in self.mesurer(self.ref, lignes).items()}
            self._largeur = max((len(l) for l in lignes[8:]), default=110)
        return self._lignes_ref

    # -- messages --------------------------------------------------------------------
    def message_offre(self, offre: dict, langue: str) -> str:
        lignes = self.lignes_reference()
        obj = objectif_courant()
        dispo = (f"disponibilité à partir de {obj.mois_fr}" if obj.date_fr else "disponibilité immédiate")
        fin_p3 = ", ".join(x for x in (
            f"pendant les {obj.duree_fr}" if obj.duree_fr else "dans ce poste",
            dispo, "stage conventionné" if (obj.type == "stage" and obj.conventionne) else ""
        ) if x)
        champs = ("nom_entreprise", "titre_poste", "lieu", "type_contrat",
                  "description_entreprise", "description_offre")
        texte_offre = "\n".join(f"{k} : {offre[k]}" for k in champs if offre.get(k))

        def cible(k: str) -> str:
            n = lignes.get(k)
            sur = f", tient sur {n} ligne{'s' if n and n > 1 else ''}" if n else ""
            return f"{k} : ~{self.MAX[k]} caractères (~{max(1, round(self.MAX[k] / 6.5))} mots{sur})"

        return (
            f"NOUVELLE OFFRE\n\n{texte_offre}\n\n"
            f"LANGUE DU CV ET DE LA LETTRE : {'français' if langue == 'fr' else 'anglais'}\n\n"
            "EMPLACEMENTS DU CV — longueur INDICATIVE (ne compte pas, le programme le fait) :\n"
            + "\n".join(cible(k) for k in self.ordre)
            + "\n\nLimite STRICTE par SECTION (somme de ses clés) :\n"
            + "\n".join(f"{nom} : {m} caractères maximum" for nom, m in self.max_section.items())
            + "\n\nLETTRE DE MOTIVATION — 3 paragraphes, à la première personne, ton professionnel "
              "(l'en-tête, l'objet, la formule de politesse et la signature sont écrits par le programme) :\n"
            f"LM_P1 : ~{self.LM_MAX['LM_P1']} caractères — accroche : pourquoi cette entreprise et ce poste "
            f"(éléments réels de l'offre), et candidature {obj.au(obj.libelle_court_fr)}.\n"
            f"LM_P2 : ~{self.LM_MAX['LM_P2']} caractères — une ou deux réalisations réelles de mes stages ou "
            "projets en lien direct avec les missions de l'offre (avec mes chiffres réels si pertinents).\n"
            f"LM_P3 : ~{self.LM_MAX['LM_P3']} caractères — ce que je viens apprendre et apporter "
            f"{fin_p3}.\n\n"
            "Réponds avec UN SEUL bloc ```json contenant toutes ces clés (CV et lettre), rien d'autre."
        )

    # -- génération complète -------------------------------------------------------
    def generer(self, offre: dict, dossier: Path, chat, nom_cv: str, nom_lm: str) -> tuple[Path, Path]:
        langue = "en" if str(offre.get("langue_offre", "fr")).lower().startswith("en") else "fr"
        lignes_ref = self.lignes_reference()
        toutes = set(self.MAX) | set(self.LM_MAX)
        rep = chat.generer_json(self.message_offre(offre, langue), valide=lambda d: "PROFIL" in d)
        textes = {k: v for k, v in rep.items() if k in toutes}
        self.logger.info(f"Gabarit : CV et lettre reçus de DeepSeek ({len(textes)} textes, {langue}).")

        meilleur = None           # (total, textes) de la meilleure version acceptable
        for tour in range(1, self.MAX_TOURS + 1):
            pdf_cv = self._compiler(self.remplir_cv(textes, langue), dossier, nom_cv)
            pages, lignes = lignes_pdf(pdf_cv)
            mes = self.mesurer(textes, lignes)
            longueurs = {nom: sum(len(visible(textes.get(k))) for k in cles)
                         for nom, cles in self.sections.items()}
            total = sum(longueurs.values())
            debords = {k: n - lignes_ref[k] for k, (n, _) in mes.items()
                       if k in lignes_ref and n > lignes_ref[k]}
            trop = {nom: longueurs[nom] - self.max_section[nom]
                    for nom in self.sections if longueurs[nom] > self.max_section[nom]}
            pdf_lm = self._compiler(self.remplir_lettre(textes, offre, langue), dossier, nom_lm)
            pages_lm, _ = lignes_pdf(pdf_lm)
            trop_lm = {k: len(visible(textes.get(k))) - m for k, m in self.LM_MAX.items()
                       if len(visible(textes.get(k))) > m}
            manquantes = [k for k in toutes if not visible(textes.get(k))]
            self.logger.info(
                f"Gabarit tour {tour} : CV {pages} page(s), lignes en trop {debords or 'aucune'}, total {total} "
                f"({self.N - self.TOLERANCE_TOTAL}-{self.N}) | "
                + " ".join(f"{n[:4]} {longueurs[n]}/{self.max_section[n]}" for n in self.sections)
                + f" | lettre {pages_lm} page(s)")
            # Acceptable = tout est dans les limites, seul le total peut être un peu court.
            acceptable = (pages == 1 and not debords and not trop and not manquantes
                          and not trop_lm and pages_lm == 1 and total <= self.N)
            # Assez proche : quelques mots de moins sur toute la page ne se voient pas, alors
            # qu'un tour de plus coûte ~15 s (et risque d'abîmer une version juste).
            conforme = acceptable and total >= self.N - self.MANQUE_ACCEPTE
            if acceptable and (meilleur is None or total > meilleur[0]):
                meilleur = (total, dict(textes))
            if conforme or tour == self.MAX_TOURS:
                break
            demandes = self._demandes(textes, mes, longueurs, total, debords, trop, manquantes,
                                      trop_lm, pages_lm)
            if not demandes:
                break
            correction = ("AJUSTEMENT — ne compte rien, suis ces indications, mêmes faits et même sens. "
                          "Modifie UNIQUEMENT ces clés :\n" + "\n".join(demandes)
                          + "\n\nGarde TOUJOURS les chiffres réels et la fin de chaque paragraphe "
                            "(apport ou résultat) : ajuste la partie descriptive. Dans le CV : pas de "
                            "« je », au plus deux technologies par paragraphe."
                            "\nRenvoie un bloc ```json avec SEULEMENT ces clés.")
            rep = chat.demander_json(correction, valide=lambda d: any(k in toutes for k in d))
            textes.update({k: v for k, v in rep.items() if k in toutes})

        if not conforme and meilleur is not None and meilleur[1] != textes:
            # Le dernier ajustement a abîmé une version qui était dans les limites : on la reprend.
            self.logger.info(f"Gabarit : meilleure version acceptable reprise (total {meilleur[0]}).")
            textes = meilleur[1]
            pdf_cv = self._compiler(self.remplir_cv(textes, langue), dossier, nom_cv)
            pages, _ = lignes_pdf(pdf_cv)
            pdf_lm = self._compiler(self.remplir_lettre(textes, offre, langue), dossier, nom_lm)
            pages_lm, _ = lignes_pdf(pdf_lm)
        if pages > 1:
            raise ErreurGabarit(f"CV sur {pages} pages malgré les ajustements")
        if pages_lm > 1:
            raise ErreurGabarit(f"lettre sur {pages_lm} pages malgré les ajustements")
        return pdf_cv, pdf_lm

    def _demandes(self, textes, mes, longueurs, total, debords, trop, manquantes, trop_lm, pages_lm):
        demandes, touchees = [], set()
        for k in manquantes:
            demandes.append(f"{k} : manquant, écris-le")
            touchees.add(k)
        for k in debords:          # une ligne de trop : enlever ce qui dépasse sur la dernière ligne
            # … ou tout l'excès de sa section si celle-ci est aussi trop longue (le plus grand).
            a_enlever = max(mes[k][1] + 3, trop.get(self.section_de.get(k), 0) + 7)
            demandes.append(f"{k} : passe sur une ligne de trop, enlève {_mots(a_enlever)}")
            touchees.add(k)
        for nom, exces in trop.items():       # section trop longue : sur SA clé la plus longue
            if any(self.section_de.get(k) == nom for k in touchees):
                continue
            k = max((c for c in self.sections[nom] if not c.endswith("_TECH")),
                    key=lambda c: len(visible(textes.get(c))))
            demandes.append(f"{k} : un peu trop long, enlève {_mots(exces + 7)}")
            touchees.add(k)
        for k, exces in trop_lm.items():
            demandes.append(f"{k} : un peu trop long, enlève {_mots(exces + 7)}")
        if pages_lm > 1 and not trop_lm:
            demandes.append("LM_P2 : la lettre dépasse une page, enlève ~15 mots")
        # Total visé une fois les sections trop longues ramenées à leur maximum.
        total_vise = total - sum(trop.values())
        if total_vise < self.N - self.TOLERANCE_TOTAL:
            # Total trop court : on n'allonge QUE là où la dernière ligne a de la place, et
            # jamais dans une section déjà trop longue ni sur un texte qui déborde d'une ligne ;
            # ~70 % de la place, car le modèle en ajoute toujours un peu plus que demandé.
            manque = (self.N - 6) - total_vise
            lignes_ref = self.lignes_reference()
            # Place = fin de la dernière ligne + lignes entières manquantes par rapport à la
            # référence (ex. un profil sur 3 lignes au lieu de 4 : une ligne complète libre).
            places = sorted((((lignes_ref[k] - mes[k][0]) * self._largeur
                              + self._largeur - mes[k][1] - 4, k)
                             for k in mes if k in lignes_ref), reverse=True)
            for place, k in places:
                if self.section_de[k] in trop or k.endswith("_TITRE"):
                    continue          # les titres restent courts : on allonge les paragraphes
                libre = self.max_section[self.section_de[k]] - longueurs[self.section_de[k]] - 2
                ajout = min(int(place * 0.7), manque, libre)
                if ajout >= (3 if manque <= 20 else 7) and k not in touchees:
                    n = lignes_ref[k]
                    limite = ("sans passer à la ligne" if mes[k][0] >= n
                              else f"sans dépasser {n} ligne{'s' if n > 1 else ''} au total")
                    demandes.append(f"{k} : trop court, ajoute {_mots(ajout) if ajout > 9 else '1 mot court'} "
                                    f"({limite})")
                    touchees.add(k)
                    manque -= ajout
                    longueurs[self.section_de[k]] += ajout
                if manque <= 0:
                    break
        return demandes
