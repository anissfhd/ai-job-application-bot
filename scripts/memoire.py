"""
Mémoire du robot (dossier memoire/) :
  - lecons_sites.json : ce que le robot a appris sur chaque site (LinkedIn, Workday,
    Welcome to the Jungle…). Relu avant chaque candidature sur le même site, pour
    qu'il s'améliore tout seul au fil du temps.
  - etat.json : petites informations persistantes (date du dernier rapport…).

Les fichiers sont lisibles et modifiables à la main (JSON). Pour faire « oublier »
un site au robot, supprime simplement sa section dans lecons_sites.json.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from utils import normaliser_espaces

# Domaines à 2 niveaux (co.uk…) : on garde alors 3 étiquettes
SUFFIXES_DOUBLES = ("co.uk", "com.au", "co.ma", "com.tn", "co.jp")


def domaine(url: str) -> str:
    """https://fr.linkedin.com/jobs/… -> linkedin.com ; acme.wd3.myworkdayjobs.com -> myworkdayjobs.com"""
    hote = (urlparse(url).hostname or "").lower()
    morceaux = hote.split(".")
    if len(morceaux) >= 3 and ".".join(morceaux[-2:]) in SUFFIXES_DOUBLES:
        return ".".join(morceaux[-3:])
    return ".".join(morceaux[-2:]) if len(morceaux) >= 2 else hote


def _nettoyer_lecon(lecon: str) -> str:
    lecon = normaliser_espaces(str(lecon or ""))
    lecon = re.sub(r"\S+@\S+", "[email]", lecon)            # jamais d'email dans une leçon
    lecon = re.sub(r"\+?\d[\d .-]{7,}\d", "[numéro]", lecon)  # ni de numéro de téléphone
    return lecon[:500]


class Memoire:
    def __init__(self, dossier: Path, max_lecons_par_site: int = 25):
        self.dossier = Path(dossier)
        self.dossier.mkdir(parents=True, exist_ok=True)
        self.fichier_lecons = self.dossier / "lecons_sites.json"
        self.fichier_etat = self.dossier / "etat.json"
        self.max_lecons = max_lecons_par_site
        self.fichier_regles = self.dossier / "regles_sites.json"
        self.fichier_playbooks = self.dossier / "playbooks.json"

    # -- lecture / écriture JSON tolérante ------------------------------------

    @staticmethod
    def _lire(fichier: Path) -> dict:
        try:
            return json.loads(fichier.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    @staticmethod
    def _ecrire(fichier: Path, donnees: dict) -> None:
        temporaire = fichier.with_suffix(".tmp")
        temporaire.write_text(json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8")
        temporaire.replace(fichier)   # écriture atomique : jamais de fichier à moitié écrit

    # -- leçons par site -------------------------------------------------------

    def lecons(self, url: str) -> list[str]:
        """Leçons connues pour le site de cette URL (les plus récentes en dernier)."""
        site = self._lire(self.fichier_lecons).get(domaine(url), {})
        return [l["texte"] for l in site.get("lecons", [])]

    def enregistrer(self, url: str, lecon: str, statut: str) -> bool:
        """Ajoute une leçon (si nouvelle) et met à jour les statistiques du site. Renvoie True si ajoutée."""
        donnees = self._lire(self.fichier_lecons)
        site = donnees.setdefault(domaine(url), {"lecons": [], "tentatives": 0, "succes": 0})
        site["tentatives"] = site.get("tentatives", 0) + 1
        if statut == "Postulé":
            site["succes"] = site.get("succes", 0) + 1

        ajoutee = False
        lecon = _nettoyer_lecon(lecon)
        if len(lecon) >= 15:
            deja = {l["texte"].lower() for l in site["lecons"]}
            if lecon.lower() not in deja:
                site["lecons"].append({"texte": lecon, "date": datetime.now().strftime("%Y-%m-%d"),
                                       "issue_de": statut})
                site["lecons"] = site["lecons"][-self.max_lecons:]
                ajoutee = True
        self._ecrire(self.fichier_lecons, donnees)
        return ajoutee

    def lecons_du_jour(self, jour: str) -> list[tuple[str, str]]:
        """[(site, leçon)] apprises à la date AAAA-MM-JJ."""
        return [(site, l["texte"])
                for site, infos in self._lire(self.fichier_lecons).items()
                for l in infos.get("lecons", []) if l.get("date") == jour]

    def statistiques(self) -> dict:
        return {d: {"tentatives": s.get("tentatives", 0), "succes": s.get("succes", 0),
                    "lecons": len(s.get("lecons", []))}
                for d, s in self._lire(self.fichier_lecons).items()}

    # -- règles EXÉCUTABLES par site (appliquées par le code, pas relues par le LLM) --------
    # Une leçon texte peut être ignorée par le LLM ; une règle est du JSON que le CODE
    # applique (ex. « sur indeed.com, cocher la case de consentement de la candidature »).

    def regles(self, url: str) -> dict:
        """Règles connues pour le site de cette URL ({cle: valeur})."""
        return dict(self._lire(self.fichier_regles).get(domaine(url), {}))

    def enregistrer_regle(self, url: str, cle: str, valeur) -> bool:
        """Ajoute ou met à jour une règle pour ce site. Renvoie True si nouvelle/modifiée."""
        if not cle:
            return False
        donnees = self._lire(self.fichier_regles)
        site = donnees.setdefault(domaine(url), {})
        if site.get(cle) == valeur:
            return False
        site[cle] = valeur
        self._ecrire(self.fichier_regles, donnees)
        return True

    # -- playbooks : séquence d'actions qui a RÉUSSI sur un site, rejouée sans LLM ----------

    def playbook(self, url: str) -> list[dict]:
        """Séquence d'actions mémorisée pour ce site ([] si inconnue ou trop peu fiable)."""
        return list(self._lire(self.fichier_playbooks).get(domaine(url), {}).get("actions", []))

    def enregistrer_playbook(self, url: str, actions: list[dict]) -> None:
        """Mémorise (ou renforce) la séquence d'actions qui vient de réussir sur ce site."""
        donnees = self._lire(self.fichier_playbooks)
        site = donnees.setdefault(domaine(url), {"actions": [], "succes": 0, "echecs": 0})
        # Ne réécrit la séquence que si on n'en a pas encore (la première qui marche fait foi)
        if actions and not site.get("actions"):
            site["actions"] = actions
        site["succes"] = site.get("succes", 0) + 1
        self._ecrire(self.fichier_playbooks, donnees)

    def playbook_echec(self, url: str) -> None:
        """Une relecture du playbook a échoué : après 2 échecs, on l'oublie (le site a changé)."""
        donnees = self._lire(self.fichier_playbooks)
        site = donnees.get(domaine(url))
        if not site:
            return
        site["echecs"] = site.get("echecs", 0) + 1
        if site["echecs"] >= 2:
            site["actions"] = []          # playbook périmé : on le vide
            site["echecs"] = 0
        self._ecrire(self.fichier_playbooks, donnees)

    # -- état persistant -------------------------------------------------------

    def lire_etat(self, cle: str, defaut=None):
        return self._lire(self.fichier_etat).get(cle, defaut)

    def ecrire_etat(self, cle: str, valeur) -> None:
        etat = self._lire(self.fichier_etat)
        etat[cle] = valeur
        self._ecrire(self.fichier_etat, etat)
