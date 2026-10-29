"""
Confidentialité : DeepSeek ne voit JAMAIS le prénom, le nom, l'email, le téléphone ni l'adresse
du candidat — même principe que le mot de passe ({MOT_DE_PASSE}).

  - Tout texte ENVOYÉ à DeepSeek passe par `masquer()` : chaque apparition d'une de ces valeurs
    (majuscules, dans un lien, un nom de fichier, encodée dans une URL…) devient un repère :
    {PRENOM}, {NOM}, {EMAIL}, {IDENTIFIANT}, {TELEPHONE}, {TELEPHONE_SANS_INDICATIF},
    {TELEPHONE_AVEC_INDICATIF}, {ADRESSE}, {CODE_POSTAL}.
  - Toute réponse de DeepSeek passe par `demasquer()` avant usage : quand il décide de taper
    {EMAIL} dans un champ, le robot tape le vrai email. DeepSeek décide donc exactement comme
    avant ; il manipule seulement des repères au lieu des vraies valeurs.
  - Les captures d'écran envoyées sont FLOUTÉES aux endroits où ces valeurs apparaissent
    (`motifs_js()`, utilisé par navigateur.capture_annotee) — SAUF le numéro de téléphone
    (choix du 27/09) : DeepSeek doit voir un indicatif faux ou en double pour le corriger.

Restent visibles, comme voulu : ville, pays, nationalité, date de naissance, formation.
Le mot de passe garde son propre mécanisme (candidature.py), inchangé.
"""
from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import quote, quote_plus

import yaml

from utils import chemin_projet

LETTRE = r"A-Za-zÀ-ÖØ-öø-ÿ0-9"          # « mot » : lettres (accents compris) et chiffres, PAS « _ »


def _chiffres(texte) -> str:
    return re.sub(r"\D", "", str(texte or ""))


class Confidentialite:
    def __init__(self, profil: dict):
        ident = (profil or {}).get("identite", {}) or {}
        rep = (profil or {}).get("reponses_standard", {}) or {}
        self.valeurs: dict[str, str] = {}
        for jeton, valeur in (("EMAIL", ident.get("email")), ("PRENOM", ident.get("prenom")),
                              ("NOM", ident.get("nom")), ("TELEPHONE", ident.get("telephone")),
                              ("TELEPHONE_SANS_INDICATIF", rep.get("telephone_sans_indicatif")),
                              ("TELEPHONE_AVEC_INDICATIF", rep.get("telephone_avec_indicatif")),
                              ("ADRESSE", ident.get("adresse")), ("CODE_POSTAL", ident.get("code_postal"))):
            v = str(valeur or "").strip()
            if v:
                self.valeurs[jeton] = v
        email = self.valeurs.get("EMAIL", "")
        ident_email = email.split("@")[0] if "@" in email else ""
        noms = {self.valeurs.get("PRENOM", "").lower(), self.valeurs.get("NOM", "").lower()}
        if len(ident_email) >= 5 and ident_email.lower() not in noms:
            self.valeurs["IDENTIFIANT"] = ident_email          # « prenom.nom42 » (GitHub, liens…)
        self._regles = self._construire(ident)

    # -- motifs --------------------------------------------------------------------------
    def _mot(self, valeur: str) -> str:
        """La valeur entière, insensible à la casse, jamais au milieu d'un autre mot."""
        corps = r"[\s\-]*".join(re.escape(p) for p in re.split(r"[\s\-]+", valeur) if p)
        # Précédé d'un non-mot, ou d'un caractère encodé d'URL (« %0A », « %20 »…).
        return rf"(?:(?<![{LETTRE}])|(?<=%[0-9A-Fa-f]{{2}})){corps}(?![{LETTRE}])"

    def _construire(self, ident: dict) -> list[tuple[re.Pattern, str]]:
        regles: list[tuple[str, str]] = []

        def ajouter(motif: str, jeton: str) -> None:
            regles.append((motif, jeton))

        v = self.valeurs
        # 1) Email complet (et ses formes encodées dans une URL), avant le prénom / nom
        if "EMAIL" in v:
            for forme in dict.fromkeys((v["EMAIL"], quote(v["EMAIL"]), quote_plus(v["EMAIL"]),
                                        quote(v["EMAIL"], safe=""))):
                ajouter(re.escape(forme), "{EMAIL}")
        # 2) Téléphone : le numéro national, quels que soient les séparateurs et l'indicatif
        national = _chiffres(v.get("TELEPHONE_SANS_INDICATIF")) or _chiffres(v.get("TELEPHONE"))[-9:]
        national = national.lstrip("0")
        if len(national) >= 6:
            complet = _chiffres(v.get("TELEPHONE_AVEC_INDICATIF")) or _chiffres(v.get("TELEPHONE"))
            indicatif = complet[: len(complet) - len(national)] if complet.endswith(national) else ""
            sep = r"[\s.\-()/]*"
            num = sep.join(re.escape(c) for c in national)
            if indicatif:
                ajouter(rf"(?<!\d)(?:\+|%2B|00)?{sep.join(indicatif)}{sep}(?:\(0\){sep})?{num}(?!\d)", "{TELEPHONE}")
            ajouter(rf"(?<!\d)(?:0{sep})?{num}(?!\d)", "{TELEPHONE_SANS_INDICATIF}")
        # 3) Adresse complète, puis ses morceaux (sauf la ville et le pays, qui restent visibles)
        if "ADRESSE" in v:
            ajouter(self._mot(v["ADRESSE"]), "{ADRESSE}")
            publics = {str(ident.get("ville") or "").lower(), str(ident.get("pays") or "").lower()}
            for morceau in re.split(r"\s*,\s*", v["ADRESSE"]):
                if len(morceau) >= 5 and morceau.lower() not in publics:
                    ajouter(self._mot(morceau), "{ADRESSE}")
        if "CODE_POSTAL" in v and len(v["CODE_POSTAL"]) >= 4:
            ajouter(rf"(?<![\d]){re.escape(v['CODE_POSTAL'])}(?![\d])", "{CODE_POSTAL}")
        # 4) Identifiant de l'email, puis nom et prénom (majuscules, liens, noms de fichiers)
        if "IDENTIFIANT" in v:
            ajouter(self._mot(v["IDENTIFIANT"]), "{IDENTIFIANT}")
        for jeton in ("NOM", "PRENOM"):
            if len(v.get(jeton, "")) >= 3:
                ajouter(self._mot(v[jeton]), "{" + jeton + "}")
                encode = quote(v[jeton])
                if encode != v[jeton]:
                    ajouter(re.escape(encode), "{" + jeton + "}")
        return [(re.compile(m, re.IGNORECASE), j) for m, j in regles]

    # -- usage ---------------------------------------------------------------------------
    def masquer(self, texte: str) -> str:
        """Texte sûr pour DeepSeek : aucune donnée personnelle, des repères à la place."""
        if not texte or not self._regles:
            return texte
        for motif, jeton in self._regles:
            texte = motif.sub(jeton, texte)
        return texte

    def demasquer(self, texte: str) -> str:
        """Repères remis en vraies valeurs (juste avant de taper / d'enregistrer)."""
        if not texte or "{" not in texte:
            return texte
        for jeton, valeur in self.valeurs.items():
            texte = texte.replace("{" + jeton + "}", valeur)
        return texte

    def demasquer_tout(self, objet):
        """Même chose dans une réponse structurée (dict / liste / tuple de textes)."""
        if isinstance(objet, str):
            return self.demasquer(objet)
        if isinstance(objet, dict):
            return {k: self.demasquer_tout(v) for k, v in objet.items()}
        if isinstance(objet, (list, tuple)):
            return type(objet)(self.demasquer_tout(v) for v in objet)
        return objet

    def motifs_js(self, telephone: bool = True) -> list[str]:
        """
        Motifs (syntaxe JavaScript) des zones à FLOUTER sur les captures d'écran.
        telephone=False : le numéro de téléphone reste LISIBLE sur l'image. Flouté, il cachait
        aussi l'indicatif tapé dans le même champ : DeepSeek ne pouvait pas voir un numéro mal
        formé (« +33 » + « +212… », vu le 27/09) et tournait en rond. Le TEXTE, lui, reste masqué.
        """
        res = []
        for motif, jeton in self._regles:
            if not telephone and jeton.startswith("{TELEPHONE"):
                continue
            # (?<!…) est accepté par les navigateurs récents (Chrome), on le garde tel quel.
            res.append(motif.pattern)
        return res


@lru_cache(maxsize=1)
def courante() -> Confidentialite:
    """Confidentialité du candidat de CE projet (config/profile.yaml), lue une fois."""
    fichier = chemin_projet("config/profile.yaml")
    profil = yaml.safe_load(fichier.read_text(encoding="utf-8")) if fichier.exists() else {}
    return Confidentialite(profil or {})


def masquer(texte: str) -> str:
    return courante().masquer(texte)


def demasquer(texte: str) -> str:
    return courante().demasquer(texte)
