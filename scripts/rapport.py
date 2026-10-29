"""
Rapport quotidien : ce qui a été envoyé, ce qui a échoué, et la courte liste des
offres qui attendent une action de ta part (avec le lien et la raison).

Le rapport est toujours enregistré dans rapports/rapport_AAAA-MM-JJ.txt et, si
l'envoi d'email est configuré (config.yaml -> email + rapport), envoyé par email.

    python scripts/rapport.py            # génère (et envoie) le rapport du jour maintenant
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import excel_manager as em
from envoi_email import envoyer_email_simple, smtp_configure
from memoire import Memoire
from utils import chemin_projet


def construire_rapport(lignes: list[dict], memoire: Memoire, max_tentatives: int,
                       jour: str | None = None) -> tuple[str, str]:
    """Renvoie (sujet, texte) du rapport pour le jour donné (AAAA-MM-JJ, défaut : aujourd'hui)."""
    jour = jour or datetime.now().strftime("%Y-%m-%d")
    du_jour = [d for d in lignes if str(d["Date_Traitement"]).startswith(jour)]

    def statut(d: dict) -> str:
        return str(d["Statut"]).strip() or em.A_TRAITER

    postulees = [d for d in du_jour if statut(d) == em.POSTULE]
    tests = [d for d in du_jour if statut(d) == em.PRET_TEST]
    manuelles = [d for d in lignes if statut(d) == em.ACTION_MANUELLE]           # toutes celles en attente
    erreurs = [d for d in lignes if statut(d) == em.ERREUR
               and str(d["Tentatives"]).isdigit() and int(d["Tentatives"]) >= max_tentatives]
    en_attente = [d for d in lignes if statut(d) in (em.A_TRAITER, em.ERREUR)
                  and d not in erreurs]
    total_postulees = sum(1 for d in lignes if statut(d) == em.POSTULE)

    def ligne(d: dict, avec_raison: bool = True) -> str:
        nom = str(d["Nom_Entreprise"]).strip() or "(entreprise inconnue)"
        texte = f"  - {nom} — {d['Lien_Offre'] or d['Email']}"
        if avec_raison and str(d["Commentaire"]).strip():
            texte += f"\n      -> {str(d['Commentaire']).strip()[:300]}"
        return texte

    date_fr = datetime.strptime(jour, "%Y-%m-%d").strftime("%d/%m/%Y")
    sujet = (f"[Candidatures PFE] {date_fr} : {len(postulees)} envoyée(s), "
             f"{len(manuelles)} action(s) manuelle(s)")
    blocs = [
        f"RAPPORT DU {date_fr}",
        "=" * 40,
        f"Envoyées aujourd'hui : {len(postulees)}   |   Total envoyées : {total_postulees}",
        f"Tests (non envoyées) aujourd'hui : {len(tests)}",
        f"En attente de traitement : {len(en_attente)}",
        "",
    ]
    if manuelles:
        blocs += [f"À FAIRE PAR TOI ({len(manuelles)}) — le robot ne peut pas finir seul :"]
        blocs += [ligne(d) for d in manuelles]
        blocs += ["  (Une fois réglé : mets la ligne en « Postulé » si tu as postulé toi-même,",
                  "   ou « À traiter » pour que le robot réessaie.)", ""]
    if postulees:
        blocs += ["CANDIDATURES ENVOYÉES AUJOURD'HUI :"] + [ligne(d, False) for d in postulees] + [""]
    if erreurs:
        blocs += ["ABANDONNÉES APRÈS PLUSIEURS ESSAIS :"] + [ligne(d) for d in erreurs] + [""]

    apprises = [f"  - {site} : {lecon}" for site, lecon in memoire.lecons_du_jour(jour)]
    if apprises:
        blocs += ["CE QUE LE ROBOT A APPRIS AUJOURD'HUI :"] + apprises + [""]

    # Progression du robot par site : taux de réussite (le robot devient-il meilleur ?)
    stats = memoire.statistiques()
    if stats:
        blocs += ["PROGRESSION DU ROBOT PAR SITE (taux de réussite) :"]
        for site in sorted(stats, key=lambda s: -stats[s]["tentatives"]):
            s = stats[site]
            taux = round(100 * s["succes"] / s["tentatives"]) if s["tentatives"] else 0
            blocs.append(f"  - {site} : {s['succes']}/{s['tentatives']} réussies ({taux} %), "
                         f"{s['lecons']} leçon(s) apprise(s)")
        blocs += [""]

    return sujet, "\n".join(blocs)


def produire_rapport(config: dict, profil: dict, excel: em.ExcelManager, memoire: Memoire,
                     logger: logging.Logger, jour: str | None = None) -> Path:
    """Écrit le rapport dans rapports/ et l'envoie par email si c'est configuré. Renvoie le fichier."""
    cfg = config.get("rapport", {})
    max_t = int(config.get("candidature", {}).get("max_tentatives_par_offre", 2))
    sujet, texte = construire_rapport(excel.lire_toutes(), memoire, max_t, jour)

    dossier = chemin_projet(cfg.get("dossier", "rapports"))
    dossier.mkdir(parents=True, exist_ok=True)
    fichier = dossier / f"rapport_{jour or datetime.now().strftime('%Y-%m-%d')}.txt"
    fichier.write_text(f"{sujet}\n\n{texte}\n", encoding="utf-8")
    logger.info(f"Rapport enregistré : {fichier}")

    if cfg.get("envoyer_par_email", True):
        destinataire = cfg.get("destinataire") or (profil.get("identite") or {}).get("email")
        if not smtp_configure(config):
            logger.info("Rapport non envoyé par email : email.utilisateur / email.mot_de_passe non configurés.")
        elif destinataire:
            try:
                envoyer_email_simple(config, destinataire, sujet, texte)
                logger.info(f"Rapport envoyé à {destinataire}.")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Envoi du rapport impossible : {e}")
    return fichier


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from utils import charger_config, configurer_logs

    config, profil = charger_config()
    logger = configurer_logs(chemin_projet(config["chemins"]["logs"]))
    excel = em.ExcelManager(chemin_projet(config["chemins"]["excel"]), logger)
    memoire = Memoire(chemin_projet(config.get("chemins", {}).get("memoire", "memoire")))
    chemin = produire_rapport(config, profil, excel, memoire, logger)
    print(chemin.read_text(encoding="utf-8"))
