"""
Envoi d'une candidature par email (CV + LM en pièces jointes), pour les offres
qui demandent « envoyez votre candidature à … ». Désactivé par défaut
(config.yaml -> email.actif).
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path


def smtp_configure(config: dict) -> bool:
    cfg = config.get("email", {})
    return bool(cfg.get("utilisateur") and cfg.get("mot_de_passe"))


def envoyer_email_simple(config: dict, destinataire: str, sujet: str, texte: str) -> None:
    """Envoie un email texte (utilisé pour le rapport quotidien). Lève une exception en cas d'échec."""
    cfg = config.get("email", {})
    msg = EmailMessage()
    msg["From"] = cfg["utilisateur"]
    msg["To"] = destinataire
    msg["Subject"] = sujet
    msg.set_content(texte)
    with smtplib.SMTP(cfg.get("smtp_serveur", "smtp.gmail.com"), int(cfg.get("smtp_port", 587)), timeout=60) as smtp:
        smtp.starttls()
        smtp.login(cfg["utilisateur"], cfg["mot_de_passe"])
        smtp.send_message(msg)


def envoyer_candidature(config: dict, profil: dict, destinataire: str, offre: dict,
                        cv_pdf: Path, lm_pdf: Path) -> None:
    """Envoie l'email ; lève une exception en cas d'échec."""
    cfg = config.get("email", {})
    ident = profil.get("identite", {}) or {}
    prenom_nom = f"{ident.get('prenom', '')} {ident.get('nom', '')}".strip()
    poste = offre.get("titre_poste") or "Stage de fin d'études"
    anglais = offre.get("langue_offre") == "en"

    msg = EmailMessage()
    msg["From"] = f"{prenom_nom} <{cfg['utilisateur']}>"
    msg["To"] = destinataire
    if anglais:
        msg["Subject"] = f"Application — 6-month end-of-studies internship — {poste}"
        msg.set_content(
            f"Dear Hiring Team,\n\n"
            f"Please find attached my application (CV and cover letter) for the position « {poste} » "
            f"at {offre.get('nom_entreprise', 'your company')}, as a 6-month end-of-studies internship "
            f"(engineering degree).\n\n"
            f"I would be glad to discuss my application with you.\n\n"
            f"Best regards,\n{prenom_nom}\n{ident.get('telephone', '')}\n"
        )
    else:
        msg["Subject"] = f"Candidature — Stage PFE 6 mois — {poste}"
        msg.set_content(
            f"Madame, Monsieur,\n\n"
            f"Je vous prie de trouver ci-joint ma candidature (CV et lettre de motivation) pour le poste "
            f"« {poste} » au sein de {offre.get('nom_entreprise', 'votre entreprise')}, dans le cadre de mon "
            f"stage de fin d'études (PFE) d'une durée de 6 mois.\n\n"
            f"Je me tiens à votre disposition pour un entretien.\n\n"
            f"Cordialement,\n{prenom_nom}\n{ident.get('telephone', '')}\n"
        )
    for pdf in (cv_pdf, lm_pdf):
        msg.add_attachment(Path(pdf).read_bytes(), maintype="application", subtype="pdf",
                           filename=Path(pdf).name)

    with smtplib.SMTP(cfg.get("smtp_serveur", "smtp.gmail.com"), int(cfg.get("smtp_port", 587)), timeout=60) as smtp:
        smtp.starttls()
        smtp.login(cfg["utilisateur"], cfg["mot_de_passe"])
        smtp.send_message(msg)
