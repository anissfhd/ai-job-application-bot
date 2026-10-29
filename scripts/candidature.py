"""
Dépôt automatique d'une candidature sur un site web.

Fonctionnement : boucle « observer -> décider -> agir ».
  1. On lit l'état de la page avec Tappi : texte, éléments numérotés, champs fichier
     ET une capture d'écran où chaque élément porte son numéro (vision).
  2. Le LLM (prompts/formulaire_prompt.txt) décide des actions : saisir, cliquer,
     choisir une option, téléverser le CV / la LM…
  3. On exécute les actions avec Tappi, on note le résultat, et on recommence
     jusqu'à la page de confirmation (ou un blocage : CAPTCHA, compte requis…).

Après chaque tentative, `bilan()` analyse ce qui s'est passé (prompts/bilan_prompt.txt) :
cause d'un échec, stratégie de rattrapage, et leçon à retenir pour ce site.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

import excel_manager as em
from gemini_chat import ChatEpuise   # chat web saturé -> l'orchestrateur change de navigateur
from llm_client import ClientLLM, ErreurLLM
from navigateur import Navigateur
from objectif import Objectif
from utils import chemin_projet, extraire_json, latex_vers_texte

# Fonctions 2Captcha réutilisées depuis le module de test (même logique, sans les garde-fous du CLI)
try:
    from test_captcha import (
        extraire_sitekey as _tc_extraire_sitekey,
        resoudre_2captcha as _tc_resoudre_2captcha,
        injecter_jeton as _tc_injecter_jeton,
        ErreurCaptcha as _ErreurCaptcha,
    )
    _CAPTCHA_MODULE_OK = True
except ImportError:
    _CAPTCHA_MODULE_OK = False
    _ErreurCaptcha = Exception  # type: ignore[assignment,misc]

# Verrous appliqués par le CODE (le LLM ne peut pas les contourner) :
# clics interdits sur tout ce qui modifie le compte du candidat (alertes, abonnements…)
CLICS_INTERDITS = re.compile(
    r"alerte|\balert|\bsuiv(re|ez|i)\b|\bfollow|newsletter|abonne|subscri|premium|messagerie|notification",
    re.IGNORECASE)
# « Nom de la pièce jointe », « Document name »… : un champ qui nomme un FICHIER, pas une
# personne. Le pré-remplissage ne doit pas y écrire le nom de famille du candidat.
CHAMP_NOM_FICHIER = re.compile(
    r"pi[èe]ce[ _-]?jointe|attachment|nom du (fichier|document)|(file|document)[ _-]?name",
    re.IGNORECASE)
# Mot de passe des comptes candidat : le modèle n'écrit que ce jeton, le code le remplace
# par la valeur de MOT_DE_PASSE_SITES (.env) au moment de la saisie.
JETON_MOT_DE_PASSE = "{MOT_DE_PASSE}"
# Trois versions DIFFÉRENTES (jeton, clé du .env, rang) : la 1re par défaut ; la 2e si le site
# refuse la 1re (trop courte, pas assez complexe — certains sites exigent 15 caractères) ; la 3e si
# la 2e est refusée aussi. Le modèle ne voit jamais que les jetons.
MOTS_DE_PASSE = ((JETON_MOT_DE_PASSE, "MOT_DE_PASSE_SITES", 1),
                 ("{MOT_DE_PASSE_2}", "MOT_DE_PASSE_SITES_2", 2),
                 ("{MOT_DE_PASSE_3}", "MOT_DE_PASSE_SITES_3", 3))
# Champs où « nom » ne désigne PAS le nom du candidat (« Nom et type de voie », « Nom de
# l'entreprise »…) : le pré-remplissage y écrivait le nom de famille (vu le 26/09).
NOM_PAS_CANDIDAT = re.compile(
    r"voie|\brue\b|street|adresse|address|entreprise|soci[ée]t[ée]|company|employeur|employer|"
    r"[ée]cole|school|universit|[ée]tablissement|r[ée]f[ée]rent|manager|contact|poste|dipl[ôo]me|"
    r"projet|ville|city", re.IGNORECASE)
# Boîtes mail : « aller » y remplacerait l'onglet du formulaire, donc le perdrait.
MESSAGERIES = re.compile(
    r"mail\.google\.|gmail\.com|outlook\.(live|office)\.com|mail\.yahoo\.",
    re.IGNORECASE)
# boutons qui ouvriraient la fenêtre Windows de choix de fichier (bloquante) : on dépose
# les fichiers uniquement avec l'action « televerser »
BOUTONS_FICHIER = re.compile(
    r"télécharger (le |un |votre )?(cv|curriculum|document|fichier)|téléverser|parcourir|importer|"
    r"choisir (un |le )?fichier|upload|browse|joindre|attach",
    re.IGNORECASE)
# Page d'ENVOI FINAL (bouton « Submit application »…) : c'est au robot d'envoyer, avec ses
# verrous CV / lettre ; le secours vision « boucle » ne s'y déclenche pas.
ENVOI_FINAL_VISIBLE = re.compile(r"\(button\)[^\n]*(submit application|envoyer (ma|la) candidature|"
                                 r"soumettre (ma|la) candidature|send application)", re.IGNORECASE)
# Boutons de compte candidat (voir _executer : nombre de clics limité).
BOUTON_CREATION_COMPTE = re.compile(
    r"cr[ée]er (mon |un |votre )?compte|create (an |your )?(applicant |candidate )?account|"
    r"s'inscrire|je m'inscris|sign ?up|\bregister\b", re.IGNORECASE)
BOUTON_CONNEXION = re.compile(r"(se |me )connecter|^\s*connexion\s*$|\blog ?in\b|\bsign ?in\b(?! with| to apply)",
                              re.IGNORECASE)
BOUTON_RENVOI_EMAIL = re.compile(r"renvoyer|resend|send (it )?again|(nouvel|autre) (e-?mail|lien)",
                                 re.IGNORECASE)
# Fenêtre LinkedIn « You reached today's Easy Apply limit » (limite quotidienne du compte).
LIMITE_EASY_APPLY = re.compile(
    r"reached today.s easy apply limit|easy apply limit|limite (quotidienne )?(de |des )?candidatures? "
    r"simplifi|limite de candidatures simplifi", re.IGNORECASE)
MARQUE_LIMITE_EASY_APPLY = "[LIMITE EASY APPLY]"
# Bouton d'upload qui concerne la LETTRE (sinon, c'est le CV qu'on dépose).
BOUTON_LETTRE = re.compile(r"lettre|cover|motivation|supporting|additional|compl[ée]mentaire",
                           re.IGNORECASE)
# Bouton radio (choix parmi des options d'une question) — le robot PEUT y répondre.
RADIO = re.compile(r"\((?:input:)?radio\)|\bradio\b", re.IGNORECASE)
# Cases qu'on ne coche JAMAIS, même dans un groupe : alertes, abonnements, suivi, mise en avant.
CASES_MARKETING = re.compile(
    r"alert|newsletter|abonne|subscribe|suivre|follow|top choice|premium|offres? similaires|"
    r"similar jobs|recevoir|receive|notification|partenaires|partners|promot",
    re.IGNORECASE)
# Bouton « Continuer / Se connecter avec Google » : un clic ordinaire ne suit pas la fenêtre
# Google (choix du compte, consentement) — la page restait blanche et la connexion n'aboutissait
# pas. Ces clics passent donc par la routine dédiée du navigateur.
GOOGLE_SSO = re.compile(
    r"(continu|sign|connect|log|identifi|inscri|register)[^\n]{0,30}\bgoogle\b"
    r"|\bgoogle\b[^\n]{0,15}(sign|connect|log)", re.IGNORECASE)
# Bouton de connexion / inscription par email + mot de passe, sur n'importe quel site : le
# site a besoin de temps pour ouvrir la session avant qu'on relise la page.
CONNEXION_SITE = re.compile(
    r"se connecter|connexion|s'identifier|\bsign ?in\b|\blog ?in\b|s'inscrire|inscription"
    r"|cr[ée]er (un |mon |votre )?compte|create (an |your )?account|\bregister\b|\bsign ?up\b",
    re.IGNORECASE)
# ATTENTES INTELLIGENTES : le robot surveille lui-même la page et ne passe au modèle que
# quand elle est prête — tout de suite si le site est rapide, au plus ces délais sinon.
DELAI_PAGE_PRETE = 30.0    # après une connexion (Google ou email) et l'action « attendre »
DELAI_CASE_VALIDEE = 20.0  # après le clic sur la case anti-robot
MAX_ATTENTES_NOPECHA = 3   # attentes NopeCHA max par page avant d'abandonner le CAPTCHA
# Page toujours vide au bout de DELAI_PAGE_PRETE (ex. « Redirecting to Indeed for one
# login », 0 bouton) : inutile d'y dépenser des étapes, l'offre passe en Action manuelle.
PAGE_BLOQUEE = "PAGE BLOQUÉE"
# Barre de RECHERCHE du site d'emploi (« City, state, zipcode, or "remote" », « Où ? »…) :
# jamais pré-remplie — y écrire la ville lance une recherche et fait quitter l'offre.
RECHERCHE_SITE = re.compile(
    r"search|recherch|keyword|mots?[ -]cl[ée]s|zip ?code,? or|job title|intitul[ée] de poste",
    re.IGNORECASE)
# consentement obligatoire au traitement des données (seule case à cocher que le robot peut cocher,
# et seulement si candidature.cocher_consentement_obligatoire vaut true)
CONSENTEMENT = re.compile(
    r"consen|données personnelles|donnees personnelles|confidentialit|privacy|j'accepte|"
    r"conditions générales|conditions generales|rgpd|gdpr|confirm|reconnais|acknowledge|"
    r"accepte[rz]? que|j'ai lu et|lu et accept|protection des données|traitement des données|"
    r"données transmises|donnees transmises",
    re.IGNORECASE)
# boutons qui font seulement passer à l'étape suivante (jamais un envoi final)
BOUTONS_NAVIGATION = re.compile(
    r"\bsuivant|\bnext\b|continuer|\bcontinue\b|réviser|\breview\b|étape|vérifier",
    re.IGNORECASE)
# Textes qui confirment qu'une candidature est bien partie (détection locale, sans LLM)
CONFIRMATIONS = ("candidature a été envoyée", "candidature envoyée", "merci pour votre candidature",
                 "votre candidature a", "application submitted", "application has been submitted",
                 "thank you for applying", "thanks for applying", "candidature a bien été",
                 "bewerbung wurde", "solicitud enviada")


def _cible_depuis_libelle(libelle: str) -> str:
    """Extrait le texte stable d'un élément (pour le retrouver plus tard, index mis à part)."""
    t = re.sub(r"^\[\d+\]\s*(\([^)]*\)\s*)?", "", str(libelle))
    t = re.sub(r"\[(coché|non coché|hors écran[^\]]*)\]\s*", "", t)
    t = t.split("«")[0].split("|")[0]
    return re.sub(r"\s+", " ", t).strip()[:60]


def _cible_exploitable(ligne: str) -> str:
    """
    Texte permettant de RETROUVER un élément lors du rejeu d'un playbook, ou "" s'il n'y en a
    pas d'assez distinctif. Une cible vide ou d'un ou deux caractères est pire qu'inutile :
    vide, l'action échoue à tous les coups ; trop courte, elle correspond à n'importe quel
    élément de la page. Dans ce cas on préfère ne rien mémoriser.
    """
    # Étiquettes que Tappi met par défaut : elles ne désignent aucun élément en particulier.
    generiques = {"select", "input", "button", "textarea", "option", "checkbox", "radio", "link"}
    for candidat in (_cible_depuis_libelle(ligne), _libelle_reel(ligne)):
        candidat = candidat.strip()
        if len(candidat) >= 3 and candidat.lower() not in generiques:
            return candidat
    return ""


def _libelle_reel(ligne: str) -> str:
    """
    Vraie question posée par un champ, telle qu'affichée à l'écran. Pour un <select>, Tappi
    ne donne que « select » : le texte utile est celui ajouté entre « » par _etat_page
    (ex. « What is your level of proficiency in English? »).
    """
    m = re.search(r"«\s*(.+?)\s*»", str(ligne))
    texte = m.group(1) if m else _cible_depuis_libelle(ligne)
    texte = re.sub(r"\[(coché|non coché|hors écran[^\]]*)\]\s*", "", texte)
    return re.sub(r"\s+", " ", texte).strip()[:80]


class InscriptionARejouer(Exception):
    """
    Le formulaire a été perdu pendant la confirmation d'un email d'inscription.

    Ce n'est pas un échec de l'offre : la page n'existe simplement plus, et poursuivre
    n'aurait aucun sens. L'orchestrateur rejoue la candidature depuis le début — UNE seule
    fois, et seulement dans ce cas précis.
    """


@dataclass
class ResultatCandidature:
    statut: str                      # un des statuts de excel_manager (POSTULE, ERREUR…)
    commentaire: str
    historique: list[str] = field(default_factory=list)
    soumission_cliquee: bool = False
    capture: bytes | None = None     # dernière capture d'écran (pour le bilan)
    url_finale: str = ""


def _nettoyer_reponse_champ(texte: str) -> str:
    """
    Garde uniquement le texte à recopier dans le champ : un chat a tendance à encadrer sa
    réponse (« Voici la réponse : », blocs Markdown, guillemets), ce qui se retrouverait tel
    quel dans le formulaire.
    """
    t = (texte or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    t = re.sub(r"^(voici|here is|here's)[^:]{0,60}:\s*", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"^(r[ée]ponse|answer)\s*:\s*", "", t, flags=re.IGNORECASE).strip()
    if len(t) > 1 and t[0] in "\"«“" and t[-1] in "\"»”":
        t = t[1:-1].strip()
    return t


class AgentCandidature:
    def __init__(self, config: dict, profil: dict, llm: ClientLLM, nav: Navigateur,
                 logger: logging.Logger | None = None):
        self.llm = llm
        self.nav = nav
        self.logger = logger or logging.getLogger("job_automation")
        # SECOURS VISION (secours_vision.py) : utilisé seulement juste avant un abandon. Sans
        # clé ou en cas d'erreur, il s'écarte : le robot garde exactement sa logique habituelle.
        try:
            from secours_vision import SecoursVision
            self.secours = SecoursVision(config, nav, self.logger)
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Secours vision non chargé : {e}")
            self.secours = None
        # Moteur du REMPLISSAGE DE FORMULAIRE : l'API (self.llm) ou un chat web.
        # Tout le reste (offre, CV, lettre, diagnostic) continue de passer par self.llm.
        self.llm_formulaire = llm
        moteur = str((config.get("llm") or {}).get("moteur_formulaire", "api")).lower()
        if moteur == "deepseek_formulaire":
            # Le chat web DeepSeek. MÊME logique que le chat Gemini, au mot près : la classe
            # en hérite et ne change que les repères du site (voir deepseek_formulaire.py).
            from deepseek_formulaire import ClientDeepSeekFormulaire
            self.llm_formulaire = ClientDeepSeekFormulaire(config, nav, self.logger)
            self.logger.info("Moteur de formulaire : CHAT WEB DeepSeek "
                             "(aucune clé API consommée).")
        # --- CHAT GEMINI : conservé tel quel, simplement hors du chemin d'exécution. ---
        # Pour y revenir : remettre `moteur_formulaire: gemini_chat` dans config.yaml et
        # décommenter les 4 lignes ci-dessous. Le module gemini_chat.py reste intact.
        # elif moteur == "gemini_chat":
        #     from gemini_chat import ClientGeminiChat
        #     self.llm_formulaire = ClientGeminiChat(config, nav, self.logger)
        #     self.logger.info("Moteur de formulaire : CHAT WEB Gemini "
        #                      "(aucune clé API consommée).")
            # NOTE : l'escalade automatique vers DeepSeek (ClientLLM.chainer + ClientDeepSeekTexte)
            # est écrite et testée unitairement, mais retirée du chemin par défaut : elle a été
            # introduite juste avant une série de candidatures ratées, sans test de bout en bout.
            # À réactiver une fois le fonctionnement nominal confirmé sur plusieurs offres.
        # Chat qui a rédigé le CV adapté (« lien 2 ») : c'est lui qui répond aux questions
        # techniques. Branché par l'orchestrateur ; None tant qu'aucun CV n'a été rédigé.
        self.moteur_technique = None
        # Le bilan de fin de candidature se fait DANS la conversation du formulaire (le
        # modèle y a vu les pages). C'est donc l'orchestrateur qui la ferme, APRÈS le bilan.
        # Reste à True en usage direct (tests, script isolé) : on ferme comme avant.
        self.fermer_chat_en_fin = True
        cfg = config.get("candidature", {})
        self.max_etapes = int(cfg.get("max_etapes_formulaire", 25))
        # CHIEN DE GARDE : durée maximale d'UNE offre (tentatives et rattrapages compris).
        # Au-delà, l'offre passe en « Action manuelle » et le robot continue : aucune offre
        # ne peut plus le retenir indéfiniment. L'échéance est posée par l'orchestrateur.
        self.duree_max_offre = float(cfg.get("duree_max_offre_minutes", 25)) * 60
        self.echeance: float | None = None
        # Mots de passe de secours utilisés pendant cette candidature ({hôte: rang}) et
        # fonction qui donne le rang déjà connu pour un hôte (branchée par l'orchestrateur).
        self.mdp_par_site: dict[str, int] = {}
        self.rang_mot_de_passe = None
        self.soumission_auto = bool(cfg.get("soumission_automatique", True))
        self.vision = bool(cfg.get("vision", True))
        self.consentement_autorise = bool(cfg.get("cocher_consentement_obligatoire", False))
        # « Postuler à tout » : on ne s'arrête plus pour demander ton avis (type de contrat,
        # champ inattendu…). Seuls les vrais obstacles arrêtent encore : CAPTCHA, création de
        # compte, connexion par mot de passe. Activé par l'option 5 du menu (--tout-postuler).
        self.tout_postuler = bool(cfg.get("tout_postuler", False))
        self.periode_capture = max(1, int(cfg.get("vision_periode_etapes", 3)))
        self.preremplissage = bool(cfg.get("preremplissage_local", True))
        # Connexion « Continuer avec Google » : utilise la session Google déjà ouverte dans le
        # Chrome du robot (aucun mot de passe). Désactivable dans config.yaml.
        self.connexion_google = bool(cfg.get("connexion_google", True))
        # Attente propre au mode « postuler à tout » : confirmer un email d'inscription ou
        # saisir un mot de passe prend plus de temps que cocher une case anti-robot.
        self.attente_inscription = float(cfg.get("attente_inscription_secondes", 240))
        # CAPTCHA / création de compte : le robot ne le fait pas, il attend l'humain puis reprend.
        self.pause_intervention = bool(cfg.get("pause_intervention_humaine", True))
        self.attente_humaine = float(cfg.get("attente_humaine_secondes", 180))
        # 2Captcha : résolution automatique des CAPTCHA simples (checkbox reCAPTCHA / hCaptcha / Turnstile)
        cfg_cap = config.get("captcha", {}) or {}
        self.captcha_actif = bool(cfg_cap.get("actif", False)) and _CAPTCHA_MODULE_OK
        self.captcha_cle = (os.environ.get("TWOCAPTCHA_API_KEY") or cfg_cap.get("cle_api") or "").strip()
        self.captcha_timeout = int(cfg_cap.get("timeout_secondes", 120))
        self.captcha_pause = max(2, int(cfg_cap.get("pause_verification_secondes", 5)))
        dossier_prompts = chemin_projet(config["chemins"]["prompts"])
        objectif = Objectif(profil)            # repères [[…]] : email, objectif de recherche
        self.prompt = objectif.remplir((dossier_prompts / "formulaire_prompt.txt").read_text(encoding="utf-8"))
        self.prompt_bilan = objectif.remplir((dossier_prompts / "bilan_prompt.txt").read_text(encoding="utf-8"))
        self.profil_yaml = yaml.safe_dump(profil, allow_unicode=True, sort_keys=False)
        self.profil = profil
        self._libelles: dict[int, str] = {}   # index -> libellé des éléments de la dernière lecture
        self._identites: dict[str, str] = {}   # index -> identité de l'élément (voir _verifier_index)
        self._contextes: dict[int, str] = {}  # index -> texte autour de l'élément (cases à cocher…)
        self._prefill_essais: dict[str, int] = {}   # libellé -> nb de pré-remplissages tentés

    # ------------------------------------------------------------------

    def _verifier_index(self, action: dict) -> str:
        """
        Le numéro de l'action désigne-t-il TOUJOURS l'élément que le modèle a vu ? La page a pu
        être renumérotée entre la lecture et l'action (React redessine, Tappi renumérote dans
        un autre ordre). Sinon, on retrouve l'élément par son identité et on corrige les
        numéros : ceux de cette action, et ceux de toutes les suivantes (via self._identites et
        self._libelles, remis à jour). Renvoie une note pour le journal, ou "".
        """
        try:
            idx = int(action.get("index"))
        except (TypeError, ValueError):
            return ""
        voulu = self._identites.get(str(idx))
        if not voulu:
            return ""
        actuelles = self.nav.identites()
        if actuelles.get(str(idx)) == voulu:
            return ""
        # Renumérotation : on relit la page (numéros neufs) et on apparie par identité.
        self.nav.elements()
        actuelles = self.nav.identites()
        par_identite: dict[str, list[str]] = {}
        for n, ident in sorted(actuelles.items(), key=lambda kv: int(kv[0])):
            par_identite.setdefault(ident, []).append(n)
        anciennes: dict[str, list[str]] = {}
        for n, ident in sorted(self._identites.items(), key=lambda kv: int(kv[0])):
            anciennes.setdefault(ident, []).append(n)
        correspondance: dict[int, int] = {}
        for ident, vieux in anciennes.items():
            neufs = par_identite.get(ident, [])
            for rang, n in enumerate(vieux):          # homonymes : même rang dans la page
                if rang < len(neufs):
                    correspondance[int(n)] = int(neufs[rang])
        nouveau = correspondance.get(idx)
        # Les numéros du modèle restent sa référence pour la suite de la liste d'actions :
        # on traduit ses anciens numéros vers les nouveaux.
        self._identites = {str(correspondance[int(n)]): i for n, i in self._identites.items()
                           if int(n) in correspondance}
        self._libelles = {correspondance[n]: l for n, l in self._libelles.items() if n in correspondance}
        self._contextes = {correspondance[n]: c for n, c in self._contextes.items() if n in correspondance}
        self._renumerotation = {**{k: correspondance.get(v, v) for k, v in self._renumerotation.items()},
                                **{k: v for k, v in correspondance.items() if k not in self._renumerotation}}
        if nouveau is None:
            return f"élément n° {idx} introuvable après renumérotation de la page"
        action["index"] = nouveau
        return f"page renumérotée : n° {idx} -> {nouveau}"

    def _etat_page(self, avec_capture: bool = True) -> tuple[str, bytes | None]:
        """Résumé textuel de la page courante + capture annotée (si demandée), envoyés au LLM."""
        self.nav.neutraliser_onglets()
        elements = self.nav.elements()             # (ré)indexe les éléments
        options = self.nav.options_select()
        menus = self.nav.options_menus()           # options des listes « maison » (sans les ouvrir)
        libelles = self.nav.libelles()
        contextes = self.nav.contextes_elements()
        lignes = []
        self._libelles = {}
        self._contextes = {}
        for ligne in elements:
            idx = ligne[1:ligne.find("]")] if ligne.startswith("[") else ""
            libelle = libelles.get(idx, "")
            if libelle and libelle.lower() not in ligne.lower():
                ligne += f"  « {libelle} »"          # vrai libellé (ex. case « Créer une alerte emploi »)
            if idx.isdigit():
                # Masqué dès ici : le libellé d'un champ mot de passe contient sa VALEUR, et il
                # sert ensuite à l'historique (envoyé à DeepSeek), au journal et aux playbooks.
                # Le mot de passe y est parti en clair une fois (vu le 27/09).
                self._libelles[int(idx)] = self._masquer(ligne)   # utilisé par les verrous de sécurité
                if contextes.get(idx):
                    self._contextes[int(idx)] = contextes[idx]
            if idx in options:
                ligne += "  | options : " + " / ".join(options[idx])
            elif idx in menus:
                m = menus[idx]
                ligne += (f"  | liste{' à CHOIX MULTIPLES' if m.get('multiple') else ''} — options : "
                          + " / ".join(m.get("options") or []))
            lignes.append(ligne)
        # Identité de chaque numéro TEL QUE LE MODÈLE LE VOIT : sert à retrouver le bon élément
        # si la page est renumérotée avant l'action (voir _verifier_index).
        self._identites = self.nav.identites()
        capture = self.nav.capture_annotee() if (self.vision and avec_capture) else None
        texte = (
            f"URL : {self.nav.url()}\n"
            f"Titre : {self.nav.titre()}\n"
            f"CAPTCHA visible détecté : {'oui' if self.nav.captcha_visible() else 'non'}\n"
            f"Capture d'écran jointe : {'oui (numéros en rouge = index des éléments)' if capture else 'non'}\n\n"
            f"--- Texte visible (extrait) ---\n{self.nav.texte_court(3500)}\n\n"
            f"--- Défilement ---\n{self.nav.etat_defilement()}\n\n"
            f"--- Éléments interactifs ---\n" + ("\n".join(lignes) or "(aucun)") + "\n\n"
            f"--- Champs fichier ---\n{json.dumps(self.nav.champs_fichier(), ensure_ascii=False)}\n\n"
            f"--- Iframes ---\n{json.dumps(self.nav.iframes(), ensure_ascii=False)}"
        )
        # La liste des éléments reprend la valeur des champs, y compris celle d'un champ mot
        # de passe déjà rempli : on la remet sous forme de jeton avant de l'envoyer au modèle.
        return self._masquer(texte), capture

    @staticmethod
    def _mot_de_passe() -> str:
        """Mot de passe des comptes candidat, lu dans .env (MOT_DE_PASSE_SITES)."""
        return os.environ.get("MOT_DE_PASSE_SITES", "").strip()

    def _masquer(self, texte: str) -> str:
        """Remplace les vrais mots de passe par leurs jetons : le modèle ne les voit jamais."""
        if not texte:
            return texte
        # Le plus long d'abord : un mot de passe ne doit jamais être masqué à moitié.
        valeurs = sorted(((os.environ.get(cle, "").strip(), jeton) for jeton, cle, _ in MOTS_DE_PASSE),
                         key=lambda v: -len(v[0]))
        for mdp, jeton in valeurs:
            if mdp:
                texte = texte.replace(mdp, jeton)
        return texte

    def _hote_courant(self) -> str:
        try:
            return (urlparse(self.nav.url()).hostname or "").lower()
        except Exception:  # noqa: BLE001
            return ""

    def _bloc_compte_site(self) -> str:
        """
        Rappelle au modèle le mot de passe avec lequel le compte candidat de CE site a été créé
        (2e ou 3e version), appris lors d'une candidature précédente sur le même site.
        """
        rang_du_site = getattr(self, "rang_mot_de_passe", None)
        hote = self._hote_courant()
        if not callable(rang_du_site) or not hote:
            return ""
        try:
            rang = int(rang_du_site(hote) or 0)
        except Exception:  # noqa: BLE001
            return ""
        jeton = next((j for j, _, r in MOTS_DE_PASSE if r == rang), "")
        if rang < 2 or not jeton:
            return ""
        return (f"<compte_site>\nSur ce site ({hote}), ton compte candidat a été créé avec le mot de passe "
                f"{jeton} (le site avait refusé {JETON_MOT_DE_PASSE}) : pour te connecter, utilise {jeton}.\n"
                f"</compte_site>\n\n")

    def _ouvrir_lien_de_la_boite(self, action: dict, url_avant: str) -> str:
        """
        Dans la boîte mail, un clic sur un lien n'a ouvert AUCUN onglet et n'a pas changé la
        page : ouvre l'adresse complète du lien dans un nouvel onglet. Renvoie l'id de l'onglet,
        ou "" si ce n'était pas un lien vers un site extérieur (lien interne de Gmail…).
        """
        if action.get("type") != "cliquer" or not str(action.get("index", "")).isdigit():
            return ""
        try:
            if self.nav.url() != url_avant or not MESSAGERIES.search(url_avant):
                return ""
        except Exception:  # noqa: BLE001
            return ""
        adresse = self.nav.adresse_du_lien(int(action["index"]))
        hote = (urlparse(adresse).hostname or "").lower()
        # Lien de redirection Google (google.com/url?q=…) : la vraie adresse est dans « q ».
        if hote.endswith("google.com") and urlparse(adresse).path == "/url":
            adresse = (parse_qs(urlparse(adresse).query).get("q") or [""])[0]
            hote = (urlparse(adresse).hostname or "").lower()
        if not adresse.startswith("http") or not hote or hote.endswith(("google.com", "gmail.com")):
            return ""
        try:
            id_onglet = self.nav.ouvrir_onglet_assistant(adresse)
        except Exception:  # noqa: BLE001
            return ""
        self.logger.info(f"  Le clic dans la boîte mail n'a rien ouvert -> le robot ouvre lui-même le "
                         f"lien ({hote}) dans un nouvel onglet.")
        return id_onglet

    def _corriger_telephone(self, index: int, valeur: str, compte_rendu: str) -> str:
        """
        Champ téléphone qui contient l'INDICATIF (widget international, ex. MuiTelInput
        d'OpenClassrooms : « +212 6… »). Y taper le numéro SANS indicatif le fait relire comme
        un autre indicatif (« 675… » -> +675, Papouasie-Nouvelle-Guinée). Si c'est arrivé, le
        robot retape le numéro COMPLET avec indicatif et vérifie.
        """
        sans = self._valeur_profil(("reponses_standard", "telephone_sans_indicatif"))
        avec = self._valeur_profil(("reponses_standard", "telephone_avec_indicatif")).replace(" ", "")
        chiffres = re.sub(r"\D", "", valeur)
        national = re.sub(r"\D", "", sans)
        if not national or not avec or not chiffres or not avec.endswith(national) \
                or chiffres not in (national, re.sub(r"\D", "", avec)):
            return compte_rendu                                  # ce n'est pas le téléphone
        indicatif = avec[: len(avec) - len(national)]          # « +212 »
        lu = self.nav.valeur_champ(index).replace(" ", "")
        if chiffres == national and lu.startswith("+") and not lu.startswith(indicatif):
            self.nav.saisir(index, avec)
            time.sleep(0.4)
            relu = self.nav.valeur_champ(index).replace(" ", "")
            ok = relu.startswith(indicatif)
            self.logger.info(f"  Téléphone : le champ contient l'indicatif et affichait un autre pays "
                             f"(« {lu[:5]}… ») -> numéro complet {indicatif}… retapé "
                             f"({'corrigé' if ok else 'TOUJOURS faux'}).")
            compte_rendu = (f"{compte_rendu} — ATTENTION : ce champ téléphone contient l'INDICATIF (il "
                            f"affichait « {lu} », mauvais pays). Le robot a retapé le numéro COMPLET {avec} : "
                            f"le champ contient maintenant « {relu} »"
                            + ("" if ok else " — toujours faux : choisis le pays dans le sélecteur "
                                             "d'indicatif, puis retape le numéro complet"))
        # Sélecteur d'indicatif « maison » À CÔTÉ du champ (« France (+33) », vu le 27/09) : s'il
        # n'est pas sur le bon pays, le site refuse le numéro — on le dit au modèle tout de suite.
        code = self.nav.indicatif_du_telephone(index)
        if code and code != indicatif:
            self.logger.info(f"  Téléphone : le sélecteur d'indicatif à côté affiche {code} "
                             f"(attendu {indicatif}) -> signalé au modèle.")
            compte_rendu = (f"{compte_rendu} — ATTENTION : le sélecteur d'indicatif à côté de ce champ "
                            f"affiche « {code} » et non {indicatif} : choisis {indicatif} dans ce menu, puis "
                            f"vérifie que le champ contient le numéro SANS indicatif ({{TELEPHONE_SANS_INDICATIF}}).")
        return compte_rendu

    def _executer(self, action: dict, fichiers: dict[str, Path]) -> str:
        """Exécute une action décidée par le LLM et renvoie un compte rendu court."""
        type_action = action.get("type")
        if type_action == "saisir":
            valeur = str(action.get("valeur", ""))
            if any(jeton in valeur for jeton, _, _ in MOTS_DE_PASSE):
                # Le modèle écrit le jeton, le vrai mot de passe n'est substitué qu'ici, au
                # moment de la frappe.
                reelle = valeur
                for jeton, cle, rang in MOTS_DE_PASSE:
                    if jeton not in reelle:
                        continue
                    mdp = os.environ.get(cle, "").strip()
                    if not mdp:
                        return f"ÉCHEC : {cle} absent du fichier .env"
                    reelle = reelle.replace(jeton, mdp)
                    # Mot de passe de secours utilisé sur CE site : retenu pour la prochaine fois.
                    hote = self._hote_courant()
                    if rang >= 2 and hote:
                        self.mdp_par_site[hote] = max(self.mdp_par_site.get(hote, 0), rang)
                try:
                    return self._masquer(str(self.nav.saisir(int(action["index"]), reelle)))
                except Exception as e:  # noqa: BLE001
                    return f"ÉCHEC : {self._masquer(str(e))}"
            index = int(action["index"])
            compte_rendu = str(self.nav.saisir(index, valeur))
            # Texte refusé par un champ qui n'accepte que des CHIFFRES (salaire Workday…) : on
            # le dit clairement au modèle, qui sinon réécrivait la même phrase en boucle.
            if (re.search(r"shows 0 chars|may not have landed", compte_rendu)
                    and re.search(r"[^\d\s.,+\-€$]", valeur)):
                compte_rendu += (" — CE CHAMP N'ACCEPTE QUE DES CHIFFRES (champ numérique) : saisis un "
                                 "NOMBRE seul, sans texte ni devise (rémunération : "
                                 "reponses_standard.gratification_souhaitee_montant du profil).")
            return self._corriger_telephone(index, valeur, compte_rendu)
        if type_action == "saisir_suggestion":
            return self.nav.saisir_suggestion(int(action["index"]), str(action.get("valeur", "")))
        if type_action == "selectionner":
            idx = int(action["index"])
            return self.nav.selectionner(idx, str(action.get("option", "")),
                                         _libelle_reel(self._libelles.get(idx, "")))
        if type_action == "cliquer":
            libelle = self._libelles.get(int(action["index"]), "")
            if CLICS_INTERDITS.search(libelle):
                return f"REFUSÉ par sécurité (alerte / abonnement / suivi interdits) : {libelle}"
            # On teste le TEXTE VISIBLE du bouton, pas le libellé complet : celui-ci contient
            # aussi le nom technique de l'élément, et un nom comme
            # « ctl00$…$UploadAttachedFileControl$btnAddPJ » (TalentSoft) contient « Upload »
            # sans que le bouton ouvre quoi que ce soit — il ne fait qu'ENREGISTRER un fichier
            # déjà téléversé. Le refuser rendait la candidature impossible : le site répétait
            # « Le CV est requis » indéfiniment. Sans texte visible, on reste sur le libellé
            # complet, c'est-à-dire sur le comportement prudent d'avant.
            visible = _libelle_reel(libelle) or libelle
            if BOUTONS_FICHIER.search(visible) and "(input:file)" not in libelle:
                return ("REFUSÉ : ce bouton ouvrirait la fenêtre Windows de choix de fichier (bloquante). "
                        "Dépose le fichier avec l'action « televerser » (voir « Champs fichier »).")
            # Connexion Google : bouton « Continue with Google » du site, ou n'importe quel
            # clic sur une page accounts.google.com (choix du compte, consentement). La routine
            # dédiée suit tout le flux jusqu'au retour sur le site, puis on attend que la page
            # du site soit prête (session ouverte) avant de la relire.
            if getattr(self, "connexion_google", True) and (GOOGLE_SSO.search(visible)
                                                            or self._sur_page_google()):
                ok, detail = self.nav.connecter_avec_google()
                if ok:
                    if self.nav.attendre_page_prete(DELAI_PAGE_PRETE) == "bloquée":
                        return f"{PAGE_BLOQUEE} après la connexion Google : {self.nav.url()}"
                    return f"connexion Google réussie ({detail})"
                if not detail.startswith("aucun bouton"):
                    return f"ÉCHEC connexion Google : {detail}"
                # la routine ne voit pas de bouton Google : clic ordinaire, comme avant
            # Comptes candidat : les essais répétés (« Créer mon compte » cliqué 3 fois, connexion
            # refusée en boucle) font bloquer le compte par le site, qui exige ensuite un changement
            # de mot de passe. On limite donc les clics, et on ne redemande jamais un email de
            # vérification : le lien du premier deviendrait invalide (Workday).
            if BOUTON_RENVOI_EMAIL.search(visible):
                return ("REFUSÉ : ne redemande jamais un nouvel email de vérification (le lien du premier "
                        "email deviendrait invalide). Ouvre le premier email reçu dans Gmail et clique son lien.")
            genre_compte = ("creation" if BOUTON_CREATION_COMPTE.search(visible)
                            else "connexion" if BOUTON_CONNEXION.search(visible) else "")
            if genre_compte:
                # 3 mots de passe différents (règle 3) : une création par version, et une connexion
                # par version + une après réinitialisation — toujours sous le seuil de blocage (5).
                limite = {"creation": 3, "connexion": 4}[genre_compte]
                deja = self._clics_compte.get(genre_compte, 0)
                if deja >= limite:
                    return (f"REFUSÉ : déjà {deja} clics sur « {visible[:50]} » pour cette candidature. Les "
                            f"essais répétés font bloquer le compte. Si le compte existe déjà avec un autre "
                            f"mot de passe, applique la règle 3e (Mot de passe oublié) ; sinon renvoie "
                            f"« manuel » avec la cause affichée par le site.")
                self._clics_compte[genre_compte] = deja + 1
            est_radio = bool(RADIO.search(libelle))
            if "[coché]" in libelle or "[non coché]" in libelle:
                # Bouton RADIO = réponse à une question à choix obligatoire (localisation, niveau
                # d'anglais, visa…) : le robot doit y répondre d'après le profil. Autorisé.
                if est_radio:
                    return self.nav.cliquer_case(int(action["index"]))
                # Case à cocher / interrupteur INDÉPENDANT : jamais touché (alertes, « Suivre »,
                # newsletters…), sauf :
                #  a) un consentement obligatoire autorisé en config (détecté via le libellé ET le
                #     texte autour de la case, souvent vide de sens seul, ex. « Confirmé » d'Indeed) ;
                #  b) une RÈGLE apprise pour ce site (bilan : « cocher la case de consentement »).
                contexte = f"{libelle} {self._contextes.get(int(action['index']), '')}"
                regle_consent = bool(getattr(self, "regles", {}).get("consentement_candidature"))
                if ("[non coché]" in libelle
                        and ((self.consentement_autorise and CONSENTEMENT.search(contexte))
                             or (regle_consent and CONSENTEMENT.search(contexte)))):
                    self.logger.info(f"  Consentement obligatoire détecté -> case cochée : {contexte[:120]}")
                    return self.nav.cliquer_case(int(action["index"]), seulement_cocher=True)
                # c) une case qui est une OPTION d'une question du formulaire (« Préférences de
                #    mobilité ? » -> « All France ») : c'est une réponse, comme un bouton radio.
                #    Jamais pour une case d'alerte, de newsletter, de suivi ou de mise en avant.
                question = self.nav.question_de_la_case(int(action["index"]))
                if question and not CASES_MARKETING.search(f"{contexte} {question}"):
                    self.logger.info(f"  Case réponse à la question « {question[:80]} » -> autorisée.")
                    return self.nav.cliquer_case(int(action["index"]))
                return ("REFUSÉ : le robot ne coche ni ne décoche aucune case indépendante. Laisse-la "
                        "telle quelle. Si une case obligatoire bloque l'envoi, renvoie « manuel ».")
            connexion = bool(CONNEXION_SITE.search(visible))
            avant = self.nav._signature_page() if connexion else None
            empreinte = self.nav.empreinte_page()
            resultat = self.nav.cliquer(int(action["index"]), libelle)
            if connexion:
                # Le site ouvre la session : on attend la page suivante, prête à l'emploi.
                time.sleep(self.nav.delai_action)
                if self.nav.attendre_page_prete(DELAI_PAGE_PRETE, avant) == "bloquée":
                    return f"{PAGE_BLOQUEE} après « {visible[:40]} » : {self.nav.url()}"
            # Un clic ouvre une fenêtre, change d'étape (« Continue » -> « Loading… » -> page
            # suivante)… : on attend que l'écran ait RÉELLEMENT fini de changer, sinon la capture
            # montre la page d'avant ou un chargement, et le modèle répond « attendre ».
            self.nav.attendre_apres_action(empreinte)
            return resultat
        if type_action == "televerser":
            cle = str(action.get("fichier", "cv")).lower()
            if cle not in fichiers:
                return f"fichier inconnu « {cle} » (valeurs possibles : cv, lm)"
            empreinte = self.nav.empreinte_page()
            if str(action.get("index", "")).isdigit() and "input_fichier" not in action:
                # Dépôt par le BOUTON du site (champ fichier créé seulement au clic).
                resultat = self.nav.televerser_par_bouton(int(action["index"]), fichiers[cle])
            else:
                resultat = self.nav.televerser(fichiers[cle], int(action.get("input_fichier", 0)))
                if str(resultat).startswith("ÉCHEC"):
                    # Champ absent, ou champ caché d'une autre rubrique : dépôt par le BOUTON du site.
                    idx = (int(action["index"]) if str(action.get("index", "")).isdigit()
                           else self._index_bouton_upload(cle))
                    if idx is not None:
                        resultat = self.nav.televerser_par_bouton(idx, fichiers[cle])
            if str(resultat).startswith("ÉCHEC"):
                # Dernier recours : champ fichier dans un CADRE D'UN AUTRE SITE posé sur le bouton
                # « Charger » (Salesforce), invisible pour la page mais accessible à Chrome.
                externe = self.nav.televerser_cadre_externe(fichiers[cle], cle)
                if not externe.startswith("ÉCHEC"):
                    self.logger.info(f"  Dépôt dans un cadre d'un autre site : {externe[:120]}")
                    resultat = externe
            self.nav.attendre_apres_action(empreinte)   # le site traite le fichier (Uploading…)
            return resultat
        if type_action == "aller":
            url = str(action.get("url", ""))
            if not url.startswith("http"):
                return "URL invalide"
            if MESSAGERIES.search(url):
                # « aller » navigue l'onglet courant : vers une boîte mail, il effacerait le
                # formulaire à moitié rempli. La confirmation d'email passe par un onglet à côté.
                return ("REFUSÉ : « aller » effacerait le formulaire. Utilise "
                        "« ouvrir_onglet » pour ouvrir la boîte mail à côté.")
            self.nav.aller(url)
            return f"page ouverte : {url}"
        if type_action == "defiler":
            index = action.get("index")
            return self.nav.defiler(int(index) if str(index).isdigit() else None,
                                    "haut" if str(action.get("sens", "bas")).lower() == "haut" else "bas")
        if type_action == "repondre_technique":
            return self._repondre_technique(action)
        if type_action == "ouvrir_onglet":
            return self._ouvrir_onglet_secondaire(str(action.get("url", "")))
        if type_action == "fermer_onglet":
            return self._fermer_onglet_secondaire()
        if type_action == "attendre":
            if self.nav.nopecha_actif and self.nav.captcha_a_resoudre():
                return f"CAPTCHA : résolution automatique -> {self.nav.attendre_nopecha()}"
            # Plus un délai fixe : on attend la fin RÉELLE du chargement ; l'attente longue
            # (page restée sans aucun bouton ni champ) ne sert qu'à détecter une page bloquée.
            self.nav.attendre_apres_action(delai_max=DELAI_PAGE_PRETE)
            if (self.nav._signature_page() or {}).get("n", 0) > 0:
                return "attente terminée : la page est prête"
            if self.nav.attendre_page_prete(DELAI_PAGE_PRETE) == "bloquée":
                return f"{PAGE_BLOQUEE} : toujours aucun bouton ni champ au bout de "                        f"{DELAI_PAGE_PRETE:.0f} s ({self.nav.url()})"
            return "attente terminée : la page est prête"
        return f"action inconnue : {type_action}"

    def _index_bouton_upload(self, cle: str, frais: bool = False) -> int | None:
        """
        Bouton d'upload du BON document sur la page : « resume / CV » pour le CV, « cover letter /
        lettre » pour la lettre (libellé du bouton ou rubrique autour, ex. « Lettre de motivation
        [Upload] »). Jamais le bouton de l'autre document.
        frais : relire la page (replay du playbook) au lieu de la liste de l'étape en cours.
        """
        lm = cle == "lm"
        genre = re.compile(r"cover|lettre|motivation|letter" if lm
                           else r"resume|résumé|\bcv\b|curriculum", re.IGNORECASE)
        autre = re.compile(r"resume|résumé|\bcv\b|curriculum" if lm
                           else r"cover|lettre|motivation|letter", re.IGNORECASE)
        liste = {} if frais else dict(getattr(self, "_libelles", {}) or {})
        if not liste:
            for ligne in self.nav.elements():
                n = ligne[1:ligne.find("]")] if ligne.startswith("[") else ""
                if n.isdigit():
                    liste[int(n)] = ligne
        candidats = []
        for i, lib in liste.items():
            if not str(i).isdigit() or "(input:file)" in lib:
                continue
            visible = _libelle_reel(lib) or lib
            if not BOUTONS_FICHIER.search(visible):
                continue
            if genre.search(visible):
                return int(i)                       # libellé explicite : « Upload resume »
            autour = self.nav.texte_autour(int(i))
            if genre.search(autour) and not autre.search(visible):
                candidats.append(int(i))
        return candidats[0] if candidats else None

    def _repondre_technique(self, action: dict) -> str:
        """
        Question TECHNIQUE du formulaire : la réponse est rédigée par le chat qui a écrit le
        CV adapté à CETTE offre (le « lien 2 »), puis saisie dans le champ.

        Pourquoi ne pas laisser le chat du formulaire répondre : il connaît le profil (état
        civil, visa, langues) et reçoit un extrait du CV, mais c'est l'autre conversation qui
        a lu l'offre ligne à ligne et choisi quelles compétences mettre en avant. C'est donc
        elle qui répond juste sur « décrivez votre expérience en X », sans rien inventer.
        """
        index = int(action["index"])
        question = str(action.get("question") or self._libelles.get(index, "")).strip()
        if not question:
            return "REFUSÉ : « repondre_technique » exige le texte de la question."
        fabrique = getattr(self, "moteur_technique", None)
        moteur = fabrique() if callable(fabrique) else None
        if moteur is None:
            # Documents pas encore rédigés (ou génération par l'API) : on ne bloque pas.
            return ("Le CV adapté n'est pas encore rédigé : réponds toi-même à partir de "
                    "<cv_adapte>, ou fais d'abord générer les documents.")
        limite = int(action.get("longueur_max") or 900)
        # Le CV et la lettre de CETTE offre sont joints à la question : la conversation a pu
        # rédiger d'autres offres depuis (CV réutilisé d'un essai précédent, robot relancé…).
        docs = getattr(self, "_docs_courants", None) or {}
        offre = getattr(self, "_offre_courante", None) or {}
        rappel = ""
        if docs.get("prets"):
            rappel = (f"OFFRE : {offre.get('nom_entreprise', '')} — {offre.get('titre_poste', '')}\n"
                      f"CV ADAPTÉ À CETTE OFFRE :\n{docs.get('cv_texte', '')[:5000]}\n\n"
                      f"LETTRE DE MOTIVATION DE CETTE OFFRE :\n{docs.get('lettre', '')[:3000]}\n\n")
        consigne = (
            "Voici le CV et la lettre adaptés à cette offre (rédigés dans cette conversation ou "
            "rappelés ci-dessous). Le formulaire de candidature pose la question suivante.\n\n"
            + rappel
            + f"QUESTION : {question}\n\n"
            "Réponds UNIQUEMENT par le texte à recopier dans le champ : pas d'introduction, "
            "pas de guillemets, pas de Markdown, aucun commentaire. Appuie-toi EXACTEMENT sur "
            "le CV et la lettre de CETTE offre — compétences, projets, technologies et motivations "
            "retenus — et n'invente rien qui n'y figure pas. Écris dans la langue de "
            f"la question, en {limite} caractères maximum.")
        try:
            reponse = moteur.generer("", consigne, images=None, tache="question_technique")
        except Exception as e:  # noqa: BLE001 — ne jamais perdre la candidature là-dessus
            self.logger.warning(f"  Question technique : le chat du CV adapté n'a pas répondu ({e}).")
            return ("le chat du CV adapté n'a pas répondu — réponds toi-même d'après <cv_adapte>")
        texte = _nettoyer_reponse_champ(reponse)[:limite]
        if not texte:
            return "le chat du CV adapté a renvoyé une réponse vide — réponds toi-même"
        self.logger.info(f"    réponse rédigée par le chat du CV adapté : {texte[:110]}…")
        return self.nav.saisir(index, texte)

    # ------------------------------------------------------------------

    # Correspondances champ -> profil, dans l'ordre (la 1re qui matche gagne).
    # Évite le LLM pour les champs évidents (prénom, email…) = formulaire bien plus rapide.
    _CORRESPONDANCES = [
        (r"first[ _-]?name|given[ _-]?name|\bpr[ée]nom\b", ("identite", "prenom")),
        (r"last[ _-]?name|family[ _-]?name|surname|\bnom\b", ("identite", "nom")),
        # « Adresse électronique » : sans « électronique », le motif « adresse » plus bas
        # l'attrapait et le champ email recevait l'adresse POSTALE.
        (r"e[ _-]?mail|courriel|[ée]lectronique|electronic", ("identite", "email")),
        # Le téléphone est traité à part (_valeur_telephone) : la forme dépend de la présence
        # d'un sélecteur d'indicatif sur la page.
        (r"phone|mobile|t[ée]l[ée]phone", ("reponses_standard", "telephone_avec_indicatif")),
        (r"linkedin", ("liens", "linkedin")),
        (r"github", ("liens", "github")),
        (r"portfolio|website|site (web|perso)|personal (site|page)", ("liens", "portfolio")),
        (r"city|ville|location|localisation", ("identite", "ville")),
        (r"country|pays\b", ("identite", "pays")),
        (r"address|adresse", ("identite", "adresse")),
        (r"postal|zip|code postal", ("identite", "code_postal")),
    ]

    # Au-delà : le champ refuse la saisie libre (typeahead) -> on laisse la main au LLM
    MAX_PREFILL_PAR_CHAMP = 2

    def _valeur_profil(self, cle: tuple[str, str]) -> str:
        return str((self.profil.get(cle[0], {}) or {}).get(cle[1], "") or "").strip()

    def _preremplir(self, historique: list[str], etape: int) -> None:
        """Remplit SANS LLM les champs texte vides dont le nom est évident (prénom, email, ville…).
        Ne touche jamais les listes, les questions ouvertes ni les champs déjà remplis."""
        if not self.preremplissage:
            return
        # Jamais dans une messagerie : le champ « À » de Gmail ressemble à un champ email, et
        # le robot s'y serait ajouté lui-même comme destinataire.
        try:
            if MESSAGERIES.search(self.nav.url()):
                return
        except Exception:  # noqa: BLE001
            return
        try:
            champs = self.nav.champs_texte_vides()
        except Exception:  # noqa: BLE001 — le pré-remplissage ne doit jamais bloquer
            return
        remplis = []
        for champ in champs:
            etiquette = str(champ.get("etiquette", ""))
            # « Nom de la pièce jointe », « File name »… : ce « nom » n'est pas celui du
            # candidat. Le motif \bnom\b y répondait et la pièce jointe prenait le NOM du candidat.
            # C'est au modèle de la nommer — d'après le nom du fichier réellement transmis.
            if CHAMP_NOM_FICHIER.search(etiquette):
                continue
            if RECHERCHE_SITE.search(etiquette):
                continue
            valeur = ""
            for motif, cle in self._CORRESPONDANCES:
                if re.search(motif, etiquette, re.IGNORECASE):
                    # « Nom et type de voie », « Nom de l'entreprise »… : pas le nom du candidat,
                    # on passe aux motifs suivants (ici : adresse).
                    if cle == ("identite", "nom") and NOM_PAS_CANDIDAT.search(etiquette):
                        continue
                    valeur = self._valeur_profil(cle)
                    break
            if not valeur:
                continue
            # Téléphone : la forme dépend de la page. Un sélecteur « Morocco (+212) » à côté
            # signifie que le champ attend le numéro SEUL — y mettre « +212… » écrirait
            # l'indicatif deux fois (« +212 » puis « +212 6… »).
            if re.search(r"phone|mobile|t[ée]l[ée]phone", etiquette, re.IGNORECASE):
                cle = ("reponses_standard",
                       "telephone_sans_indicatif" if self.nav.selecteur_indicatif_present()
                       else "telephone_avec_indicatif")
                valeur = self._valeur_profil(cle) or valeur
            # Un champ qui REDEVIENT vide après plusieurs saisies refuse la saisie libre :
            # c'est un typeahead qui exige de choisir une suggestion (ex. « Location » de
            # Workable). Inutile de le réécrire à chaque tour — on le signale au LLM, qui
            # dispose de « saisir_suggestion », sinon le bouton d'envoi reste désactivé.
            cle_champ = etiquette.lower()[:40]
            self._prefill_essais[cle_champ] = self._prefill_essais.get(cle_champ, 0) + 1
            if self._prefill_essais[cle_champ] > self.MAX_PREFILL_PAR_CHAMP:
                if self._prefill_essais[cle_champ] == self.MAX_PREFILL_PAR_CHAMP + 1:
                    self.logger.info(f"  Champ « {etiquette[:40]} » redevient vide après "
                                     f"{self.MAX_PREFILL_PAR_CHAMP} saisies -> champ à autocomplétion, "
                                     f"la main est laissée au LLM (saisir_suggestion).")
                    historique.append(
                        f"étape {etape} : le champ « {etiquette[:40]} » REDEVIENT VIDE après "
                        f"{self.MAX_PREFILL_PAR_CHAMP} saisies automatiques : il REFUSE la saisie libre. "
                        f"C'est un champ à AUTOCOMPLÉTION -> utilise « saisir_suggestion » avec « {valeur} » "
                        f"(elle tape la valeur ET choisit la 1re suggestion). Tant que ce champ n'est pas "
                        f"validé ainsi, le bouton d'envoi restera désactivé : ne clique pas dessus avant.")
                continue
            # Un champ d'autocomplétion (ville…) doit choisir dans la liste de suggestions
            saisie = self.nav.saisir_suggestion if champ.get("autocomplete") else self.nav.saisir
            compte_rendu = saisie(int(champ["index"]), valeur)
            if re.search(r"phone|mobile|t[ée]l[ée]phone", etiquette, re.IGNORECASE):
                # Même contrôle que pour une saisie du modèle : indicatif en double / mauvais pays.
                compte_rendu = self._corriger_telephone(int(champ["index"]), valeur, str(compte_rendu))
            # Un avertissement (téléphone) ne doit pas être coupé : c'est lui qui guide le modèle.
            longueur = 600 if "ATTENTION" in str(compte_rendu) else 120
            historique.append(f"étape {etape} : pré-remplissage auto « {etiquette[:40]} » = « {valeur} » "
                              f"-> {str(compte_rendu)[:longueur]}")
            remplis.append(f"{etiquette[:28]} = {valeur}")
        if remplis:
            self.logger.info(f"  Pré-remplissage automatique ({len(remplis)}) : " + " | ".join(remplis))

    # ------------------------------------------------------------------

    # JS : détecte le type de CAPTCHA présent sur la page (hcaptcha, turnstile, ou recaptcha_v2)
    _JS_TYPE_CAPTCHA = ("(() => {"
                        "if (document.querySelector('.h-captcha,[data-hcaptcha-sitekey]')) return 'hcaptcha';"
                        "if (document.querySelector('.cf-turnstile,[data-turnstile-sitekey]')) return 'turnstile';"
                        "return 'recaptcha_v2';})()")

    def _resoudre_captcha_nopecha(self, dossier_captures: Path, etape: int) -> bool:
        """
        Laisse l'extension NopeCHA résoudre le CAPTCHA encore affiché (reCAPTCHA, hCaptcha,
        Turnstile, défis à images compris). Renvoie True si la page s'est débloquée.
        """
        if not self.nav.nopecha_actif or not self.nav.captcha_a_resoudre():
            return False
        self.logger.info("  NopeCHA : CAPTCHA toujours affiché — attente de la résolution…")
        etat = self.nav.attendre_nopecha()
        if etat in ("validée", "disparue"):
            self.logger.info(f"  NopeCHA : CAPTCHA résolu ({etat}).")
            self.nav.capture(dossier_captures / f"captcha_resolu_etape{etape}.png")
            return True
        self.logger.warning("  NopeCHA : CAPTCHA non résolu dans le délai.")
        return False

    def _resoudre_captcha_2captcha(self, dossier_captures: Path, etape: int) -> bool:
        """
        Tente de résoudre le CAPTCHA présent sur la page via l'API 2Captcha.
        Utilise les fonctions de test_captcha.py (extraire_sitekey, resoudre_2captcha, injecter_jeton).
        Renvoie True si le CAPTCHA a été résolu et le jeton injecté dans la page, False sinon.
        """
        if not self.captcha_actif or not self.captcha_cle:
            return False
        try:
            sitekey = _tc_extraire_sitekey(self.nav)
            if not sitekey:
                self.logger.info("  2Captcha : aucun sitekey détecté — résolution impossible.")
                return False
            try:
                type_captcha = str(self.nav.b.eval(self._JS_TYPE_CAPTCHA) or "recaptcha_v2")
            except Exception:  # noqa: BLE001
                type_captcha = "recaptcha_v2"
            pageurl = self.nav.url()
            self.logger.info(f"  2Captcha : CAPTCHA détecté ({type_captcha}, sitekey {sitekey[:18]}…) — envoi à 2Captcha…")
            jeton, duree = _tc_resoudre_2captcha(self.captcha_cle, type_captcha, sitekey, pageurl,
                                                  self.captcha_timeout, self.captcha_pause, self.logger)
            self.logger.info(f"  2Captcha : jeton reçu en {duree:.1f} s — injection dans la page…")
            if not _tc_injecter_jeton(self.nav, type_captcha, jeton):
                self.logger.warning("  2Captcha : jeton reçu mais champ CAPTCHA introuvable dans la page.")
                return False
            self.logger.info("  2Captcha : CAPTCHA résolu et jeton injecté.")
            self.nav.capture(dossier_captures / f"captcha_resolu_etape{etape}.png")
            return True
        except _ErreurCaptcha as e:
            self.logger.warning(f"  2Captcha : échec de résolution ({e}).")
            return False
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"  2Captcha : erreur inattendue ({e}).")
            return False

    def _alerte_sonore(self) -> None:
        """Bip d'alerte pour prévenir l'utilisateur qu'une intervention manuelle est requise."""
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(880, 250)
        except Exception:  # noqa: BLE001 — pas de son dispo (non-Windows, service…) : on retombe sur le bip terminal
            try:
                print("\a", end="", flush=True)
            except Exception:  # noqa: BLE001
                pass

    # Durée de vie maximale de l'onglet secondaire, en étapes. Sans ce plafond, un modèle
    # perdu dans une boîte mail y consommerait les 60 étapes de la candidature.
    MAX_ETAPES_ONGLET_SECONDAIRE = 20

    def _ids_onglets(self) -> list[str]:
        try:
            return [getattr(t, "id", "") for t in self.nav.b.tabs()]
        except Exception:  # noqa: BLE001
            return []

    def _ouvrir_onglet_secondaire(self, url: str) -> str:
        """
        Ouvre une page À CÔTÉ du formulaire, sans le perdre, et y bascule.

        Réservé à la confirmation d'un email d'inscription (modes 4 et 5) : c'est le seul
        cas où quitter le formulaire est justifié. Une boîte mail est donc toujours
        acceptée ; une autre adresse seulement après un rappel de la règle 3. L'action
        « aller » ne conviendrait pas — elle navigue l'onglet courant, donc elle écraserait
        le formulaire à moitié rempli.
        """
        if not (self._inscription_en_cours or MESSAGERIES.search(url)):
            return ("REFUSÉ : « ouvrir_onglet » ne sert qu'à confirmer un email d'inscription. "
                    "Utilise « aller » pour naviguer dans le formulaire.")
        if not url.startswith("http"):
            return "URL invalide"
        if self._onglet_secondaire:
            return "un onglet secondaire est déjà ouvert : utilise « fermer_onglet » d'abord."
        # On retient l'onglet du formulaire AVANT de bouger : c'est lui qu'il faudra retrouver.
        self._onglet_formulaire = self.nav.epingler_onglet_courant()
        # Gmail : toujours la boîte de l'email de candidature (authuser), quel que soit l'ordre
        # des comptes Google du navigateur. Choix fait ICI, sur le PC : DeepSeek ne voit pas l'email.
        from candidature_gmail import url_gmail_du_compte
        url = url_gmail_du_compte(url, str((self.profil.get("identite") or {}).get("email") or ""))
        nouvel_id = self.nav.ouvrir_onglet_assistant(url)
        if not nouvel_id:
            return "ÉCHEC : l'onglet n'a pas pu être ouvert."
        self._onglet_secondaire = nouvel_id
        self._etapes_onglet_secondaire = 0
        self.nav.epingler(nouvel_id)
        time.sleep(2.0)
        self.logger.info(f"  Onglet secondaire ouvert ({url[:60]}) — le formulaire reste ouvert à côté.")
        return f"onglet ouvert sur {url[:80]} ; le formulaire t'attend dans son onglet."

    def _fermer_onglet_secondaire(self) -> str:
        """Referme l'onglet secondaire et revient sur le formulaire, là où il en était."""
        if not self._onglet_secondaire:
            return "aucun onglet secondaire ouvert."
        for id_lien in self._onglets_lien:           # pages ouvertes par le lien de l'email
            if id_lien != self._onglet_formulaire:
                self.nav._fermer_onglet(id_lien)
        self._onglets_lien = []
        self.nav._fermer_onglet(self._onglet_secondaire)
        self._onglet_secondaire = ""
        self._etapes_onglet_secondaire = 0
        self.nav.epingler(self._onglet_formulaire or None)
        time.sleep(1.5)
        # L'onglet du formulaire existe-t-il encore ? Le site a pu le rediriger pendant la
        # confirmation, ou le fermer lui-même. Sans lui, inutile de continuer à l'aveugle :
        # la candidature sera rejouée depuis le début (une seule fois, voir l'orchestrateur).
        try:
            ouverts = {getattr(t, "id", "") for t in self.nav.b.tabs()}
        except Exception:  # noqa: BLE001
            ouverts = set()
        if self._onglet_formulaire and self._onglet_formulaire not in ouverts:
            self._formulaire_perdu = True
            self.logger.warning("  L'onglet du formulaire a disparu pendant la confirmation "
                                "de l'email — la candidature va être rejouée.")
            return "ÉCHEC : le formulaire a été perdu pendant la confirmation."
        self.logger.info("  Onglet secondaire refermé — retour sur le formulaire.")
        return "onglet refermé, retour sur le formulaire de candidature."

    def _attendre_intervention_humaine(self, raison: str, dossier_captures: Path, etape: int,
                                       forcer: bool = False, delai: float | None = None,
                                       consigne: str = "") -> bool:
        """
        CAPTCHA « je ne suis pas un robot », connexion ou création de compte : le robot ne le fait
        JAMAIS lui-même. Il ALERTE l'utilisateur (bip + message + capture) et ATTEND que celui-ci
        fasse le geste dans le Chrome du robot, puis reprend. Renvoie True si la page s'est débloquée
        (geste fait), False si personne n'est intervenu dans le délai imparti.

        `forcer` : attendre même si `pause_intervention_humaine` est à false. Sert au mode
        « postuler à tout », où l'on préfère un arrêt de quelques minutes à une candidature
        perdue — alors qu'en mode normal on enchaîne sans déranger l'utilisateur.
        `delai` : durée propre à ce cas. None = celle de la configuration générale ;
        0 = on n'attend pas du tout (c'est ainsi qu'on désactive l'attente du mode 5).
        `consigne` : ce que l'utilisateur doit faire, quand ce n'est ni un CAPTCHA ni une
        connexion (confirmer un email, par exemple).
        """
        attente = self.attente_humaine if delai is None else delai
        if (not self.pause_intervention and not forcer) or attente <= 0:
            return False
        try:
            url0 = self.nav.url()
        except Exception:  # noqa: BLE001
            url0 = ""
        captcha0 = self.nav.captcha_visible()
        self.nav.capture(dossier_captures / f"intervention_requise_etape{etape}.png")
        self._alerte_sonore()
        self.logger.warning("=" * 70)
        self.logger.warning(f"  ⚠️  INTERVENTION REQUISE : {raison}")
        self.logger.warning("  👉 " + (consigne or
                            "Fais-le TOI-MÊME dans le Chrome du robot : coche « Je ne suis pas un "
                            "robot » / clique « Se connecter avec Google »."))
        self.logger.warning("     Le robot reprend seul dès que la page change.")
        self.logger.warning(f"  (Le robot attend jusqu'à {int(attente)} s…)")
        self.logger.warning("=" * 70)
        debut = time.time()
        while time.time() - debut < attente:
            time.sleep(3)
            try:
                captcha_maintenant = self.nav.captcha_visible()
                url_maintenant = self.nav.url()
            except Exception:  # noqa: BLE001
                continue
            # Débloqué si : le CAPTCHA a disparu, OU on a quitté la page (connexion faite -> URL change).
            if (captcha0 and not captcha_maintenant) or (url_maintenant and url_maintenant != url0):
                self.logger.info("  ✅ Intervention détectée — reprise automatique de la candidature.")
                time.sleep(1.5)
                return True
        self.logger.warning("  ⏳ Aucune intervention à temps -> l'offre passe en « Action manuelle ».")
        return False

    def _tenter_connexion_google(self, historique: list[str], etape: int) -> bool:
        """
        Si la page propose « Continuer / Se connecter avec Google », s'y connecte avec la session
        Google DÉJÀ ouverte dans le Chrome du robot (aucun identifiant, aucun mot de passe saisi).
        Renvoie True si la connexion a abouti et que la page a changé.
        """
        if not self.connexion_google:
            return False
        try:
            deja_google = "accounts.google.com" in self.nav.url()
        except Exception:  # noqa: BLE001
            deja_google = False
        if not deja_google:
            try:
                labels = self.nav.bouton_google()
            except Exception:  # noqa: BLE001 — la détection ne doit jamais bloquer
                return False
            if not labels:
                return False
            self.logger.info(f"  Bouton Google détecté (« {labels[0][:60]} ») -> connexion automatique…")
        try:
            ok, detail = self.nav.connecter_avec_google()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"erreur technique ({e})"
        self.logger.info(f"  Connexion Google : {detail}")
        historique.append(f"étape {etape} : connexion Google "
                          f"{'(page Google déjà ouverte)' if deja_google else ''} -> {detail}")
        if ok:
            self.nav.attendre_page_prete(DELAI_PAGE_PRETE)   # le site finalise la session
        return ok

    def _sur_page_google(self) -> bool:
        try:
            return "accounts.google.com" in self.nav.url()
        except Exception:  # noqa: BLE001
            return False

    def postuler(self, url_offre: str, offre: dict, fournir_documents, dossier_captures: Path,
                 lecons: list[str] | None = None, conseil: str = "",
                 regles: dict | None = None, reprendre_page: bool = False) -> ResultatCandidature:
        """
        Remplit et soumet le formulaire de candidature de l'offre. Le CV et la lettre sont
        générés À LA DEMANDE (via `fournir_documents()`), au moment où le formulaire réclame
        un fichier — pas avant.
        """
        self.regles = regles or {}
        self.mdp_par_site = {}                 # mots de passe de secours utilisés (cette tentative)
        self.nav.alertes_fermees()             # fenêtres d'une offre précédente : sans objet ici
        self._prefill_essais = {}              # compteur de pré-remplissage, propre à cette offre
        self._clics_compte: dict[str, int] = {}   # clics « Créer mon compte » / « Se connecter »
        self.actions_menees: list[dict] = []   # séquence d'actions réussies (playbook), remise à zéro
        # Documents générés paresseusement (au 1er besoin d'upload)
        documents: dict = {"prets": False, "cv": None, "lm": None, "lettre": "", "cv_texte": ""}
        self._docs_courants = documents     # pour les questions ouvertes (repondre_technique)
        self._offre_courante = offre
        historique: list[str] = []
        soumission_cliquee = False
        derniere_capture: bytes | None = None
        dernieres_actions: list[str] = []
        boucles = 0
        scrolls_consecutifs = 0    # nb de « defiler » d'affilée sans autre action (anti-boucle de scroll)
        etats_recents: list[int] = []
        actions_menees: list[dict] = []   # actions ayant réussi, pour le playbook du site
        champ_fichier_vu = False   # le site propose au moins un champ de dépôt de fichier
        cv_depose = False          # le CV adapté à cette offre a bien été déposé
        lm_depose = False          # la lettre adaptée à cette offre a bien été déposée
        rappel_cv = False          # envoi déjà retenu une fois pour le CV
        rappel_lm = False          # envoi déjà retenu une fois pour la lettre
        recherches_ratees = {"cv": 0, "lm": 0}   # bouton Edit / Add cherché sans succès
        recuperations = 0          # nombre de tentatives de récupération déjà faites (voir plus bas)
        MAX_RECUPERATIONS = 2      # avant d'abandonner, on montre la capture au modèle et on réessaie
        relances_manuel = 0        # mode « postuler à tout » : « manuel » renvoyé au modèle (voir plus bas)
        MAX_RELANCES_MANUEL = 3    # au-delà, c'est un vrai blocage : on s'arrête comme d'habitude
        secours_utilises = 0       # secours vision déjà tentés pour cette candidature
        visites_etapes: dict[str, int] = {}   # étape (signature) -> nombre de passages de DeepSeek
        etapes_secourues: set[str] = set()     # étapes où le secours a déjà été tenté
        relances_inscription = 0   # règle 3 (connexion / compte) rappelée au modèle
        MAX_RELANCES_INSCRIPTION = 1
        # Confirmation d'email : autorise l'ouverture d'un onglet à côté du formulaire (une
        # boîte mail l'est toujours, voir _ouvrir_onglet_secondaire). Remis à zéro à chaque
        # candidature.
        self._inscription_en_cours = False
        self._onglet_formulaire = ""
        self._onglet_secondaire = ""
        self._onglets_lien: list[str] = []   # pages ouvertes par un lien de la boîte mail
        self._etapes_onglet_secondaire = 0
        self._formulaire_perdu = False
        page_changee = True        # la page vient de changer -> capture à cette étape (vision adaptative)
        etapes_sans_capture = 0    # nb d'étapes d'affilée SANS capture (on en force une périodiquement)
        # Blocages « durs » : on n'insiste pas (le robot ne doit pas les contourner).
        BLOCAGES_DURS = ("captcha", "robot", "compte", "connexion", "connecter", "mot de passe",
                         "password", "sign in", "log in", "email", "expir", "plus disponible", "sms",
                         "inscription")
        # Blocages de connexion / compte / CAPTCHA / SMS : traités par le bloc dédié plus bas
        # (Google, 2Captcha, rappel de la règle 3, attente éventuelle, sinon Action manuelle).
        BLOCAGES_HUMAINS = ("captcha", "robot", "compte", "account", "connexion", "connecter",
                            "se connecter", "sign in", "log in", "s'inscrire", "inscription",
                            "register", "sso", "sms")

        def assurer_documents() -> None:
            """Génère le CV + la lettre à la première demande (ouvre/ferme DeepSeek si actif)."""
            if not documents["prets"]:
                cv, lm, lettre = fournir_documents()
                documents.update(prets=True, cv=Path(cv), lm=Path(lm), lettre=str(lettre or ""))
                # Texte du CV adapté : source des expériences/projets pour les questions ouvertes
                tex = Path(cv).with_suffix(".tex")
                if tex.exists():
                    documents["cv_texte"] = latex_vers_texte(tex.read_text(encoding="utf-8"))

        def fichiers_actuels() -> dict:
            return {"cv": documents["cv"], "lm": documents["lm"]} if documents["prets"] else {}

        def resultat(statut: str, commentaire: str, nom_capture: str | None = None) -> ResultatCandidature:
            if nom_capture:
                self.nav.capture(dossier_captures / nom_capture)
            try:
                url = self.nav.url()
            except Exception:  # noqa: BLE001
                url = ""
            # Fin de candidature : une conversation = une candidature -> on ferme le chat.
            # La suivante repartira d'un chat vierge. Sans effet si le moteur est l'API.
            fermer = getattr(self.llm_formulaire, "fermer", None) if self.fermer_chat_en_fin else None
            if callable(fermer):
                fermer()
            return ResultatCandidature(statut, commentaire, historique, soumission_cliquee, derniere_capture, url)

        self.nav.onglet_unique()
        # reprendre_page : le replay du playbook a déjà avancé dans le formulaire (fenêtre
        # Easy Apply ouverte, CV généré…). Recharger l'offre effacerait tout ce travail : le
        # LLM repart de la page telle qu'elle est.
        if not reprendre_page:
            self.nav.aller(url_offre)

        # La description du poste sert aux questions ouvertes (« pourquoi nous ? », « en quoi
        # ce poste vous intéresse ? ») : sans elle, le modèle ne peut répondre que vaguement.
        description = str(offre.get("description_offre", "") or "").strip()
        contexte_fixe = (
            f"<profil>\n{self.profil_yaml}</profil>\n\n"
            f"<offre>\nEntreprise : {offre.get('nom_entreprise', '')}\n"
            f"Poste : {offre.get('titre_poste', '')}\nLien : {url_offre}\n"
            + (f"\nDescription du poste :\n{description[:3000]}\n" if description else "")
            + "</offre>\n\n"
        )
        if lecons:
            contexte_fixe += "<lecons_site>\n" + "\n".join(f"- {l}" for l in lecons) + "\n</lecons_site>\n\n"
        if conseil:
            contexte_fixe += f"<conseil_diagnostic>\n{conseil}\n</conseil_diagnostic>\n\n"

        cases_cliquees: set[str] = set()   # pages où la case anti-robot a déjà été cliquée
        attentes_nopecha: dict[str, int] = {}   # nombre d'attentes NopeCHA par page
        for etape in range(1, self.max_etapes + 1):
            # CHIEN DE GARDE : l'offre a dépassé sa durée maximale (tentatives et rattrapages
            # compris) -> « Action manuelle », et le robot passe à la suivante.
            if self.echeance is not None and time.monotonic() > self.echeance:
                minutes = self.duree_max_offre / 60
                self.logger.warning(f"  Durée maximale de l'offre dépassée ({minutes:.0f} min) -> "
                                    f"offre suivante.")
                if soumission_cliquee:
                    return resultat(em.ACTION_MANUELLE,
                                    f"Soumission cliquée mais confirmation non détectée — durée maximale "
                                    f"({minutes:.0f} min) atteinte, vérifie sur le site", "duree_max.png")
                try:
                    ou = self.nav.url()
                except Exception:  # noqa: BLE001
                    ou = ""
                return resultat(em.ACTION_MANUELLE,
                                f"Durée maximale ({minutes:.0f} min) dépassée sans finir la candidature "
                                f"(dernière page : {ou[:120]}) : le robot est passé à l'offre suivante",
                                "duree_max.png")
            # Page en cours de chargement (« Loading… », « Saving… », « Submitting… ») : le
            # robot attend lui-même la fin, sans aller-retour « attendre » avec le modèle.
            attente = self.nav.attendre_apres_action()
            if attente > 1:
                self.logger.info(f"  Chargement de la page attendu par le robot ({attente:.1f} s).")
            # Page de REVUE qui reprend les fichiers d'une candidature précédente (Indeed /
            # Glassdoor) : le robot clique TOUT DE SUITE « Edit resume » puis « Add supporting
            # documents » (dans une iframe que le modèle ne voit pas), sans le laisser chercher.
            genre = ("cv" if not cv_depose and not rappel_cv
                     else "lm" if not lm_depose and not rappel_lm else "")
            if genre and self.nav.cliquer_bouton_documents(genre, cliquer=False):
                if genre == "cv":
                    rappel_cv = True
                else:
                    rappel_lm = True
                try:
                    assurer_documents()
                except ChatEpuise:
                    raise
                except Exception as e:  # noqa: BLE001
                    self.logger.warning(f"  Génération du CV/lettre impossible : {e}")
                if documents["prets"]:
                    empreinte = self.nav.empreinte_page()
                    clic = self.nav.cliquer_bouton_documents(genre)
                    if clic:
                        self.nav.attendre_apres_action(empreinte)
                        fichier = documents[genre].name
                        quoi = "le CV" if genre == "cv" else "la lettre de motivation"
                        self.logger.info(f"  Page de revue : {quoi} ({fichier}) à déposer -> "
                                         f"le robot a cliqué « {clic} ».")
                        historique.append(
                            f"étape {etape} : page de revue avec les fichiers d'une candidature "
                            f"précédente. Le robot a cliqué lui-même « {clic} ». Dépose « {genre} » "
                            f"({fichier}) avec « televerser » (il REMPLACE l'ancien fichier), puis "
                            f"« Continue »."
                            + (" Pour la lettre : UNIQUEMENT le fichier dans « Upload a file / "
                               "Additional documents » ; ne touche pas à « Write a cover letter »."
                               if genre == "lm" else ""))
                        page_changee = True
            # CAPTCHA visible : le ROBOT s'en occupe avant le modèle — attente de NopeCHA, puis
            # en secours UN clic sur la case (le modèle ne le pourrait pas : elle vit dans une
            # iframe d'un autre domaine, sans numéro). Règle 4 du prompt : le modèle répond
            # « attendre » tant que le CAPTCHA est là.
            try:
                url_page = self.nav.url()
                captcha = self.nav.captcha_a_resoudre()
            except Exception:  # noqa: BLE001
                url_page, captcha = "", False
            # NopeCHA (extension du Chrome du robot) résout seule le CAPTCHA : le robot RESTE sur
            # la page et l'attend, avant toute capture ou décision du modèle. Plafonné par page
            # pour ne pas tourner en rond sur un CAPTCHA qu'elle ne sait pas résoudre.
            # Toutes les chances d'affilée : on n'interroge PAS le modèle entre deux attentes (il
            # ne pourrait que répondre « attendre »), on laisse NopeCHA retravailler directement.
            while (captcha and self.nav.nopecha_actif
                    and attentes_nopecha.get(url_page, 0) < MAX_ATTENTES_NOPECHA):
                attentes_nopecha[url_page] = attentes_nopecha.get(url_page, 0) + 1
                nopecha = self.nav.attendre_nopecha()
                if nopecha in ("validée", "disparue"):
                    self.logger.info(f"  CAPTCHA visible -> résolu par NopeCHA ({nopecha}).")
                    historique.append(
                        f"étape {etape} : un CAPTCHA était affiché ; il a été résolu automatiquement "
                        f"({nopecha}). Poursuis la candidature normalement.")
                    captcha = False
                    page_changee = True
                else:
                    self.logger.info(f"  NopeCHA : CAPTCHA {nopecha} "
                                     f"({attentes_nopecha[url_page]}/{MAX_ATTENTES_NOPECHA}).")
            # Secours : UN clic sur la case, une fois par page.
            if captcha and url_page not in cases_cliquees:
                cases_cliquees.add(url_page)
                clic = self.nav.cliquer_case_anti_robot()
                if clic == "case cliquée":
                    clic += " -> " + self.nav.attendre_case_validee(DELAI_CASE_VALIDEE)
                self.logger.info(f"  Case anti-robot visible -> {clic} (une seule fois sur cette page).")
                historique.append(
                    f"étape {etape} : le robot a cliqué lui-même, une fois, la case anti-robot "
                    f"({clic}). Si le CAPTCHA est toujours là, réponds {{\"type\": \"attendre\"}} : "
                    f"la résolution automatique continue.")
                page_changee = True
            # Vision ADAPTATIVE : la capture (lente et coûteuse) n'est envoyée que si la page
            # vient de changer, après un problème, ou toutes les self.periode_capture étapes.
            avec_capture = (page_changee or recuperations > 0
                            or etapes_sans_capture >= self.periode_capture)
            try:
                etat, capture = self._etat_page(avec_capture=avec_capture)
                derniere_capture = capture or derniere_capture
                page_changee = False
                etapes_sans_capture = 0 if capture else etapes_sans_capture + 1
            except Exception as e:  # noqa: BLE001 — page en cours de chargement, onglet fermé…
                historique.append(f"étape {etape} : lecture de la page impossible ({e})")
                page_changee = True
                time.sleep(self.nav.delai)
                continue
            # Limite quotidienne Easy Apply de LinkedIn : inutile d'insister ni de consulter
            # DeepSeek. L'orchestrateur reporte l'offre et passe aux offres sans Easy Apply.
            if LIMITE_EASY_APPLY.search(etat):
                return resultat(em.A_TRAITER, MARQUE_LIMITE_EASY_APPLY + " LinkedIn affiche la limite "
                                "quotidienne de candidatures Easy Apply : offre reportée.",
                                "limite_easy_apply.png")
            # Pré-remplissage LOCAL des champs évidents (prénom, email…) : sans appel LLM.
            self._preremplir(historique, etape)
            champ_fichier_present = '"input_fichier"' in etat.split("--- Champs fichier ---")[-1]
            if champ_fichier_present:
                champ_fichier_vu = True
            # Certains formulaires posent leurs questions ouvertes AVANT de réclamer le CV.
            # Si on attend le champ fichier pour générer, le modèle répond à ces questions
            # sans CV ni lettre — donc sans expériences ni projets (profile.yaml n'en a pas).
            # Dès qu'on est sur un vrai formulaire, on génère les documents.
            formulaire_atteint = (champ_fichier_present
                                  or sum(c in etat for c in ("(input", "(textarea", "(select")) >= 3)

            # Le CV adapté est-il DÉJÀ présent sur la page ? (ex. LinkedIn reprend une candidature en
            # cours et rouvre à l'écran de revue : le CV a été déposé lors d'un essai précédent.) Si oui,
            # on considère le dépôt fait -> on n'insiste PAS pour retrouver un champ CV inexistant, ce qui
            # évitait de boucler en fin de formulaire sans jamais envoyer.
            if documents["prets"] and not cv_depose and self.nav.fichier_present(documents["cv"].name):
                cv_depose = True
                self.logger.info(f"  CV adapté déjà présent sur la page ({documents['cv'].name}) "
                                 f"-> dépôt considéré comme fait, pas besoin de le redéposer.")
            if documents["prets"] and not lm_depose and self.nav.fichier_present(documents["lm"].name):
                lm_depose = True

            # On est sur le formulaire -> on GÉNÈRE le CV + la lettre MAINTENANT, avant de
            # répondre à quoi que ce soit, puis on relit la page (la génération a pu changer d'onglet).
            if formulaire_atteint and not documents["prets"]:
                self.logger.info("  Formulaire détecté -> génération du CV et de la lettre "
                                 "(avant de répondre aux questions)…")
                try:
                    assurer_documents()
                except ChatEpuise:
                    # Le compte DeepSeek de CE navigateur est saturé. Ce n'est pas un échec de
                    # la candidature : on laisse remonter pour que l'orchestrateur bascule sur
                    # le navigateur suivant et rejoue l'offre. Sans ce passage, l'exception
                    # tombait dans le « except Exception » ci-dessous et l'offre était perdue.
                    raise
                except Exception as e:  # noqa: BLE001
                    return resultat(em.ERREUR, f"Génération du CV/lettre impossible : {e}", f"gen_etape{etape}.png")
                page_changee = True   # la génération a pu changer d'onglet / recharger la page
                continue

            # Blocage : la page n'a pas changé du tout pendant 4 étapes -> arrêt (le bilan analysera)
            etats_recents.append(hash(etat))
            if len(etats_recents) >= 4 and len(set(etats_recents[-4:])) == 1:
                ok, secours_utilises = self._secours_vision(
                    "la page n'évolue plus malgré les actions : " + " / ".join(historique[-3:]),
                    contexte_fixe, historique, etape, secours_utilises, soumission_cliquee)
                if ok:
                    etats_recents.clear()
                    page_changee = True
                    continue
                return resultat(em.ERREUR, "Blocage : la page n'évolue plus malgré les actions (4 étapes)",
                                f"blocage_etape{etape}.png")

            # Onglet secondaire (boîte mail) : on ne s'y éternise pas. Passé ce plafond, la
            # confirmation est considérée comme faite — le message a pu être déjà lu, ou le
            # lien déjà cliqué — et on revient au formulaire, qui est le but réel.
            if self._onglet_secondaire:
                self._etapes_onglet_secondaire += 1
                if self._etapes_onglet_secondaire >= self.MAX_ETAPES_ONGLET_SECONDAIRE:
                    self.logger.warning(f"  Onglet secondaire ouvert depuis "
                                        f"{self._etapes_onglet_secondaire} étapes -> retour "
                                        f"forcé au formulaire, email considéré comme confirmé.")
                    historique.append(f"étape {etape} : retour forcé au formulaire (trop "
                                      f"d'étapes dans la boîte mail). Considère l'email comme "
                                      f"confirmé et reprends la connexion au site.")
                    self._fermer_onglet_secondaire()
                    page_changee = True
                    continue

            # BOUCLE : le robot revient une 3e fois sur la MÊME étape — DeepSeek a agi deux fois
            # sans franchir ce qui bloque (bouton « Next » sans effet, formulaire qui se referme…).
            # Le secours vision prend la main, une seule fois par étape. Jamais sur une page de
            # connexion (mot de passe) ou de CAPTCHA, qui gardent leur propre logique. Si le
            # secours n'est pas disponible ou échoue, rien ne change : le robot continue.
            signature = self._signature_etape(etat)
            visites_etapes[signature] = visites_etapes.get(signature, 0) + 1
            if (visites_etapes[signature] >= 3 and signature not in etapes_secourues
                    and "(input:password)" not in etat and "CAPTCHA visible détecté : oui" not in etat
                    and not ENVOI_FINAL_VISIBLE.search(etat)):
                etapes_secourues.add(signature)
                self.logger.info(f"  Boucle : 3e passage sur la même étape sans la franchir "
                                 f"-> secours vision.")
                ok, secours_utilises = self._secours_vision(
                    "le robot revient pour la 3e fois sur la même étape du formulaire sans la franchir "
                    "(les actions de DeepSeek n'ont pas corrigé ce qui bloque) : " + " / ".join(historique[-4:]),
                    contexte_fixe, historique, etape, secours_utilises, soumission_cliquee)
                if ok:
                    page_changee = True
                    continue

            if documents["prets"]:
                # Le CV adapté est joint : c'est la SEULE source d'expériences, de projets et de
                # compétences (profile.yaml n'en contient pas). Sans lui, les questions ouvertes
                # du type « décrivez votre expérience en X » restent sans réponse possible.
                bloc_docs = (f"<cv_adapte>\n{documents['cv_texte'][:6000]}\n</cv_adapte>\n\n"
                             f"<lettre>\n{documents['lettre'][:4000]}\n</lettre>\n\n"
                             f"<fichiers>\ncv : {documents['cv'].name}\nlm : {documents['lm'].name}\n</fichiers>\n\n")
            else:
                bloc_docs = ("<fichiers>\nLe CV et la lettre adaptés à l'offre seront générés "
                             "automatiquement dès qu'un champ de dépôt de fichier apparaît. Utilise l'action "
                             "televerser avec fichier 'cv' (et 'lm' s'il y a un champ lettre) normalement.\n"
                             "</fichiers>\n\n")

            message = (contexte_fixe + bloc_docs + self._bloc_compte_site()
                       + "<historique>\n" + ("\n".join(historique[-30:]) or "(début)") + "\n</historique>\n\n"
                       + f"<page>\n{etat}\n</page>\n\n"
                       + f"Étape {etape}/{self.max_etapes}. Quelles sont les prochaines actions ?")
            try:
                reponse = self.llm_formulaire.generer(
                    self.prompt, message, images=[capture] if capture else None, tache="formulaire")
                decision = extraire_json(reponse)
            except ValueError as e:
                historique.append(f"étape {etape} : réponse JSON invalide ({e}) — réponds uniquement en JSON")
                continue
            except ErreurLLM as e:
                return resultat(em.ERREUR, f"LLM indisponible pendant le formulaire : {e}")
            except ChatEpuise as e:
                # DeepSeek saturé. Normalement on laisse remonter : l'orchestrateur change de
                # navigateur et REJOUE l'offre. Mais si l'envoi final a déjà été cliqué, la
                # candidature est peut-être partie — la rejouer risquerait un DOUBLON chez le
                # recruteur. Dans ce cas seulement, on s'arrête et on demande une vérification.
                if soumission_cliquee:
                    return resultat(em.ACTION_MANUELLE,
                                    f"Envoi cliqué, puis DeepSeek saturé ({e}) : confirmation non "
                                    f"vérifiée — contrôle sur le site (aucun nouvel envoi fait).",
                                    f"sature_apres_envoi_etape{etape}.png")
                raise

            statut = str(decision.get("statut", "continuer")).lower()
            raison = str(decision.get("raison", "") or decision.get("analyse", ""))
            self.logger.info(f"  [formulaire {etape}] {decision.get('analyse', '')} -> {statut}")

            # JSON valide mais HORS FORMAT (ex. {"action","index","raison"} au singulier) :
            # sans « actions », le robot n'exécute rien et rejoue le même tour indéfiniment.
            # On le dit explicitement au modèle plutôt que de tourner à vide.
            if statut == "continuer" and not isinstance(decision.get("actions"), list):
                self.logger.warning(f"  [formulaire {etape}] réponse hors format "
                                    f"(clés reçues : {', '.join(list(decision)[:6]) or 'aucune'}) "
                                    f"-> rappel du format demandé.")
                historique.append(
                    f"étape {etape} : ta réponse était hors format et n'a donc RIEN exécuté. "
                    f"Clés reçues : {', '.join(list(decision)[:6]) or 'aucune'}. Le robot attend "
                    f'exactement : {{"analyse": "...", "actions": [{{"type": "cliquer", "index": 12}}], '
                    f'"statut": "continuer"}}. La clé est « actions » au PLURIEL et c\'est une LISTE ; '
                    f'chaque élément a un « type ». N\'utilise jamais {{"action", "index", "raison"}}.')
                continue

            # Réponse « manuel / échec » qui contient DÉJÀ des actions (le modèle a rempli tout ce
            # qu'il pouvait, puis signale un seul champ) : quand on le relance au lieu d'abandonner,
            # on exécute d'abord ces actions — sinon il doit tout renvoyer au tour suivant.
            # Jamais l'envoi final : le modèle a dit qu'il manquait quelque chose.
            actions_deja_la = [a for a in (decision.get("actions") or [])
                               if isinstance(a, dict) and not a.get("soumission_finale")]

            if statut == "termine":
                self.actions_menees = actions_menees   # séquence gagnante -> playbook du site
                return resultat(em.POSTULE, raison or "Confirmation de candidature détectée", "confirmation.png")

            # RÉCUPÉRATION AVANT D'ABANDONNER : si le modèle veut arrêter (manuel/échec) pour un
            # problème qui n'est PAS un blocage dur (captcha, compte, offre expirée…), on ne lâche
            # pas tout de suite. On prend une capture d'écran du problème et on redemande au modèle
            # de bien la REGARDER et de corriger (ex. champ ville à autocomplétion, champ oublié).
            if (statut in ("manuel", "echec") and recuperations < MAX_RECUPERATIONS
                    and not soumission_cliquee
                    and not any(m in raison.lower() for m in BLOCAGES_DURS)):
                recuperations += 1
                self.nav.capture(dossier_captures / f"probleme_etape{etape}_essai{recuperations}.png")
                self.logger.info(f"  Problème signalé (« {raison[:80]} ») -> capture + nouvelle tentative "
                                 f"guidée {recuperations}/{MAX_RECUPERATIONS}")
                historique.append(
                    f"étape {etape} : PROBLÈME signalé « {raison} ». NE PAS ABANDONNER. Regarde "
                    f"attentivement la capture d'écran jointe pour voir l'état réel :\n"
                    f"  • Champ ville / localisation en erreur (« Please enter a valid answer ») -> "
                    f"utilise l'action « saisir_suggestion » avec « Ville, Pays » (elle choisit la "
                    f"1re suggestion). NE cherche pas à fermer la liste ni à continuer avant.\n"
                    f"  • Un champ obligatoire est vide ou en rouge -> remplis-le d'après le profil.\n"
                    f"  • Le champ fichier n'accepte que des images -> ce n'est PAS le champ CV, "
                    f"cherche (en défilant) le vrai champ CV qui accepte le PDF.\n"
                    f"Corrige puis continue. Ne renvoie « manuel »/« echec » que si c'est vraiment "
                    f"impossible (CAPTCHA non résolu, inscription impossible, offre expirée)."
                    + (" Les actions de ta réponse ont déjà été exécutées : ne les renvoie pas, "
                       "complète seulement ce qui manque." if actions_deja_la else ""))
                if not actions_deja_la:
                    continue
                decision["actions"], statut = actions_deja_la, "continuer"   # exécutées ci-dessous

            # Connexion / compte / CAPTCHA / SMS : le modèle a déjà appliqué les règles 3 et 4 du
            # prompt (connexion, création du compte, confirmation de l'email, clic sur la case).
            # S'il s'arrête quand même, on tente ce qui reste, sinon « Action manuelle ».
            if statut in ("manuel", "echec") and any(m in raison.lower() for m in BLOCAGES_HUMAINS) \
                    and "expir" not in raison.lower() and "plus disponible" not in raison.lower():
                # 1) D'abord : « Continuer avec Google » (session Google déjà ouverte dans le
                #    Chrome du robot).
                if self._tenter_connexion_google(historique, etape):
                    historique.append(f"étape {etape} : connecté via Google — la page a changé, "
                                      f"poursuis la candidature normalement (ne redemande pas de connexion).")
                    page_changee = True
                    recuperations = 0
                    continue
                # 2) CAPTCHA simple (checkbox / reCAPTCHA v2 / hCaptcha / Turnstile) :
                #    tentative de résolution automatique via 2Captcha (si captcha.actif=true en config).
                #    On n'essaie que si la raison mentionne « captcha » ou « robot » : les cas « compte »,
                #    « connexion » etc. ne peuvent PAS être résolus par 2Captcha et vont directement au
                #    geste humain ou au passage à l'offre suivante.
                if any(m in raison.lower() for m in ("captcha", "robot")):
                    if self._resoudre_captcha_nopecha(dossier_captures, etape):
                        historique.append(f"étape {etape} : CAPTCHA résolu automatiquement par NopeCHA — "
                                          f"reprise de la candidature.")
                        page_changee = True
                        recuperations = 0
                        continue
                    # 2Captcha : conservé mais désactivé par défaut (captcha.actif: false).
                    if self._resoudre_captcha_2captcha(dossier_captures, etape):
                        historique.append(f"étape {etape} : CAPTCHA résolu automatiquement via 2Captcha — "
                                          f"reprise de la candidature.")
                        page_changee = True
                        recuperations = 0
                        continue
                # 2 bis) Le modèle s'est arrêté sur une connexion ou un compte SANS avoir tenté
                #    la règle 3 du prompt (connexion, puis création, puis confirmation de
                #    l'email). On lui rappelle cette règle une fois, dans les deux modes.
                #    Pas de relance quand la raison dit déjà que la tentative a échoué
                #    (« Inscription impossible »), ni pour un CAPTCHA ou un SMS : là, l'offre
                #    passe directement en « Action manuelle ».
                if (relances_inscription < MAX_RELANCES_INSCRIPTION
                        and not any(m in raison.lower() for m in ("captcha", "robot", "sms", "impossible"))):
                    relances_inscription += 1
                    self._inscription_en_cours = True
                    self.logger.info(f"  Connexion / compte : règle 3 rappelée au modèle "
                                     f"({relances_inscription}/{MAX_RELANCES_INSCRIPTION}).")
                    historique.append(
                        f"étape {etape} : tu t'es arrêté (« {raison} ») sans appliquer la règle 3. "
                        f"Ne t'arrête pas, dans cet ordre : (a) bouton « Continuer avec Google » "
                        f"s'il existe ; (b) connexion avec l'email du profil et le mot de passe "
                        f"{JETON_MOT_DE_PASSE} ; (c) si elle échoue, création du compte avec ce même "
                        f"email et {JETON_MOT_DE_PASSE} ; (d) si le site demande de confirmer l'email : "
                        f"« ouvrir_onglet » sur https://mail.google.com/, lien de confirmation, "
                        f"« attendre », puis « fermer_onglet ». Ne renvoie « manuel » que si la "
                        f"création du compte a échoué (« Inscription impossible : <cause> »).")
                    page_changee = True
                    recuperations = 0
                    continue
                # 3) Sinon : attente d'un geste humain, puis reprise là où on en était.
                #    Mode 4 : seulement si pause_intervention_humaine est à true.
                #    Mode 5 : délai attente_inscription_secondes (0 = aucune attente,
                #    l'offre passe directement en « Action manuelle »).
                if self._attendre_intervention_humaine(
                        raison, dossier_captures, etape,
                        forcer=self.tout_postuler,
                        delai=self.attente_inscription if self.tout_postuler else None,
                        consigne=("Fais le geste demandé dans le Chrome du robot (CAPTCHA à "
                                  "défi, code SMS, inscription)." if self.tout_postuler else "")):
                    historique.append(f"étape {etape} : intervention humaine effectuée (CAPTCHA / connexion). "
                                      f"La page a changé, poursuis la candidature normalement.")
                    page_changee = True
                    recuperations = 0
                    continue
                return resultat(em.ACTION_MANUELLE, raison or "CAPTCHA / création de compte : à faire manuellement",
                                f"manuel_etape{etape}.png")

            if statut == "manuel":
                # Mode « postuler à tout » : arrivé ici, la raison n'est PAS un blocage réel —
                # captcha, compte et connexion ont déjà été traités juste au-dessus et sortent
                # par leur propre chemin. C'est donc une hésitation du modèle (champ obligatoire
                # sans réponse évidente dans le profil, question inattendue). On lui rend la main
                # avec une consigne ferme au lieu d'abandonner la candidature.
                if self.tout_postuler and relances_manuel < MAX_RELANCES_MANUEL:
                    relances_manuel += 1
                    self.logger.info(f"  Mode « postuler à tout » : « manuel » refusé "
                                     f"({relances_manuel}/{MAX_RELANCES_MANUEL}) — raison : {raison or 'aucune'}")
                    historique.append(
                        f"étape {etape} : « manuel » REFUSÉ ({relances_manuel}/{MAX_RELANCES_MANUEL}). "
                        f"Raison que tu as donnée : {raison or 'aucune'}. Ce n'est pas un obstacle "
                        f"insurmontable. Réponds TOI-MÊME avec la valeur la plus raisonnable d'après "
                        f"<profil> et <cv_adapte> ; pour un champ obligatoire sans réponse évidente, "
                        f"choisis l'option la plus neutre proposée. Puis CONTINUE jusqu'à l'envoi. "
                        f"Ne renvoie « manuel » que pour un CAPTCHA non résolu, une vérification "
                        f"par SMS ou une inscription impossible (règle 3)."
                        + (" Les actions de ta réponse ont déjà été exécutées : ne les renvoie pas, "
                           "complète seulement ce qui manque." if actions_deja_la else ""))
                    page_changee = True
                    if not actions_deja_la:
                        continue
                    decision["actions"], statut = actions_deja_la, "continuer"   # exécutées ci-dessous
                ok, secours_utilises = self._secours_vision(
                    raison, contexte_fixe, historique, etape, secours_utilises, soumission_cliquee)
                if ok:
                    page_changee = True
                    recuperations = 0
                    continue
                return resultat(em.ACTION_MANUELLE, raison or "Intervention humaine requise", f"manuel_etape{etape}.png")
            if statut == "echec":
                if "expir" in raison.lower() or "plus disponible" in raison.lower():
                    return resultat(em.OFFRE_EXPIREE, raison, f"echec_etape{etape}.png")
                if soumission_cliquee:
                    # On ne retente pas : risque de candidature en double
                    return resultat(em.ACTION_MANUELLE, f"Soumission cliquée puis échec : {raison} — à vérifier",
                                    f"echec_etape{etape}.png")
                ok, secours_utilises = self._secours_vision(
                    raison, contexte_fixe, historique, etape, secours_utilises, soumission_cliquee)
                if ok:
                    page_changee = True
                    recuperations = 0
                    continue
                return resultat(em.ERREUR, raison or "Échec du formulaire", f"echec_etape{etape}.png")

            actions = decision.get("actions") or []
            if not isinstance(actions, list):
                actions = []
            self._renumerotation = {}   # ancien numéro (vu par le modèle) -> numéro actuel
            for action in actions:
                if not isinstance(action, dict):
                    continue
                if "index" in action:
                    # Numéro du modèle -> numéro actuel si la page a déjà été renumérotée dans
                    # cette série, puis vérification que c'est toujours le même élément.
                    try:
                        action["index"] = self._renumerotation.get(int(action["index"]), int(action["index"]))
                    except (TypeError, ValueError):
                        pass
                    note = self._verifier_index(action)
                    if note:
                        self.logger.info(f"    {note}")
                    # L'élément visé n'existe plus : cliquer ce numéro viserait un AUTRE élément
                    # (chez JD.COM : « Skip to main content »). On n'exécute pas, on relit la page.
                    if note and "introuvable" in note:
                        historique.append(f"étape {etape} : action NON exécutée ({json.dumps(action, ensure_ascii=False)}) "
                                          f"— {note}. La page a changé : relis la nouvelle liste et la capture.")
                        page_changee = True
                        break
                if action.get("type") == "cliquer" and action.get("soumission_finale"):
                    libelle = self._libelles.get(int(action.get("index", -1)), "")
                    if BOUTONS_NAVIGATION.search(libelle):
                        action["soumission_finale"] = False   # « Suivant » n'est pas l'envoi final
                if action.get("type") == "cliquer" and action.get("soumission_finale"):
                    # Verrou : jamais d'envoi sans avoir déposé le CV adapté à CETTE offre
                    # (sinon le site utiliserait un ancien CV présélectionné)
                    if champ_fichier_vu and not cv_depose:
                        nom_cv = documents["cv"].name if documents["prets"] else "le CV adapté"
                        historique.append(
                            f"étape {etape} : ENVOI BLOQUÉ par le robot — le CV adapté à cette offre "
                            f"({nom_cv}) n'a pas encore été déposé. Utilise l'action « televerser » "
                            f"avec fichier « cv » (et « lm » s'il y a un champ lettre), même si un ancien CV est "
                            f"déjà sélectionné, puis vérifie qu'il est sélectionné avant d'envoyer.")
                        break
                    # Verrou : CV ET lettre adaptés à CETTE offre. Indeed / Glassdoor reprennent
                    # souvent directement à la page de revue avec les fichiers d'une candidature
                    # PRÉCÉDENTE, sans aucun champ fichier visible. Avant l'envoi, on retient UNE
                    # fois pour le CV puis UNE fois pour la lettre : le robot clique lui-même
                    # « Edit resume » / « Add supporting documents » (souvent dans une iframe que
                    # le modèle ne peut pas viser), puis le modèle dépose le fichier.
                    genre = ("cv" if not cv_depose and not rappel_cv
                             else "lm" if not lm_depose and not rappel_lm else "")
                    if genre == "lm":
                        # Page de revue ENTIÈREMENT affichée (sa section CV, avec « Edit resume »,
                        # est là) mais AUCUNE section pour la lettre : l'employeur n'en accepte
                        # pas (ex. MuseIA). Inutile de chercher ni de retenir l'envoi.
                        # Idem quand le CV vient d'être déposé DANS ce formulaire (LinkedIn Easy
                        # Apply : un seul champ fichier, pour le CV) et qu'aucune section lettre
                        # n'existe : on est à l'envoi final, il n'y a rien d'autre à déposer.
                        self.nav.attendre_apres_action()
                        if ((cv_depose or self.nav.cliquer_bouton_documents("cv", cliquer=False))
                                and not self.nav.section_lettre_presente()):
                            rappel_lm = True
                            genre = ""
                            self.logger.info("  Page de revue sans section pour la lettre : "
                                             "l'employeur n'en demande pas -> envoi sans lettre.")
                    if genre:
                        # La tentative n'est « consommée » que si le bouton a été trouvé (ou après
                        # 2 recherches vaines) : le modèle marque parfois « Continue » comme envoi
                        # final alors qu'on n'est pas encore sur la page de revue.
                        try:
                            assurer_documents()
                        except ChatEpuise:
                            raise
                        except Exception as e:  # noqa: BLE001 — sans documents, on n'insiste pas
                            self.logger.warning(f"  Génération du CV/lettre impossible avant l'envoi : {e}")
                        if not documents["prets"]:
                            rappel_cv = rappel_lm = True   # rien à déposer : ne pas réessayer
                        if documents["prets"]:
                            fichier = documents[genre].name
                            quoi = "le CV" if genre == "cv" else "la lettre de motivation"
                            self.nav.attendre_apres_action()
                            empreinte = self.nav.empreinte_page()
                            clic = self.nav.cliquer_bouton_documents(genre, attente=6.0)
                            if not clic:
                                recherches_ratees[genre] += 1
                            if clic or recherches_ratees[genre] >= 2:
                                if genre == "cv":
                                    rappel_cv = True
                                else:
                                    rappel_lm = True
                            if clic:
                                self.nav.attendre_apres_action(empreinte)
                                self.logger.info(f"  Envoi retenu : {quoi} ({fichier}) pas encore déposé "
                                                 f"-> le robot a cliqué « {clic} ».")
                                historique.append(
                                    f"étape {etape} : ENVOI RETENU — {quoi} adapté(e) à CETTE offre "
                                    f"({fichier}) n'avait pas été déposé(e). Le robot a cliqué lui-même "
                                    f"« {clic} ». Sur la page qui s'ouvre, dépose « {genre} » avec "
                                    f"« televerser » (il REMPLACE tout ancien fichier), vérifie que "
                                    f"{fichier} est sélectionné, puis « Continue » jusqu'à la revue et envoie."
                                    + (" Pour la lettre : UNIQUEMENT le fichier « lm » dans le champ "
                                       "fichier (« Upload a file / Additional documents ») ; ne touche "
                                       "pas à la zone « Write a cover letter »."
                                       if genre == "lm" else ""))
                            else:
                                self.logger.info(f"  Envoi retenu : {quoi} ({fichier}) pas encore déposé "
                                                 f"(aucun bouton de modification trouvé).")
                                historique.append(
                                    f"étape {etape} : ENVOI RETENU — {quoi} adapté(e) à CETTE offre "
                                    f"({fichier}) n'a pas été déposé(e) (la page peut montrer un fichier "
                                    f"d'une candidature précédente). Cherche un lien « Modifier / Edit » "
                                    f"(CV) ou « Add a cover letter / Ajouter / Supporting documents » "
                                    f"(lettre), clique-le et dépose « {genre} » avec « televerser ». S'il "
                                    f"n'existe VRAIMENT aucun moyen, renvoie la soumission finale.")
                            page_changee = True
                            break
                    if not self.soumission_auto:
                        bouton = self._libelles.get(int(action.get("index", -1)), "?")
                        return resultat(em.PRET_TEST,
                                        f"Formulaire rempli, envoi NON effectué (mode test) — arrêt avant le "
                                        f"bouton : {bouton}", "pret_avant_envoi.png")
                    soumission_cliquee = True
                # Clic sur un bouton d'upload (« Upload resume », « Parcourir »…) : au lieu de le
                # refuser (fenêtre Windows bloquante), on dépose le fichier PAR ce bouton, fenêtre
                # interceptée par Chrome. Lettre si le bouton en parle, CV sinon.
                if action.get("type") == "cliquer" and str(action.get("index", "")).isdigit():
                    lib = self._libelles.get(int(action["index"]), "")
                    vis = _libelle_reel(lib) or lib
                    if BOUTONS_FICHIER.search(vis) and "(input:file)" not in lib:
                        genre = "lm" if BOUTON_LETTRE.search(vis) else "cv"
                        action = {"type": "televerser", "fichier": genre, "index": int(action["index"])}
                description = json.dumps(action, ensure_ascii=False)
                if action.get("type") == "cliquer":
                    description += f" [{self._libelles.get(int(action.get('index', -1)), '?')}]"
                # Double sécurité : aucune description d'action (journal, historique envoyé à
                # DeepSeek) ne contient un vrai mot de passe.
                description = self._masquer(description)
                if action.get("type") == "televerser":
                    assurer_documents()   # au cas où l'upload arrive sans champ fichier détecté avant
                ids_avant = (self._ids_onglets()
                             if self._onglet_secondaire and action.get("type") == "cliquer" else None)
                url_avant = ""
                if ids_avant is not None:
                    try:
                        url_avant = self.nav.url()
                    except Exception:  # noqa: BLE001
                        url_avant = ""
                try:
                    compte_rendu = self._executer(action, fichiers_actuels())
                except Exception as e:  # noqa: BLE001 — une action ratée n'arrête pas la boucle
                    compte_rendu = f"ÉCHEC : {e}"
                # Lien de confirmation / réinitialisation ouvert dans un NOUVEL onglet : on y passe
                # (sinon le robot restait sur Gmail et la page du lien était ignorée). Gmail reste
                # ouvert ; « fermer_onglet » refermera les deux une fois l'opération terminée.
                if ids_avant is not None:
                    time.sleep(1.5)
                    nouveaux = [i for i in self._ids_onglets() if i not in ids_avant]
                    if not nouveaux:
                        # Le clic n'a RIEN ouvert (bouton « Vérifiez votre adresse email » de
                        # Teamtailor, cliqué 10 fois en vain — vu le 26/09) : le robot ouvre
                        # lui-même l'adresse COMPLÈTE du lien (jeton compris) dans un onglet.
                        nouveau = self._ouvrir_lien_de_la_boite(action, url_avant)
                        if nouveau:
                            nouveaux = [nouveau]
                    if nouveaux:
                        self._onglets_lien.append(nouveaux[-1])
                        self.nav.epingler(nouveaux[-1])
                        self.nav.attendre_page_prete(15)
                        self.logger.info("  Le lien s'est ouvert dans un nouvel onglet -> le robot y passe "
                                         "(Gmail reste ouvert à côté).")
                        compte_rendu = (f"{compte_rendu} — le lien s'est ouvert dans un NOUVEL onglet et le "
                                        f"robot y est passé (Gmail reste ouvert). Termine ICI la confirmation "
                                        f"ou la réinitialisation, vérifie le message de succès, puis seulement "
                                        f"« fermer_onglet ».")
                # Fenêtre JavaScript du site (« CV téléchargé avec succès » + OK) fermée par le
                # surveillant pendant l'action : le modèle en connaît le message.
                alertes = self.nav.alertes_fermees()
                if alertes:
                    compte_rendu = (f"{compte_rendu} — le site a affiché une fenêtre, fermée "
                                    f"automatiquement (OK) : « {' / '.join(alertes)[:300]} »")
                compte_rendu = self._masquer(str(compte_rendu))
                self.logger.info(f"    action : {description} -> {str(compte_rendu)[:200]}")
                if self.nav.nopecha_actif and action.get("type") != "attendre" and self.nav.captcha_a_resoudre():
                    etat_np = self.nav.attendre_nopecha()
                    self.logger.info(f"    CAPTCHA apparu après l'action -> NopeCHA : {etat_np}.")
                    historique.append(
                        f"étape {etape} : un CAPTCHA est apparu après {description} ; résolution "
                        f"automatique : {etat_np}. Les actions suivantes n'ont PAS été exécutées : "
                        f"regarde la nouvelle capture et reprends.")
                    page_changee = True
                    break
                if str(compte_rendu).startswith(PAGE_BLOQUEE):
                    return resultat(em.ACTION_MANUELLE,
                                    f"Connexion bloquée : la page ne se charge pas "
                                    f"({str(compte_rendu)[len(PAGE_BLOQUEE):].strip(' :')[:200]})",
                                    f"bloquee_etape{etape}.png")
                # Formulaire disparu pendant la confirmation de l'email : continuer n'aurait
                # aucun sens, la page n'existe plus. On remonte pour rejouer la candidature.
                if self._formulaire_perdu:
                    raise InscriptionARejouer(
                        "formulaire d'inscription perdu pendant la confirmation de l'email")
                # Sélection non confirmée : le composant React n'a pas mis à jour la valeur.
                # On injecte un avertissement explicite pour guider le LLM.
                if (action.get("type") == "selectionner"
                        and "ATTENTION valeur non confirmée" in str(compte_rendu)):
                    historique.append(
                        f"étape {etape} : AVERTISSEMENT — la sélection dans la liste déroulante n'a pas "
                        f"été confirmée par le composant (React n'a pas mis à jour). Pour débloquer :\n"
                        f"  • Défiler d'abord vers le bas pour t'assurer que le champ est visible à l'écran.\n"
                        f"  • Réessaie « selectionner » avec l'option EXACTE telle qu'elle apparaît dans la liste.\n"
                        f"  • Si ça échoue encore, clique sur le champ déroulant (action « cliquer »), attends "
                        f"une étape, puis réessaie « selectionner » — le clic force React à initialiser l'écouteur.\n"
                        f"  • En dernier recours, renvoie « manuel » avec la raison précise.")
                if action.get("type") == "televerser" and str(compte_rendu).startswith("Uploaded"):
                    if str(action.get("fichier", "cv")).lower() == "cv":
                        cv_depose = True
                    elif str(action.get("fichier", "")).lower() == "lm":
                        lm_depose = True
                historique.append(f"étape {etape} : {description} -> {str(compte_rendu)[:200]}")
                dernieres_actions.append(f"{description} -> {str(compte_rendu)[:120]}")  # action + effet
                # On mémorise les actions ayant réussi (pour le playbook du site, rejoué sans LLM)
                if not str(compte_rendu).startswith(("ÉCHEC", "REFUSÉ")):
                    enregistrement: dict = {}
                    ta = action.get("type")
                    cible = _cible_exploitable(self._libelles.get(int(action.get("index", -1)), ""))
                    if ta == "cliquer":
                        if cible and not CLICS_INTERDITS.search(cible):
                            enregistrement = {"type": "cliquer", "cible": cible,
                                              "soumission_finale": bool(action.get("soumission_finale"))}
                    elif ta == "saisir":
                        if cible:
                            enregistrement = {"type": "saisir", "cible": cible,
                                              "valeur": str(action.get("valeur", ""))[:60]}
                    elif ta == "televerser":
                        enregistrement = {"type": "televerser",
                                          "fichier": str(action.get("fichier", "cv")).lower()}
                        if cible and "input_fichier" not in action:
                            enregistrement["cible"] = cible   # dépôt par le bouton du site
                    elif ta == "defiler" and not str(action.get("index", "")).strip():
                        enregistrement = {"type": "defiler", "sens": str(action.get("sens", "bas"))}
                    if enregistrement:
                        actions_menees.append(enregistrement)
                scrolls_consecutifs = scrolls_consecutifs + 1 if action.get("type") == "defiler" else 0
                # ouvrir_onglet / fermer_onglet changent d'onglet (boîte mail <-> formulaire) :
                # même traitement qu'un clic, pour que le modèle voie la nouvelle page en capture.
                # Une case à cocher ou un bouton radio NE change PAS de page : on enchaîne les
                # actions suivantes (toute la page se remplit en un seul tour). Seul un vrai
                # bouton / lien (Continuer, Suivant, Envoyer…) interrompt la liste.
                try:
                    libelle_clic = self._libelles.get(int(action.get("index", -1)), "")
                except (TypeError, ValueError):
                    libelle_clic = ""
                clic_case = (action.get("type") == "cliquer"
                             and ("[coché]" in libelle_clic or "[non coché]" in libelle_clic))
                if clic_case:
                    page_changee = True   # la capture suivante montrera les cases cochées
                elif (action.get("type") == "repondre_technique"
                      and not str(compte_rendu).startswith("Typed")):
                    # Question ouverte restée SANS réponse : surtout pas de « Suivant » dans la
                    # foulée (le champ partirait vide). Le modèle revoit la page et la remplit.
                    page_changee = True
                    break
                elif action.get("type") in ("cliquer", "aller", "ouvrir_onglet", "fermer_onglet"):
                    page_changee = True   # la page a pu changer : capture à la prochaine étape
                    break  # la page a pu changer : on relit l'état avant d'agir à nouveau

            # Anti-boucle de DÉFILEMENT : le modèle défile parfois sans fin dans une longue section
            # déjà pré-remplie (expérience, formation) car il « veut tout voir ». Après 3 scrolls
            # d'affilée, on le force à agir (cliquer le bouton de navigation) au lieu de re-défiler.
            if scrolls_consecutifs >= 3:
                historique.append(
                    f"étape {etape} : tu as défilé {scrolls_consecutifs} fois de suite SANS rien faire. "
                    f"ARRÊTE de défiler. Les sections déjà pré-remplies (expérience professionnelle, "
                    f"formation) n'ont PAS besoin d'être lues en entier. Regarde la capture et CLIQUE "
                    f"MAINTENANT le bouton de navigation visible (Suivant / Vérifier / Envoyer la "
                    f"candidature). Remplis d'abord un éventuel champ obligatoire VIDE visible à l'écran.")
                scrolls_consecutifs = 0

            # Détection de boucle : la même action 3 fois de suite sans progrès.
            # 1re fois : on prévient le modèle ; 2e fois : on arrête (le bilan proposera un rattrapage).
            if len(dernieres_actions) >= 3 and len(set(dernieres_actions[-3:])) == 1:
                boucles += 1
                dernieres_actions.clear()
                if boucles >= 2:
                    return resultat(em.ERREUR, f"Blocage : la même action est répétée sans effet ({etape} étapes)",
                                    f"boucle_etape{etape}.png")
                historique.append(f"étape {etape} : BOUCLE DÉTECTÉE — la même action a été répétée 3 fois sans "
                                  f"effet. Regarde la capture et change d'approche (autre élément, défiler, "
                                  f"attendre) ou renvoie « echec » avec la raison.")

            if not actions:
                time.sleep(self.nav.delai)

        if soumission_cliquee:
            return resultat(em.ACTION_MANUELLE,
                            "Soumission cliquée mais confirmation non détectée — vérifie sur le site", "max_etapes.png")
        return resultat(em.ERREUR, f"Formulaire non terminé après {self.max_etapes} étapes", "max_etapes.png")

    # ------------------------------------------------------------------

    @staticmethod
    def _signature_etape(etat: str) -> str:
        """
        Identité d'une ÉTAPE du formulaire, stable d'un passage à l'autre : l'adresse (sans
        paramètres) et la liste des éléments interactifs SANS leurs numéros, les valeurs saisies,
        les états (coché…) ni les identifiants aléatoires que le site change à chaque ouverture
        (« radio-group-«r3r» »). Deux étapes différentes n'ont pas les mêmes éléments.
        """
        url = re.search(r"^URL : (\S+)", etat, re.MULTILINE)
        base = (url.group(1).split("?")[0] if url else "")
        bloc = etat.split("--- Éléments interactifs ---", 1)[-1].split("--- Champs fichier ---", 1)[0]
        elements = set()
        for ligne in bloc.splitlines():
            ligne = re.sub(r"^\[\d+\]\s*", "", ligne.strip())
            ligne = ligne.split("  «")[0].split(" → ")[0].split("  |")[0]
            ligne = re.sub(r"«[^»]*»|\[[^\]]*\]|\d+", "", ligne)
            ligne = re.sub(r"\s+", " ", ligne).strip().lower()
            if ligne:
                elements.add(ligne)
        return base + "|" + "|".join(sorted(elements))

    def _secours_vision(self, probleme: str, contexte: str, historique: list, etape: int,
                        utilises: int, soumission_cliquee: bool) -> tuple[bool, int]:
        """
        Dernière chance avant un abandon : le secours vision (voir secours_vision.py) débloque
        l'étape en cliquant sur la capture, puis la main revient au robot normal. Renvoie
        (débloqué ?, nombre de secours utilisés). Jamais après l'envoi final, jamais plus de
        max_secours_par_candidature fois, et ne lève jamais : en cas de souci, (False, …) et le
        robot abandonne exactement comme avant.
        """
        s = getattr(self, "secours", None)
        if s is None or soumission_cliquee or utilises >= s.max_par_candidature or not s.disponible():
            return False, utilises
        utilises += 1
        try:
            ok, compte_rendu = s.debloquer(probleme, contexte)
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"  Secours vision : erreur inattendue ({e}) -> comportement habituel.")
            return False, utilises
        if ok:
            historique.append(f"étape {etape} : le SECOURS VISION a agi sur la page : {compte_rendu}. "
                              f"Regarde la page ACTUELLE et reprends la candidature à partir d'elle.")
        return ok, utilises

    def fermer_chat(self) -> None:
        """
        Ferme la conversation du formulaire. Appelé par l'orchestrateur APRÈS le bilan, pour
        que celui-ci puisse se faire dans la conversation qui a vu les pages.
        """
        fermer = getattr(self.llm_formulaire, "fermer", None)
        if callable(fermer):
            try:
                fermer()
            except Exception as e:  # noqa: BLE001 — un nettoyage raté ne doit rien casser
                self.logger.debug(f"Fermeture du chat du formulaire impossible ({e})")

    def bilan(self, offre: dict, url: str, res: ResultatCandidature, lecons: list[str]) -> dict:
        """
        Analyse une tentative (réussie ou non) : cause, stratégie de rattrapage et leçon pour ce site.
        Renvoie {} si l'analyse est impossible (ne bloque jamais le robot).
        """
        message = (
            f"<offre>\nEntreprise : {offre.get('nom_entreprise', '')}\nPoste : {offre.get('titre_poste', '')}\n"
            f"Lien : {url}\nURL au moment de l'arrêt : {res.url_finale}\n</offre>\n\n"
            f"<resultat>\nStatut : {res.statut}\nCommentaire : {res.commentaire}\n"
            f"Bouton d'envoi cliqué : {'oui' if res.soumission_cliquee else 'non'}\n</resultat>\n\n"
            "<lecons_deja_connues>\n" + ("\n".join(f"- {l}" for l in lecons) or "(aucune)") + "\n</lecons_deja_connues>\n\n"
            "<historique>\n" + ("\n".join(res.historique[-40:]) or "(vide)") + "\n</historique>"
        )
        try:
            reponse = self.llm.generer(self.prompt_bilan, message,
                                       images=[res.capture] if res.capture else None, tache="diagnostic")
            bilan = extraire_json(reponse)
            return bilan if isinstance(bilan, dict) else {}
        except Exception as e:  # noqa: BLE001
            # Le bilan est un CONFORT : il ne doit jamais faire tomber la candidature ni, depuis
            # qu'il se fait dans la conversation du formulaire, empêcher celle-ci d'être fermée.
            # Le moteur peut lever ses propres erreurs (délai du chat, onglet perdu) : on les
            # attrape toutes, comme le promet la docstring.
            self.logger.warning(f"Bilan de la tentative impossible : {e}")
            return {}

    # ------------------------------------------------------------------
    # PLAYBOOK : rejoue sans LLM une séquence d'actions qui a déjà réussi sur ce site
    # ------------------------------------------------------------------

    def replay_playbook(self, url_offre: str, offre: dict, fournir_documents,
                        dossier_captures: Path, actions: list[dict]) -> ResultatCandidature | None:
        """
        Rejoue la séquence mémorisée pour ce site, SANS appeler le LLM (beaucoup plus rapide).
        Renvoie un ResultatCandidature, ou None si le playbook n'est plus valide (le site a
        changé) : l'appelant repasse alors en mode normal (LLM).
        """
        self.nav.onglet_unique()
        self.nav.aller(url_offre)
        self.actions_menees = []          # un replay ne réécrit pas le playbook
        self._prefill_essais = {}         # compteur de pré-remplissage, propre à cette offre
        documents: dict = {"prets": False, "cv": None, "lm": None}
        historique: list[str] = ["replay du playbook du site (sans LLM)"]
        soumission_cliquee = False
        cv_depose = False                 # le CV adapté a été déposé pendant le replay

        def assurer_documents() -> None:
            if not documents["prets"]:
                cv, lm, _ = fournir_documents()
                documents.update(prets=True, cv=Path(cv), lm=Path(lm))

        def resultat(statut: str, commentaire: str) -> ResultatCandidature:
            try:
                url = self.nav.url()
            except Exception:  # noqa: BLE001
                url = ""
            return ResultatCandidature(statut, commentaire, historique, soumission_cliquee, None, url)

        def trouver(cible: str) -> int | None:
            """Index de l'élément dont le libellé ressemble à `cible` (None si introuvable)."""
            self.nav.elements()
            libelles = self.nav.libelles()
            cible_n = cible.lower()[:30]
            for idx, lib in libelles.items():
                if cible_n and cible_n in lib.lower():
                    return int(idx)
            return None

        for numero, action in enumerate(actions, start=1):
            type_action = action.get("type")
            self.logger.info(f"  [playbook {numero}/{len(actions)}] {type_action} {action.get('cible', '')}")
            # Un playbook peut être INCOMPLET : une action dont le libellé n'était pas
            # exploitable n'a pas été mémorisée (ex. le champ téléphone de LinkedIn). Le
            # pré-remplissage local comble ces trous — sans LLM — sinon on clique « Suivant »
            # avec un champ obligatoire vide et le formulaire refuse d'avancer.
            self._preremplir(historique, numero)
            try:
                if type_action == "televerser":
                    assurer_documents()
                    cle = str(action.get("fichier", "cv")).lower()
                    if cle not in ("cv", "lm"):
                        return None
                    compte_rendu = self.nav.televerser(documents[cle], 0)
                    if str(compte_rendu).startswith("ÉCHEC"):
                        # Pas de champ fichier (ou champ caché d'une autre rubrique) : le site
                        # dépose par un BOUTON (« Upload resume » de LinkedIn), créé au clic.
                        idx = trouver(str(action.get("cible", ""))) if action.get("cible") else None
                        if idx is None:
                            idx = self._index_bouton_upload(cle, frais=True)
                        if idx is not None:
                            empreinte = self.nav.empreinte_page()
                            compte_rendu = self.nav.televerser_par_bouton(idx, documents[cle])
                            self.nav.attendre_apres_action(empreinte)
                    if cle == "cv" and str(compte_rendu).startswith("Uploaded"):
                        cv_depose = True
                elif type_action == "defiler":
                    compte_rendu = self.nav.defiler(None, str(action.get("sens", "bas")))
                else:
                    cible = str(action.get("cible", "")).strip()
                    # Entrée sans cible (playbook enregistré par une version antérieure) :
                    # injouable, mais ce n'est pas une raison d'abandonner tout le playbook.
                    if not cible:
                        self.logger.info(f"  Playbook {numero} : action « {type_action} » sans cible "
                                         f"-> ignorée (le LLM la refera si nécessaire).")
                        continue
                    idx = trouver(cible)
                    if idx is None:
                        self.logger.info(f"  Playbook : cible introuvable « {cible[:40]} » -> mode LLM.")
                        return None
                    self._libelles = {int(k): self._masquer(v) for k, v in self.nav.libelles().items()
                                      if str(k).isdigit()}
                    # Soumission finale : même verrou que le mode normal (CV déposé + mode test).
                    soumission = type_action == "cliquer" and action.get("soumission_finale")
                    if soumission and not cv_depose:
                        self.logger.info("  Playbook : envoi bloqué (CV pas encore déposé) -> mode LLM.")
                        return None
                    if soumission and not self.soumission_auto:
                        return resultat(em.PRET_TEST,
                                        "Formulaire rempli (playbook), envoi NON effectué (mode test)")
                    compte_rendu = self._executer({"type": type_action, "index": idx,
                                                   "valeur": action.get("valeur", "")}, {})
                    if soumission:
                        soumission_cliquee = True
                historique.append(f"playbook {numero} : {type_action} -> {str(compte_rendu)[:120]}")
                if str(compte_rendu).startswith(("ÉCHEC", "REFUSÉ")):
                    self.logger.info(f"  Playbook : action échouée ({str(compte_rendu)[:60]}) -> mode LLM.")
                    return None
                time.sleep(self.nav.delai_action)
            except ChatEpuise:
                # Compte du chat saturé : repasser en mode LLM ne servirait à rien, il
                # buterait sur la même limite. On laisse remonter pour changer de navigateur.
                raise
            except Exception as e:  # noqa: BLE001 — un playbook qui plante ne doit pas bloquer
                self.logger.info(f"  Playbook : erreur ({e}) -> mode LLM.")
                return None
            # Après une soumission : confirmation présente ?
            if soumission_cliquee:
                time.sleep(1.5)
                texte = (self.nav.texte_court(4000) or "").lower()
                if any(c in texte for c in CONFIRMATIONS):
                    return resultat(em.POSTULE, "Playbook rejoué : confirmation de candidature détectée")
                self.logger.info("  Playbook : soumission sans confirmation détectée -> mode LLM.")
                return None
        # Toute la séquence a été rejouée sans envoi : on vérifie si la candidature est confirmée
        time.sleep(1.0)
        texte = (self.nav.texte_court(4000) or "").lower()
        if any(c in texte for c in CONFIRMATIONS):
            return resultat(em.POSTULE, "Playbook rejoué : confirmation de candidature détectée")
        self.logger.info("  Playbook terminé sans confirmation -> mode LLM pour finir.")
        return None

    # ------------------------------------------------------------------


if __name__ == "__main__":
    print("Module d'agent de candidature (utilisé par orchestrator.py).")

