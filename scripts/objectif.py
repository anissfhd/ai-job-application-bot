"""
Objectif de recherche du candidat (config/objectif.yaml) : stage PFE, stage PFA, alternance,
emploi… Tout ce qui dépendait de « stage PFE de 6 mois à partir du 1er mars 2027 » — et de
l'email du candidat — passe par ici au lieu d'être écrit en dur :

  - les consignes de prompts/*.txt contiennent des repères [[NOM]] remplis par `remplir()` ;
  - l'email de candidature, le gabarit du CV, le contrôle de la lettre, le filtre du type de
    contrat et le tri du domaine lisent les phrases et contrôles d'ici.

Les phrases sont construites pour redonner MOT POUR MOT les textes d'origine avec l'objectif
d'origine (stage PFE de 6 mois dès le 1er mars 2027) : pour ce candidat, rien ne change.
"""
from __future__ import annotations

import re
from functools import lru_cache

import yaml

from utils import chemin_projet

VOYELLES = "aeiouyàâäéèêëîïôöùûüh"
FEMININS = ("alternance", "mission", "thèse", "these", "formation")
NOMBRES = {1: ("un|une", "one"), 2: ("deux", "two"), 3: ("trois", "three"), 4: ("quatre", "four"),
           5: ("cinq", "five"), 6: ("six", "six"), 7: ("sept", "seven"), 8: ("huit", "eight"),
           9: ("neuf", "nine"), 10: ("dix", "ten"), 11: ("onze", "eleven"), 12: ("douze", "twelve")}
UNITES = {"mois": ("mois", "months?"), "an": ("ans?|années?", "years?"), "ans": ("ans?|années?", "years?"),
          "année": ("ans?|années?", "years?"), "années": ("ans?|années?", "years?"),
          "semaine": ("semaines?", "weeks?"), "semaines": ("semaines?", "weeks?")}
# Autres écritures d'un même sigle (ce que DeepSeek peut légitimement écrire à la place).
SYNONYMES = {"PFE": [r"fin d.études", r"end[\s-]of[\s-]studies", r"final[\s-]year"],
             "PFA": [r"fin d.année", r"end[\s-]of[\s-]year"]}


def _elide(mot: str) -> bool:
    return bool(mot) and mot[0].lower() in VOYELLES


class Objectif:
    def __init__(self, profil: dict, obj: dict | None = None):
        self.profil = profil or {}
        o = dict(obj if obj is not None else charger_objectif())
        self.type = str(o.get("type") or "stage").strip().lower()
        self.contrats = [str(c).strip().lower() for c in (o.get("contrats_acceptes") or [self.type])]
        self.nom_fr = str(o.get("nom_fr") or "").strip()
        self.nom_en = str(o.get("nom_en") or "").strip()
        self.sigle = str(o.get("sigle") or "").strip()
        self.duree_fr = str(o.get("duree_fr") or "").strip()
        self.duree_en = str(o.get("duree_en") or "").strip()
        self.duree_adj_en = str(o.get("duree_adjectif_en") or "").strip()
        self.date_fr = str(o.get("date_debut_fr") or "").strip()
        self.date_en = str(o.get("date_debut_en") or "").strip()
        self.mois_fr = str(o.get("mois_debut_fr") or self.date_fr).strip()
        self.conventionne = bool(o.get("conventionne", False))
        self.statut_fr = str(o.get("statut_fr") or "").strip()
        self.statut_long_fr = str(o.get("statut_long_fr") or self.statut_fr).strip()
        self.presentation_fr = str(o.get("presentation_fr") or "").strip()
        ident = self.profil.get("identite", {}) or {}
        self.email = str(ident.get("email") or "").strip()
        self.prenom = str(ident.get("prenom") or "").strip()
        self.femme = str(ident.get("genre") or "").strip().lower().startswith(("f", "w"))

    # -- petits outils de grammaire -------------------------------------------------------
    def _fem(self, mot: str) -> bool:
        return mot.split()[0].lower() in FEMININS if mot else False

    def de(self, x: str) -> str:
        return ("d'" + x) if _elide(x) else ("de " + x)

    def du(self, x: str) -> str:
        if _elide(x):
            return "de l'" + x
        return ("de la " if self._fem(x) else "du ") + x

    def au(self, x: str) -> str:
        if _elide(x):
            return "à l'" + x
        return ("à la " if self._fem(x) else "au ") + x

    def le(self, x: str) -> str:
        if _elide(x):
            return "l'" + x
        return ("la " if self._fem(x) else "le ") + x

    def un(self, x: str) -> str:
        return "une" if self._fem(x) else "un"

    @staticmethod
    def maj(x: str) -> str:
        return x[:1].upper() + x[1:]

    # -- morceaux de phrases ---------------------------------------------------------------
    @property
    def type_mot(self) -> str:
        return self.type if self.type in ("stage", "alternance") else (self.nom_fr or "emploi")

    @property
    def intitule_fr(self) -> str:                      # « stage de fin d'études (PFE) »
        return f"{self.nom_fr} ({self.sigle})" if self.sigle else self.nom_fr

    @property
    def intitule_en(self) -> str:                      # « end-of-studies internship (PFE) »
        return f"{self.nom_en} ({self.sigle})" if self.sigle else self.nom_en

    @property
    def court_fr(self) -> str:                         # « stage PFE »
        return f"{self.type_mot} {self.sigle}" if self.sigle else self.nom_fr

    @property
    def de_duree(self) -> str:                         # « de 6 mois »
        return f" de {self.duree_fr}" if self.duree_fr else ""

    @property
    def dispo_fr(self) -> str:                         # « à partir du 1er mars 2027 »
        return f"à partir du {self.date_fr}" if self.date_fr else "(disponible dès que possible)"

    @property
    def dispo_en(self) -> str:
        return f"starting {self.date_en}" if self.date_en else "(available immediately)"

    @property
    def formule_fr(self) -> str:        # « stage de fin d'études (PFE) de 6 mois à partir du 1er mars 2027 »
        return f"{self.intitule_fr}{self.de_duree} {self.dispo_fr}"

    @property
    def formule_inverse_fr(self) -> str:  # « stage PFE (stage de fin d'études) de 6 mois à partir du … »
        base = f"{self.court_fr} ({self.nom_fr})" if self.sigle else self.nom_fr
        return f"{base}{self.de_duree} {self.dispo_fr}"

    @property
    def formule_en(self) -> str:        # « 6-month end-of-studies internship (PFE) starting March 1, 2027 »
        adj = f"{self.duree_adj_en} " if self.duree_en and self.duree_adj_en else ""
        return f"{adj}{self.intitule_en} {self.dispo_en}"

    @property
    def libelle_court_fr(self) -> str:  # « stage PFE de 6 mois »
        return f"{self.court_fr}{self.de_duree}"

    @property
    def objet_fr(self) -> str:          # « Stage PFE de 6 mois »
        return self.maj(self.libelle_court_fr)

    @property
    def objet_en(self) -> str:          # « 6-month end-of-studies internship (PFE) »
        adj = f"{self.duree_adj_en} " if self.duree_en and self.duree_adj_en else ""
        return f"{adj}{self.intitule_en}"

    def article_en(self, x: str) -> str:
        return "an" if x[:1].lower() in "aeiou" else "a"

    def interesse(self) -> str:
        return "intéressée" if self.femme else "intéressé"

    # -- phrases utilisées par le code ----------------------------------------------------
    def phrase_objectif(self, anglais: bool) -> str:
        if anglais:
            return f"I am applying for {self.article_en(self.formule_en)} {self.formule_en}."
        return f"Je vous adresse ma candidature pour {self.un(self.nom_fr)} {self.formule_fr}."

    def dispo_objet(self, anglais: bool) -> str:
        """Disponibilité à écrire dans un objet imposé par l'offre ("" = rien à corriger)."""
        if not self.date_fr:
            return ""
        return f"Available from {self.date_en}" if anglais else f"Dispo {self.dispo_fr}"

    def regex_duree(self, lettre: bool = False) -> re.Pattern | None:
        m = re.match(r"\s*(\d+)\s*([^\s\d]+)", self.duree_fr)
        if not m:
            return None
        n, unite = int(m.group(1)), m.group(2).lower()
        fr, en = NOMBRES.get(n, ("", ""))
        nombres = "|".join(dict.fromkeys(x for x in (str(n), fr, en) if x))
        u_fr, u_en = UNITES.get(unite, (re.escape(unite), ""))
        if lettre:   # contrôle de la lettre LaTeX (documents.py) : même forme qu'à l'origine
            return re.compile(rf"\b({nombres})(~|\s|\\,)*({u_fr}|{u_en or u_fr})\b", re.IGNORECASE)
        return re.compile(rf"({nombres})[\s-]*({u_fr.replace('?', '') if u_fr == 'mois' else u_fr}"
                          rf"|{(u_en or u_fr).replace('s?', '')})", re.IGNORECASE)

    def regex_type(self) -> re.Pattern | None:
        motifs = []
        if self.sigle:
            motifs.append(re.escape(self.sigle))
            motifs += SYNONYMES.get(self.sigle.upper(), [])
        for nom in (self.nom_fr, self.nom_en):
            if nom:
                motifs.append(r"[\s\-'’.]*".join(re.escape(w) for w in re.split(r"[\s\-'’]+", nom) if w))
        return re.compile("|".join(motifs), re.IGNORECASE) if motifs else None

    def objectif_present(self, texte: str) -> bool:
        """L'email mentionne-t-il bien la nature ET la durée de ce qui est recherché ?"""
        for motif in (self.regex_duree(), self.regex_type()):
            if motif is not None and not motif.search(texte):
                return False
        return True

    def contrat_accepte(self, type_contrat: str) -> bool:
        return str(type_contrat or "").lower() in set(self.contrats) | {"inconnu"}

    @property
    def verifie_duree(self) -> bool:
        return bool(self.duree_fr)

    # -- repères [[NOM]] des consignes -----------------------------------------------------
    def reperes(self) -> dict[str, str]:
        rep = (self.profil.get("reponses_standard") or {})
        ident = (self.profil.get("identite") or {})
        t = self.type_mot
        # Autorisation de travail (règle 8 septies du formulaire)
        v_ue = str(rep.get("autorisation_travail_france_ou_ue_oui_non") or "Non")
        if v_ue.lower().startswith("non") and self.type == "stage" and self.conventionne:
            aut_ue = f"« {v_ue} » : il faut l'accord de l'entreprise, via la convention de stage et le visa « stagiaire »"
        else:
            aut_ue = f"« {v_ue} »"
        v_pays = str(rep.get("autorisation_travail_maroc_oui_non") or "Oui")
        commencer = (f"« Non » (disponible {self.dispo_fr})" if self.date_fr else "« Oui »")
        # Invariants du CV (consigne CV, mode LaTeX)
        invariants = []
        if self.duree_fr:
            invariants.append(f"la durée ({self.duree_fr})")
        if self.sigle or self.conventionne:
            nature = " ".join(x for x in (self.sigle, "conventionné" if self.conventionne else "") if x)
            invariants.append(f"la nature {self.du(t)} ({nature})")
        else:
            invariants.append("le type de contrat recherché")
        invariants.append("la date de disponibilité")
        cv_invariants = ("Ne change jamais " + ", ".join(invariants[:-1]) + " ni " + invariants[-1] + ".")
        # Extraction : compatibilité de durée
        if self.duree_fr and self.type == "stage":
            extraction = (f"false seulement si l'offre exclut explicitement {self.un(t)} {t} de {self.duree_fr} "
                          f"(ex. \"stage de 2 mois\" ou \"stage de 3 mois uniquement\")")
        elif self.duree_fr:
            extraction = f"false seulement si l'offre exclut explicitement {self.un(t)} {t} de {self.duree_fr}"
        else:
            extraction = "laisse toujours true (aucune durée n'est recherchée)"
        # Lettre de motivation
        if self.duree_fr:
            lm_r4 = (f"R4. {self.maj(self.le(t))} est {self.un(self.nom_fr)} « {self.intitule_fr} d'une durée de "
                     f"{self.duree_fr} ». Mentionne explicitement « {self.duree_fr} » (ou « {self.duree_en} » si "
                     f"la lettre est en anglais). Ne propose jamais une autre durée. Si <profil> donne une date "
                     f"de début, cite-la ; sinon n'en invente pas.")
            lm_check = f"« {self.duree_fr} » est-il mentionné ?"
            pendant = f"pendant les {self.duree_fr}"
        else:
            lm_r4 = (f"R4. Le candidat cherche : « {self.intitule_fr} ». Ne propose jamais autre chose. Si "
                     f"<profil> donne une date de début, cite-la ; sinon n'en invente pas.")
            lm_check = "L'objectif recherché est-il respecté ?"
            pendant = "dans ce poste"
        convention = " et convention de stage" if (self.type == "stage" and self.conventionne) else ""
        lm_point3 = (f"Ce que le candidat vient chercher et ce qu'il pourra accomplir {pendant} ; "
                     f"disponibilité{convention}.")
        duree_champ = ("type de contrat" if self.type not in ("stage", "alternance") else f"durée {self.du(t)}")
        d_un = ("d'une " if self.femme else "d'un ") + self.statut_fr
        return {
            "EMAIL": self.email,
            "DE_TYPE": self.de(t),
            "DUREE_DU_TYPE": duree_champ,
            "AUTORISATION_FR_UE": aut_ue,
            "PAYS": str(ident.get("pays") or ""),
            "AUTORISATION_PAYS": f"« {v_pays} »",
            "COMMENCER_IMMEDIATEMENT": commencer,
            "D_UN_STATUT": d_un,
            "DU_LIBELLE_COURT": self.du(self.libelle_court_fr),
            "CV_INVARIANTS": cv_invariants,
            "EXTRACTION_DUREE": extraction,
            "LM_OFFRE": "offre " + self.de(f"{self.intitule_fr}{self.de_duree}"),
            "LM_R4": lm_r4,
            "OBJET_LM": self.maj(f"{self.intitule_fr}{self.de_duree}"),
            "LM_POINT3": lm_point3,
            "LM_CHECK_DUREE": lm_check,
        }

    def remplir(self, texte: str) -> str:
        """Remplit les repères [[NOM]] d'une consigne. Un repère inconnu est une erreur."""
        valeurs = self.reperes()

        def remplace(m: re.Match) -> str:
            cle = m.group(1)
            if cle not in valeurs:
                raise KeyError(f"Repère inconnu dans une consigne : [[{cle}]]")
            return valeurs[cle]
        return re.sub(r"\[\[([A-Z0-9_]+)\]\]", remplace, texte)


def charger_objectif() -> dict:
    fichier = chemin_projet("config/objectif.yaml")
    if not fichier.exists():
        raise FileNotFoundError("config/objectif.yaml absent : lance FORMULAIRE.bat (objectif de recherche).")
    return yaml.safe_load(fichier.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def courant() -> Objectif:
    """Objectif du candidat de CE projet (profil + objectif), lu une fois."""
    profil = yaml.safe_load(chemin_projet("config/profile.yaml").read_text(encoding="utf-8")) or {}
    return Objectif(profil)
