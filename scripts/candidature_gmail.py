"""
Candidature PAR EMAIL, via Gmail dans le navigateur du robot.

Utilisée quand l'offre donne une VRAIE adresse de candidature (jamais une adresse devinée) :
- offre « envoyez votre candidature à … » ;
- ou formulaire en échec (« Action manuelle ») alors que l'offre contient une adresse.

1. DeepSeek (la conversation qui a rédigé le CV et la lettre, donc qui connaît l'offre) écrit
   l'objet et UN paragraphe d'intérêt pour le poste — objectif conservé : stage PFE de 6 mois.
   Le robot ajoute la formule d'appel, la fin (pièces jointes, politesse) et la signature.
2. Le robot ouvre la fenêtre « Nouveau message » de Gmail déjà remplie (destinataire, objet,
   texte), puis la MÊME logique que pour un formulaire : captures d'écran, DeepSeek indique où
   cliquer, le CV et la lettre sont joints, et le verrou interdit l'envoi sans le CV.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from objectif import courant as objectif_courant

# L'objectif (config/objectif.yaml : nature, durée, date de début) ne doit jamais se perdre dans
# la rédaction : il est contrôlé par objectif.objectif_present().


def _anglais(offre: dict) -> bool:
    return str(offre.get("langue_offre", "fr")).lower().startswith("en")


def cloture_et_signature(offre: dict, profil: dict) -> str:
    """Fin d'email FIXE (jamais écrite par DeepSeek) : pièces jointes, politesse, nom + email."""
    ident = profil.get("identite", {}) or {}
    signature = f"{ident.get('prenom', '')} {ident.get('nom', '')}".strip() + f"\n{ident.get('email', '')}"
    if _anglais(offre):
        return ("Please find attached my CV and cover letter. I remain at your disposal for an "
                "interview.\n\nKind regards,\n\n" + signature)
    return ("Vous trouverez mon CV et ma lettre de motivation en pièces jointes. Je me tiens à votre "
            "entière disposition pour un entretien et vous prie d'agréer, Madame, Monsieur, "
            "l'expression de mes respectueuses salutations.\n\n" + signature)


def assembler(offre: dict, profil: dict, recruteur: str, paragraphe: str) -> str:
    """Email complet : formule d'appel + LE paragraphe d'intérêt + fin fixe."""
    return (f"{formule_appel(offre, recruteur)}\n\n{paragraphe.strip()}\n\n"
            f"{cloture_et_signature(offre, profil)}")


def message_de_secours(offre: dict, profil: dict, recruteur: str = "") -> tuple[str, str]:
    """Modèle fixe, si DeepSeek ne répond pas : même forme que l'email rédigé."""
    obj = objectif_courant()
    poste = offre.get("titre_poste") or obj.maj(obj.nom_fr)
    entreprise = offre.get("nom_entreprise") or ""
    if _anglais(offre):
        return (f"Application – {obj.objet_en} – {poste}",
                assembler(offre, profil, recruteur,
                          f"I am applying for {obj.article_en(obj.formule_en)} {obj.formule_en}, "
                          f"for the position « {poste} »"
                          f"{' at ' + entreprise if entreprise else ''}."))
    return (f"Candidature – {obj.objet_fr} – {poste}",
            assembler(offre, profil, recruteur,
                      f"Je vous adresse ma candidature à {obj.un(obj.nom_fr)} {obj.formule_fr}, "
                      f"au poste « {poste} »"
                      f"{' au sein de ' + entreprise if entreprise else ''}."))


# Rubrique LinkedIn « Meet the hiring team » / « Rencontrez l'équipe de recrutement » : le nom
# du recruteur est sur la première ligne non vide qui suit.
_EQUIPE_RECRUTEMENT = re.compile(
    r"(?:meet the hiring team|rencontrez l.équipe de recrutement)\s*\n\s*([^\n•]{3,60})", re.IGNORECASE)


def _nom_propre(nom: str) -> str:
    """« Ophélie CHEVALIER » -> « Ophélie Chevalier » (les noms en capitales sont adoucis)."""
    mots = []
    for mot in nom.split():
        if mot.isupper() and len(mot) > 1:
            mot = "-".join(p.capitalize() for p in mot.split("-"))
        mots.append(mot)
    return " ".join(mots)


def recruteur_offre(dossier: Path | None, lien: str = "") -> str:
    """
    Nom du recruteur affiché sur la page de l'offre (texte sauvegardé dans offre_page.txt),
    ou "" s'il n'y en a pas. Si l'offre n'a pas été relue pour cette candidature, on reprend le
    offre_page.txt d'un essai précédent de LA MÊME offre (même lien en première ligne).
    """
    if dossier is None:
        return ""
    ici = Path(dossier) / "offre_page.txt"
    pages = [ici]
    if lien and not ici.exists():
        pages += sorted(Path(dossier).parent.glob("*/offre_page.txt"),
                        key=lambda f: f.stat().st_mtime, reverse=True)
    for page in pages:
        try:
            texte = page.read_text(encoding="utf-8")
        except OSError:
            continue
        if page != ici and texte.split("\n", 1)[0].strip() != lien:
            continue
        m = _EQUIPE_RECRUTEMENT.search(texte)
        if not m:
            return ""
        nom = " ".join(m.group(1).split())
        # Un vrai nom : 2 à 4 mots, sans chiffres (pas « LinkedIn Member », pas un lien).
        if (re.search(r"\d|linkedin|membre|member|http|@", nom, re.IGNORECASE)
                or not 2 <= len(nom.split()) <= 4):
            return ""
        return _nom_propre(nom)
    return ""


def formule_appel(offre: dict, recruteur: str) -> str:
    anglais = str(offre.get("langue_offre", "fr")).lower().startswith("en")
    if recruteur:
        return f"Dear {recruteur}," if anglais else f"Bonjour {recruteur},"
    return "Dear Hiring Team," if anglais else "Madame, Monsieur,"


def demande_redaction(offre: dict, profil: dict, destinataire: str, lettre: str = "",
                      recruteur: str = "") -> str:
    """
    DeepSeek n'écrit QUE l'objet et UN paragraphe d'intérêt pour le poste. La formule d'appel,
    la fin (pièces jointes, politesse) et la signature sont ajoutées par le robot. Pas
    d'expériences ni de chiffres : le CV et la lettre joints s'en chargent déjà.
    (`lettre` n'est plus transmise : elle poussait DeepSeek à recopier les expériences.)
    """
    formation = profil.get("formation", {}) or {}
    anglais = _anglais(offre)
    obj = objectif_courant()
    qui = (f"{obj.statut_long_fr}, {formation.get('specialite', '')}, "
           f"{formation.get('ecole', '')}")
    un = obj.un(obj.nom_fr)
    objet_consigne = ("OBJET : si l'offre impose un objet (« avec pour objet : … »), reprends-le, mais remplace "
                      "toute disponibilité qui n'est pas la mienne (« dès maintenant », « immédiate », « ASAP ») "
                      f"par « {obj.dispo_fr} ».\n" if obj.date_fr else
                      "OBJET : si l'offre impose un objet (« avec pour objet : … »), reprends-le.\n")
    return (
        "EMAIL DE CANDIDATURE — l'email accompagne mon CV et ma lettre de motivation (déjà "
        f"rédigés pour CETTE offre : {offre.get('nom_entreprise', '')} — "
        f"{offre.get('titre_poste', '')}), envoyé à {destinataire}. "
        f"Langue : {'anglais' if anglais else 'français'}.\n"
        f"Extrait de l'offre : {' '.join(str(offre.get('description_offre', '')).split())[:1500]}\n"
        + objet_consigne
        + "Écris UNIQUEMENT l'objet et UN SEUL paragraphe de 2 à 3 phrases, qui dit :\n"
        f"1) que je candidate à {un} {obj.formule_inverse_fr}, au poste et dans l'entreprise de "
        "l'offre (ville si elle est indiquée) ;\n"
        f"2) qui je suis, en une demi-phrase : {qui} ;\n"
        "3) mon intérêt pour l'activité de l'entreprise et pour 2 ou 3 missions précises de l'offre.\n"
        "INTERDIT dans ce paragraphe : mes expériences, stages, projets, entreprises passées, "
        "chiffres ou pourcentages (le CV et la lettre joints s'en chargent) ; toute formule "
        "d'appel (« Madame, Monsieur », « Bonjour »), toute formule de politesse et toute "
        "signature (le robot les ajoute lui-même).\n"
        f"Exemple de ton et de longueur : « Je vous adresse ma candidature à {un} "
        f"{obj.formule_inverse_fr}, au poste de Stagiaire Dealing au sein de votre équipe à Paris. "
        f"{obj.presentation_fr}, je suis particulièrement {obj.interesse()} par votre activité en "
        "produits structurés et par les missions de pricing, de suivi des valorisations et de "
        "création de trade ideas. »\n"
        'Réponds avec UN SEUL bloc ```json {"objet": "...", "paragraphe": "..."} et rien d\'autre.')


# Lignes qui ne sont pas le paragraphe : formule d'appel, politesse, signature, coordonnées.
_HORS_PARAGRAPHE = re.compile(
    r"^(madame|monsieur|bonjour|dear|hello|cordialement|bien cordialement|best regards|kind "
    r"regards|regards|sincerely|vous trouverez|please find|je me tiens|"
    + (re.escape(objectif_courant().prenom.lower()) + "|" if objectif_courant().prenom else "")
    + r"\+?\d[\d\s]{6,}|\S+@\S+)", re.IGNORECASE)
# Signes d'un paragraphe qui raconte le CV au lieu de dire l'intérêt pour le poste.
_FAITS = re.compile(r"\d+\s*%|\b(?!20[2-3]\d\b)\d{2,}\b|\bchez\b|"
                    r"\bmon (?:stage|expérience)|\bmes (?:stages|expériences)|\bmy (?:internship|experience)",
                    re.IGNORECASE)


# Disponibilité recopiée d'un objet imposé par l'offre, mais fausse pour moi (dispo mars 2027).
_DISPO_FAUSSE = re.compile(
    r"dispo(?:nible|nibilité)?\s*(?::\s*)?(?:dès\s+(?:maintenant|que\s+possible|à\s+présent)|"
    r"immédiate(?:ment)?|de\s+suite|tout\s+de\s+suite|asap)|available\s+(?:immediately|now|asap)|"
    r"(?:immediate|asap)\s+availability", re.IGNORECASE)


def corriger_objet(offre: dict, objet: str) -> str:
    """Remplace dans l'objet une disponibilité fausse par la vraie (sans toucher au reste)."""
    vraie = objectif_courant().dispo_objet(_anglais(offre))
    return (_DISPO_FAUSSE.sub(vraie, objet) if vraie else objet).rstrip(" .")


def _paragraphe(rep: dict) -> str:
    """Le paragraphe seul, même si DeepSeek a ajouté salutation / politesse / signature."""
    brut = str(rep.get("paragraphe") or rep.get("corps") or "").replace("\\n", "\n")
    blocs = [" ".join(b.split()) for b in re.split(r"\n\s*\n", brut) if b.strip()]
    blocs = [b for b in blocs if not _HORS_PARAGRAPHE.search(b)] or blocs
    return " ".join(blocs).strip()


def _objectif_present(objet: str, corps: str) -> bool:
    return objectif_courant().objectif_present(objet + " " + corps)


def phrase_objectif(offre: dict) -> str:
    return objectif_courant().phrase_objectif(_anglais(offre))


def demande_correction(probleme: str) -> str:
    obj = objectif_courant()
    return (f"Ton paragraphe ne convient pas : {probleme}. Réécris-le : UN SEUL paragraphe de 2 à "
            f"3 phrases — candidature à {obj.un(obj.nom_fr)} {obj.formule_fr} au poste de l'offre, "
            "qui je suis en une demi-phrase, mon intérêt pour "
            "l'entreprise et 2 ou 3 missions de l'offre — SANS expériences, stages, projets, "
            "chiffres, formule d'appel, politesse ni signature. Même format : UN SEUL bloc "
            '```json {"objet": "...", "paragraphe": "..."}.')


def _probleme(objet: str, paragraphe: str) -> str:
    if not _objectif_present(objet, paragraphe):
        return f"l'objectif « {objectif_courant().formule_fr} » n'y est pas écrit"
    if _FAITS.search(paragraphe):
        return "il cite des expériences ou des chiffres, que le CV et la lettre donnent déjà"
    if len(paragraphe) > 700:
        return "il est trop long (2 à 3 phrases suffisent)"
    return ""


def _sauver_versions(dossier: Path | None, offre: dict, destinataire: str, recruteur: str,
                     versions: list[tuple[str, str, str]], decision: str) -> None:
    """email_deepseek.txt : EXACTEMENT ce que DeepSeek a rédigé (chaque version), et ce que le
    robot en a fait. L'email réellement envoyé reste dans email_candidature.txt."""
    if dossier is None:
        return
    lignes = [f"Email rédigé par DeepSeek — {offre.get('nom_entreprise', '')} — "
              f"{offre.get('titre_poste', '')}",
              f"Date : {datetime.now():%Y-%m-%d %H:%M}",
              f"Destinataire : {destinataire}",
              f"Recruteur repéré sur l'offre : {recruteur or '(aucun)'}", ""]
    for i, (titre, objet, paragraphe) in enumerate(versions, start=1):
        lignes += [f"===== Version {i} — {titre} =====", f"Objet : {objet}", "", paragraphe, ""]
    if not versions:
        lignes += ["(DeepSeek n'a rédigé aucune version)", ""]
    lignes += ["===== Décision du robot =====", decision, ""]
    try:
        (Path(dossier) / "email_deepseek.txt").write_text("\n".join(lignes), encoding="utf-8")
    except OSError:
        pass


def rediger(chat, offre: dict, profil: dict, destinataire: str, logger, lettre: str = "",
            recruteur: str = "", dossier: Path | None = None) -> tuple[str, str]:
    """
    (objet, corps). DeepSeek écrit l'objet et UN paragraphe d'intérêt ; le robot assemble
    l'email (formule d'appel + paragraphe + fin fixe + nom et email). Paragraphe non conforme
    (objectif absent, expériences ou chiffres, trop long) : une demande de correction ; objectif
    toujours absent : le robot l'ajoute en tête du paragraphe. Modèle fixe si DeepSeek ne
    rédige rien d'exploitable.
    """
    versions: list[tuple[str, str, str]] = []
    valide = lambda d: bool(d.get("objet")) and bool(d.get("paragraphe") or d.get("corps"))  # noqa: E731

    def lire(rep: dict) -> tuple[str, str]:
        return corriger_objet(offre, " ".join(str(rep.get("objet", "")).split())), _paragraphe(rep)

    if chat is not None:
        try:
            objet, par = lire(chat.demander_json(
                demande_redaction(offre, profil, destinataire, lettre, recruteur), valide=valide,
                ouvrir=True))
            versions.append(("réponse de DeepSeek", objet, par))
            probleme = _probleme(objet, par)
            decision = "Version 1 utilisée telle quelle."
            if probleme:
                logger.warning(f"  Paragraphe de DeepSeek à corriger ({probleme}) -> demande de correction.")
                try:
                    objet2, par2 = lire(chat.demander_json(demande_correction(probleme), valide=valide))
                    versions.append(("après la demande de correction", objet2, par2))
                    probleme2 = _probleme(objet2, par2)
                    # On garde la version corrigée si elle règle le problème, ou si elle est au
                    # moins aussi bonne (objectif présent) — sinon la première.
                    if not probleme2 or _objectif_present(objet2, par2) >= _objectif_present(objet, par):
                        objet, par, probleme = objet2, par2, probleme2
                        decision = "Version 2 (corrigée par DeepSeek) utilisée."
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"  Correction du paragraphe impossible ({e}).")
                if not _objectif_present(objet, par):
                    par = phrase_objectif(offre) + " " + par
                    decision += f" Phrase d'objectif ajoutée par le robot : « {phrase_objectif(offre)} »"
                if _FAITS.search(par):
                    # Toujours des expériences / chiffres : le robot retire ces phrases-là.
                    phrases = re.split(r"(?<=[.!?])\s+", par)
                    gardees = [x for x in phrases if not _FAITS.search(x)]
                    if gardees and _objectif_present(objet, " ".join(gardees)):
                        par = " ".join(gardees)
                        decision += " Phrases citant expériences ou chiffres retirées par le robot."
            corps = assembler(offre, profil, recruteur, par)
            logger.info(f"  Email de candidature rédigé par DeepSeek (objet : « {objet[:80]} »).")
            _sauver_versions(dossier, offre, destinataire, recruteur, versions,
                             decision + " Formule d'appel, fin et signature ajoutées par le robot.")
            return objet, corps
        except Exception as e:  # noqa: BLE001 — la rédaction ne doit jamais bloquer l'envoi
            logger.warning(f"  Rédaction de l'email par DeepSeek impossible ({e}) -> modèle fixe.")
    objet, corps = message_de_secours(offre, profil, recruteur)
    _sauver_versions(dossier, offre, destinataire, recruteur, versions,
                     "DeepSeek n'a rien rédigé d'exploitable : modèle fixe du robot envoyé.")
    return objet, corps


def url_nouveau_message(destinataire: str, objet: str, corps: str, expediteur: str = "") -> str:
    """Fenêtre « Nouveau message » de Gmail, déjà remplie, dans la boîte de l'EXPÉDITEUR
    (authuser = son email) — jamais « le premier compte Google » du navigateur."""
    compte = f"authuser={quote(expediteur)}&" if expediteur else ""
    return (f"https://mail.google.com/mail/?{compte}view=cm&fs=1&tf=1"
            f"&to={quote(destinataire)}&su={quote(objet)}&body={quote(corps)}")


def url_gmail_du_compte(url: str, email: str) -> str:
    """Toute adresse mail.google.com ouverte par le robot vise la boîte de `email`."""
    p = urlsplit(url)
    if not email or p.netloc.lower() != "mail.google.com" or "authuser=" in p.query:
        return url
    chemin = re.sub(r"^/mail/u/\d+/?", "/mail/", p.path)       # « /mail/u/0 » = 1er compte : retiré
    if not chemin.startswith("/mail"):
        chemin = "/mail/"
    requete = (p.query + "&" if p.query else "") + f"authuser={quote(email)}"
    return urlunsplit((p.scheme or "https", p.netloc, chemin, requete, p.fragment))


def consigne(destinataire: str, objet: str) -> str:
    """Mission donnée à la boucle habituelle (captures + DeepSeek) pour cet envoi."""
    return (
        "MISSION SPÉCIALE — CANDIDATURE PAR EMAIL DANS GMAIL. Tu n'es pas sur le site de l'offre : "
        "tu es dans Gmail, dans la fenêtre « Nouveau message » déjà remplie par le robot "
        f"(À : {destinataire} ; Objet : « {objet} » ; texte complet de l'email).\n"
        "1. Ne modifie JAMAIS le destinataire, l'objet ni le texte de l'email.\n"
        "2. Joins les deux fichiers de <fichiers> : « cv » puis « lm », avec l'action « televerser » : "
        "avec \"input_fichier\" si un champ fichier est listé, sinon avec \"index\" = le bouton trombone "
        "« Joindre des fichiers / Attach files ».\n"
        "3. Vérifie sur la capture que les DEUX pièces jointes (noms de <fichiers>) apparaissent sous "
        "le texte et que leur chargement est terminé.\n"
        "4. Clique le bouton « Envoyer » / « Send » avec \"soumission_finale\": true.\n"
        "5. Dès que « Message envoyé » / « Message sent » s'affiche, réponds avec le statut « termine ».\n"
        "Si Gmail demande de se connecter, réponds « manuel » avec la raison « Gmail non connecté ».")
