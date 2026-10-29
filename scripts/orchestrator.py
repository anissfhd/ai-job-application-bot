"""
ORCHESTRATEUR — script principal du système de candidatures automatiques.

Pour chaque offre « À traiter » du fichier Excel :
  1. ouvre le lien avec Tappi et extrait la description de l'entreprise et de l'offre ;
  2. génère un CV et une lettre de motivation LaTeX adaptés (LLM + garde-fous) ;
  3. compile les deux en PDF dans output/Candidatures/<Entreprise>_ligneN/ ;
  4. remplit le formulaire de candidature et téléverse les PDF (ou envoie un email) ;
  5. fait le BILAN de la tentative : en cas d'échec il en trouve la cause et se rattrape
     seul (nouvelle tentative avec un conseil, ou recherche de l'offre sur le site
     carrière de l'entreprise) ; il retient une leçon par site (dossier memoire/) ;
  6. écrit le statut, la date, les chemins des PDF et un commentaire dans l'Excel.
Puis il passe à l'offre suivante et tourne en boucle (24h/24) en surveillant l'Excel.
Chaque jour à l'heure choisie, il produit un rapport (rapports/ + email si configuré).

Lancement :
    python scripts/orchestrator.py                 # boucle infinie (mode normal)
    python scripts/orchestrator.py --once          # traite les offres en attente puis s'arrête
    python scripts/orchestrator.py --test          # remplit les formulaires SANS cliquer sur « Envoyer »
    python scripts/orchestrator.py --documents-seulement --limite 1
                                                   # génère seulement CV + LM (aucune candidature)
    python scripts/orchestrator.py --ligne 5       # (re)traite uniquement la ligne 5 de l'Excel
    python scripts/orchestrator.py --verifier      # vérifie l'installation et la configuration
    python scripts/orchestrator.py --rapport       # produit (et envoie) le rapport du jour maintenant
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import excel_manager as em  # noqa: E402
from candidature import (AgentCandidature, InscriptionARejouer, MARQUE_LIMITE_EASY_APPLY,  # noqa: E402
                         ResultatCandidature)
from gemini_chat import ChatEpuise  # noqa: E402  (chat web saturé -> navigateur suivant)
from documents import (ErreurGeneration, GenerateurDocuments, compter_pages,  # noqa: E402
                       normaliser_pour_comparaison)
from envoi_email import envoyer_candidature  # noqa: E402
from envoi_email import smtp_configure as envoi_smtp_configure  # noqa: E402
from latex_compiler import moteur_disponible  # noqa: E402
from llm_client import ClientLLM, ErreurLLM  # noqa: E402
from memoire import Memoire, domaine  # noqa: E402
from rapport import produire_rapport  # noqa: E402
from objectif import Objectif  # noqa: E402
from utils import (charger_config, chemin_projet, configurer_logs, extraire_json,  # noqa: E402
                   latex_vers_texte, pause_aleatoire, slugifier)


class ErreurOffre(Exception):
    """La page de l'offre est inaccessible ou inexploitable."""


class PageAntiRobot(Exception):
    """Le site affiche une vérification anti-robot à la place de l'offre (Cloudflare…).
    L'offre n'est pas expirée : elle n'a simplement pas pu être lue."""


# Raison d'invalidité donnée par le modèle qui désigne en fait une page anti-robot
MOTIFS_ANTI_ROBOT = ("cloudflare", "vérification de sécurité", "verification de securite",
                     "anti-robot", "captcha", "verify you are human", "vérification supplémentaire")


class Orchestrateur:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.config, self.profil = charger_config()
        self.cfg_cand = self.config.setdefault("candidature", {})
        # Rotation des navigateurs quand DeepSeek est saturé (voir _chat_sature) :
        self._echecs_chat_consecutifs = 0     # remis à zéro dès qu'une offre passe sans saturation
        self._rejeux_chat: dict[int, int] = {}   # ligne Excel -> nombre de saturations subies
        self._pause_saturation = False        # demandée quand TOUS les navigateurs ont échoué
        self._chat_sature_ce_tour = False
        # Limite quotidienne Easy Apply de LinkedIn : nombre d'offres SANS Easy Apply à traiter
        # avant de revenir à l'ordre normal de l'Excel (0 = ordre normal).
        self._sans_easy_apply = 0
        if args.test:
            self.cfg_cand["soumission_automatique"] = False
        chemins = self.config["chemins"]
        self.logger = configurer_logs(chemin_projet(chemins["logs"]))
        self.dossier_sortie = chemin_projet(chemins["sortie"])
        self.dossier_sortie.mkdir(parents=True, exist_ok=True)
        self.excel = em.ExcelManager(chemin_projet(chemins["excel"]), self.logger)
        self.objectif = Objectif(self.profil)  # ce que le candidat cherche (config/objectif.yaml)
        self.prompt_extraction = self.objectif.remplir(
            (chemin_projet(chemins["prompts"]) / "extraction_prompt.txt").read_text(encoding="utf-8"))
        self.prompt_recherche = self.objectif.remplir(
            (chemin_projet(chemins["prompts"]) / "recherche_prompt.txt").read_text(encoding="utf-8"))
        self.memoire = Memoire(chemin_projet(chemins.get("memoire", "memoire")))
        self.cfg_rapport = self.config.setdefault("rapport", {})
        self.llm: ClientLLM | None = None
        self.generateur: GenerateurDocuments | None = None
        self.nav = None
        self.agent: AgentCandidature | None = None

    # ------------------------------------------------------------------
    # Vérifications au démarrage
    # ------------------------------------------------------------------

    def verifier_prerequis(self) -> bool:
        """Affiche les problèmes de configuration. Renvoie False si un problème est bloquant."""
        chemins = self.config["chemins"]
        bloquants, alertes = [], []

        for cle in ("cv_original", "lm_original"):
            if not chemin_projet(chemins[cle]).exists():
                bloquants.append(f"Fichier manquant : {chemins[cle]} (dépose ton fichier LaTeX original).")

        moteur = self.config.get("latex", {}).get("moteur", "pdflatex")
        if not moteur_disponible(moteur):
            bloquants.append(f"« {moteur} » introuvable : installe MiKTeX / TeX Live (voir README).")

        ident = self.profil.get("identite", {}) or {}
        for champ in ("prenom", "nom", "email", "telephone"):
            if not str(ident.get(champ) or "").strip():
                bloquants.append(f"config/profile.yaml : identite.{champ} est vide (lance FORMULAIRE.bat).")
        if not (self.objectif.type and self.objectif.nom_fr):
            bloquants.append("config/objectif.yaml : objectif de recherche vide (lance FORMULAIRE.bat, section 4).")
        if str((self.config.get("deepseek_chat") or {}).get("mode_cv", "")).lower() == "gabarit":
            for f in ("gabarit.tex", "reference_en.json", "fixe_fr.json", "fixe_en.json", "lettre.tex", "lettre_fixe.json"):
                if not (chemin_projet("config/cv_gabarit") / f).exists():
                    bloquants.append(f"config/cv_gabarit/{f} manquant (lance FORMULAIRE.bat, section 9).")

        try:
            self.llm = ClientLLM(self.config, self.logger)
        except (ErreurLLM, ImportError) as e:
            bloquants.append(str(e))

        # Les mentions protégées doivent exister telles quelles dans l'original,
        # sinon le garde-fou ne peut pas les vérifier.
        cv = chemin_projet(chemins["cv_original"])
        if cv.exists():
            contenu = normaliser_pour_comparaison(cv.read_text(encoding="utf-8"))
            for chaine in self.config.get("garde_fous", {}).get("chaines_obligatoires_cv") or []:
                if normaliser_pour_comparaison(chaine) not in contenu:
                    alertes.append(f"« {chaine} » n'apparaît pas tel quel dans ton CV original : "
                                   f"le garde-fou ne pourra pas le vérifier (ajuste garde_fous dans config.yaml).")
            for champ in ("prenom", "nom", "email"):
                valeur = str(ident.get(champ) or "")
                if valeur and normaliser_pour_comparaison(valeur) not in contenu:
                    alertes.append(f"identite.{champ} (« {valeur} ») n'apparaît pas tel quel dans ton CV original.")

        if self.config.get("email", {}).get("actif"):
            cfg_mail = self.config["email"]
            if not (cfg_mail.get("utilisateur") and cfg_mail.get("mot_de_passe")):
                bloquants.append("email.actif est true mais email.utilisateur / email.mot_de_passe sont vides.")

        if not os.environ.get("MOT_DE_PASSE_SITES", "").strip():
            alertes.append("MOT_DE_PASSE_SITES absent du fichier .env : le robot ne pourra ni se "
                           "connecter ni créer de compte candidat sur les sites qui le demandent.")
        for cle in ("MOT_DE_PASSE_SITES_2", "MOT_DE_PASSE_SITES_3"):
            if not os.environ.get(cle, "").strip():
                alertes.append(f"{cle} absent du fichier .env : si un site refuse le mot de passe "
                               f"principal (trop court…), le robot n'aura pas de version de secours.")

        for a in alertes:
            self.logger.warning(a)
        for b in bloquants:
            self.logger.error(b)
        if not bloquants:
            self.logger.info("Configuration OK.")
        return not bloquants

    def initialiser(self) -> None:
        """Crée le générateur de documents, le navigateur et l'agent de formulaire."""
        from navigateur import Navigateur  # import ici : tappi n'est pas requis pour --verifier
        self.generateur = GenerateurDocuments(self.config, self.profil, self.llm, self.logger)
        self.nav = Navigateur(self.config, self.logger)
        # Avec plusieurs comptes Google connectés dans un même profil, un SSO doit viser
        # CELUI des candidatures — sinon l'offre partirait sous l'identité du compte de chat.
        self.nav.email_candidature = str(
            (self.profil.get("identite") or {}).get("email", "") or "").strip()
        self.nav.demarrer(premier_plan=True)   # fenêtre agrandie et devant dès le départ
        self.agent = AgentCandidature(self.config, self.profil, self.llm, self.nav, self.logger)
        if getattr(self.args, "tout_postuler", False):
            self.agent.tout_postuler = True
            self.logger.info("Mode « POSTULER À TOUT » : le type de contrat n'arrête plus rien, et le "
                             "robot ne demande plus ton avis. Il se connecte, crée le compte et "
                             "confirme l'email lui-même ; seuls un CAPTCHA à défi, un code SMS ou "
                             "une inscription impossible l'arrêteront (Action manuelle).")
        # Si le remplissage passe par le chat web, la clé API est probablement morte : on
        # branche ce même chat en SECOURS pour les autres appels (lecture de l'offre,
        # réduction du CV à 1 page, diagnostic) afin que rien ne reste bloqué.
        if self.agent.llm_formulaire is not self.llm:
            nom = getattr(self.agent.llm_formulaire, "NOM", "chat web")
            self.llm.brancher_secours(self.agent.llm_formulaire, f"chat web {nom}")
            # Les réécritures LaTeX, elles, ne doivent PAS partir au chat du formulaire : il
            # ne connaît ni le document ni l'offre. Quand DeepSeek rédige les documents, c'est
            # SA conversation qui les corrige (branchée par documents.py au moment de la
            # génération) — deux conversations DeepSeek en tout, jamais trois.
            if not self.generateur.deepseek_actif:
                # Documents produits par l'API : pas de conversation « documents » à réutiliser,
                # on garde alors un chat dédié comme filet.
                from deepseek_chat import ClientDeepSeekTexte
                self.generateur.brancher_secours_latex(
                    ClientDeepSeekTexte(self.config, self.nav, self.logger))
        # Les questions TECHNIQUES du formulaire sont renvoyées au chat qui a rédigé le CV
        # adapté à l'offre. On passe la fabrique, pas l'objet : la conversation n'existe
        # qu'à partir de la génération des documents.
        self.agent.moteur_technique = lambda: self.generateur.moteur_documents(self.nav)
        # Mot de passe de secours (2e / 3e version) avec lequel le compte candidat d'un site a
        # été créé : retenu PAR HÔTE (un compte Workday est propre à chaque entreprise).
        self.agent.rang_mot_de_passe = lambda hote: self.memoire.regles(f"https://{hote}").get(
            f"mot_de_passe@{hote}", 0)
        # Le bilan se fait DANS la conversation qui vient de remplir le formulaire : elle ne
        # doit donc pas être fermée à la fin de la candidature, mais juste après le bilan.
        if self.agent.llm_formulaire is not self.llm:
            self.agent.fermer_chat_en_fin = False
        n = self.excel.reinitialiser_en_cours()
        if n:
            self.logger.info(f"{n} ligne(s) « En cours » remise(s) à « À traiter » (arrêt précédent).")

    def fermer_onglets_assistants(self) -> None:
        """
        Fin de programme (y compris Ctrl+C) : referme les onglets des assistants — chat
        Gemini et chat DeepSeek — pour ne pas les laisser traîner d'une session à l'autre.
        L'onglet de la dernière offre est conservé : il montre où le robot s'est arrêté.
        """
        nav = getattr(self, "nav", None)
        if nav is None:
            return
        fermer = getattr(getattr(self, "agent", None), "llm_formulaire", None)
        if callable(getattr(fermer, "fermer", None)):
            fermer.fermer()
        # Fermer par index EN ITÉRANT est faux : chaque fermeture décale les index suivants.
        # On referme donc un onglet à la fois, en relisant la liste entre chaque.
        def assistants():
            return [t for t in nav.b.tabs()
                    if any(m in (t.url or "").lower() for m in ("deepseek", "gemini.google"))]

        try:
            for _ in range(8):
                restants = assistants()
                if not restants:
                    break
                try:
                    nav.b.close_tab(restants[0].index)
                except Exception:  # noqa: BLE001 — index périmé : on relit et on réessaie
                    pass
                time.sleep(0.4)
            if not assistants():
                self.logger.info("Onglets des assistants (DeepSeek / Gemini) refermés.")
            else:
                self.logger.warning("Certains onglets d'assistant n'ont pas pu être refermés.")
        except Exception as e:  # noqa: BLE001 — un nettoyage raté ne doit rien casser
            self.logger.debug(f"Nettoyage des onglets impossible : {e}")

    # ------------------------------------------------------------------
    # Étape 1 : extraction de l'offre
    # ------------------------------------------------------------------

    def extraire_offre(self, ligne: dict, dossier: Path) -> dict:
        """Renvoie un dictionnaire décrivant l'offre (extrait de la page ou déjà présent dans l'Excel)."""
        url = str(ligne["Lien_Offre"]).strip()
        offre = {
            "nom_entreprise": str(ligne["Nom_Entreprise"]).strip(),
            "description_entreprise": str(ligne["Description_Entreprise"]).strip(),
            "description_offre": str(ligne["Description_Offre"]).strip(),
            "email_candidature": str(ligne["Email"]).strip(),
            "titre_poste": "", "lieu": "", "langue_offre": "fr", "offre_valide": True,
            "type_contrat": self.objectif.contrats[0], "duree_compatible_6_mois": True,
            "mode_candidature": "formulaire" if url else "email",
        }
        # Descriptions déjà renseignées (à la main ou lors d'un essai précédent) -> pas de nouvelle
        # extraction ; on récupère titre / langue / mode de candidature d'un éventuel offre.json.
        if offre["description_offre"] or not url:
            fichier_json = dossier / "offre.json"
            if fichier_json.exists():
                try:
                    anciennes = json.loads(fichier_json.read_text(encoding="utf-8"))
                    for cle in ("titre_poste", "lieu", "langue_offre", "mode_candidature"):
                        if anciennes.get(cle):
                            offre[cle] = str(anciennes[cle])
                except ValueError:
                    pass
            return offre

        self.logger.info(f"Lecture de l'offre : {url}")
        self.nav.onglet_unique()
        self.nav.aller(url)
        if self.nav.url().startswith("chrome-error://"):
            raise ErreurOffre(f"Site inaccessible : {url}")
        # Descend vers la description et clique « voir plus / … plus » pour l'avoir en entier.
        # « voir plus » encore présent = description pas dépliée -> on réessaie (chargement paresseux).
        texte = ""
        for essai in range(1, 4):
            clics = self.nav.deplier_description()
            texte = self.nav.texte_complet()
            self.logger.info(f"Description : essai {essai}, {clics} bouton(s) « plus » cliqué(s), "
                             f"{len(texte)} caractères.")
            # Description chargée -> on s'arrête tout de suite (pas de tours inutiles).
            # On ne réessaie QUE si la page est courte (site lent / bridage : description non servie).
            if len(texte.strip()) > 2500:
                break
            self.nav.attendre_page(2.0)   # attente intelligente : dès que la page a du contenu
        # Vérification anti-robot à la place de l'offre : UN clic sur la case, et c'est tout.
        # Si la page ne s'en va pas, l'offre passe en « Action manuelle » (jamais « expirée »).
        if self.nav.page_anti_robot():
            # NopeCHA (si actif) résout seule la vérification ; le clic sur la case reste en secours.
            clic = ("NopeCHA " + self.nav.attendre_nopecha()) if self.nav.nopecha_actif else ""
            if clic == "NopeCHA validée":
                self.nav.attendre_page_prete(15)
            if self.nav.page_anti_robot():
                clic = (clic + " ; " if clic else "") + self.nav.cliquer_case_anti_robot()
                if clic.endswith("case cliquée"):
                    clic += " -> " + self.nav.attendre_case_validee(20)   # validée dès que possible
                    if clic.endswith("-> validée"):
                        self.nav.attendre_page_prete(15)   # la page de l'offre se recharge
            self.logger.info(f"Page de vérification anti-robot -> {clic}.")
            if self.nav.page_anti_robot():
                raise PageAntiRobot(f"Page de vérification anti-robot du site ({clic}) : "
                                    f"offre non lue, à ouvrir à la main.")
            self.nav.deplier_description()
            texte = self.nav.texte_complet()
            self.logger.info(f"Vérification passée : {len(texte)} caractères lus.")
        (dossier / "offre_page.txt").write_text(f"{url}\n\n{texte}", encoding="utf-8")
        if len(texte.strip()) < 150:
            raise ErreurOffre("Page vide ou non chargée (contenu < 150 caractères).")

        message = f"URL : {url}\nTitre de la page : {self.nav.titre()}\n\n<page>\n{texte}\n</page>"
        donnees = extraire_json(self.llm.generer(self.prompt_extraction, message))
        # Le chat de secours répond parfois dans le format du FORMULAIRE (analyse / actions /
        # statut) : sans ce contrôle, l'offre partait avec entreprise et descriptions vides,
        # et rien n'était enregistré dans l'Excel. On redemande une fois, puis on s'arrête.
        if not isinstance(donnees, dict) or "description_offre" not in donnees:
            self.logger.warning("Extraction de l'offre : réponse hors format -> nouvelle demande.")
            donnees = extraire_json(self.llm.generer(self.prompt_extraction, message))
            if not isinstance(donnees, dict) or "description_offre" not in donnees:
                raise ErreurOffre("Extraction de l'offre : réponse hors format (deux fois).")
        (dossier / "offre.json").write_text(json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8")

        # Anti-hallucination : un email doit apparaître littéralement dans la page
        email = str(donnees.get("email_candidature") or "").strip()
        if email and email.lower() not in texte.lower():
            email = ""
        telephone = str(donnees.get("telephone_contact") or "").strip()

        for cle in ("titre_poste", "lieu", "langue_offre", "type_contrat", "mode_candidature",
                    "description_entreprise", "description_offre", "raison_invalide", "duree_indiquee"):
            if donnees.get(cle) not in (None, ""):
                offre[cle] = str(donnees[cle]).strip()
        offre["offre_valide"] = donnees.get("offre_valide", True) is not False
        offre["duree_compatible_6_mois"] = donnees.get("duree_compatible_6_mois", True) is not False
        if not offre["nom_entreprise"]:
            offre["nom_entreprise"] = str(donnees.get("nom_entreprise") or "").strip()
        if not offre["email_candidature"]:
            offre["email_candidature"] = email

        # Sauvegarde immédiate dans l'Excel (lisible même si la suite échoue)
        maj = {"Description_Entreprise": offre["description_entreprise"],
               "Description_Offre": offre["description_offre"]}
        if not str(ligne["Nom_Entreprise"]).strip() and offre["nom_entreprise"]:
            maj["Nom_Entreprise"] = offre["nom_entreprise"]
        if not str(ligne["Email"]).strip() and email:
            maj["Email"] = email
        if not str(ligne["Téléphone"]).strip() and telephone:
            maj["Téléphone"] = telephone
        self.excel.mettre_a_jour(ligne["_ligne"], **maj)
        return offre

    # ------------------------------------------------------------------
    # Étapes 2 à 5 : traitement complet d'une ligne
    # ------------------------------------------------------------------

    OFFRES_SANS_EASY_APPLY = 10       # offres sans Easy Apply traitées après la limite

    # ------------------------------------------------------------------
    # Scraping automatique quand l'Excel est vide
    # ------------------------------------------------------------------

    DOSSIER_SCRAPING = Path(__file__).resolve().parent.parent / "input" / "Scraping"
    MARQUE_PASSAGE = "===== Passage de scraping ====="

    def _dernier_scraping(self) -> datetime | None:
        """Heure du dernier passage de scraping, manuel (LANCER_SCRAPING.bat) ou automatique."""
        journal = self.DOSSIER_SCRAPING / "logs" / "scraping.log"
        try:
            lignes = journal.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        for l in reversed(lignes):
            if self.MARQUE_PASSAGE in l:
                try:
                    return datetime.strptime(l[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return None
        return None

    def _scraping_auto(self) -> bool:
        """
        L'Excel n'a plus d'offre à traiter : lance UN passage de scraping — la fonction
        `passage()` de input/Scraping/scraping.py, c'est-à-dire exactement ce que fait
        LANCER_SCRAPING.bat (Apify, filtre < 100 candidats, Easy Apply d'abord, nettoyage,
        doublons ignorés, insertion sous la ligne en cours). Au plus un passage toutes les
        `intervalle_min_heures`, manuel ou automatique : chaque passage coûte sur Apify.
        Renvoie True si un passage a été lancé (l'Excel est alors relu tout de suite).
        """
        cfg = self.config.get("scraping_auto") or {}
        if not cfg.get("actif", True):
            return False
        ecart = timedelta(hours=float(cfg.get("intervalle_min_heures", 2)))
        dernier = self._dernier_scraping()
        if dernier and datetime.now() - dernier < ecart:
            self.logger.info(f"Excel vide : dernier scraping à {dernier:%H:%M}, le prochain automatique "
                             f"est possible à partir de {dernier + ecart:%H:%M} (au plus un toutes les "
                             f"{ecart.total_seconds() / 3600:g} h).")
            return False
        self.logger.info("Excel vide : scraping automatique des nouvelles offres "
                         "(même logique que LANCER_SCRAPING.bat)…")
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("scraping", self.DOSSIER_SCRAPING / "scraping.py")
            scraping = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(scraping)
            journal = scraping.journal()
            journal.info(self.MARQUE_PASSAGE + " (automatique : Excel vide)")
            scraping.passage(journal)
        except Exception as e:  # noqa: BLE001 — un scraping raté ne doit jamais arrêter le robot
            self.logger.error(f"Scraping automatique impossible : {type(e).__name__} : {e}")
            return False                 # pas de relance immédiate : attente normale de 10 min
        return True

    @staticmethod
    def _est_easy_apply(ligne: dict) -> bool:
        """Offre Easy Apply (marquée ainsi par le scraping), bloquée par la limite quotidienne."""
        return "easy apply" in str(ligne.get("Commentaire", "")).lower()

    def _gmail_actif(self) -> bool:
        return bool((self.config.get("candidature_gmail") or {}).get("actif", True))

    def _candidature_gmail(self, destinataire: str, offre: dict, fournir_documents,
                           dossier: Path, lien: str = "") -> ResultatCandidature:
        """
        Candidature par email via Gmail (voir candidature_gmail.py) : DeepSeek rédige l'email,
        puis la boucle habituelle (captures + DeepSeek) joint le CV et la lettre et envoie.
        """
        import candidature_gmail as cg
        try:
            _, _, lettre = fournir_documents()      # CV + lettre (déjà prêts en général)
            # CV réutilisé ou robot relancé : la conversation du CV n'existe pas encore -> on la
            # crée (sinon l'email partait toujours sur le modèle fixe).
            self.generateur.moteur_documents(self.nav)
            chat = getattr(self.generateur, "_chat_documents", None)
            recruteur = cg.recruteur_offre(dossier, lien)
            if recruteur:
                self.logger.info(f"  Recruteur affiché sur l'offre : {recruteur}")
            objet, corps = cg.rediger(chat, offre, self.profil, destinataire, self.logger,
                                      lettre=lettre, recruteur=recruteur, dossier=dossier)
            (dossier / "email_candidature.txt").write_text(
                f"À : {destinataire}\nObjet : {objet}\n\n{corps}\n", encoding="utf-8")
            self.logger.info(f"  Candidature par email via Gmail à {destinataire}…")
            expediteur = str((self.profil.get("identite") or {}).get("email") or "")
            res = self.agent.postuler(cg.url_nouveau_message(destinataire, objet, corps, expediteur), offre,
                                      fournir_documents, dossier / "captures",
                                      conseil=cg.consigne(destinataire, objet))
        except ChatEpuise:
            raise
        except Exception as e:  # noqa: BLE001 — l'envoi par email ne doit jamais planter le robot
            self.logger.warning(f"  Candidature par email impossible : {e}")
            return ResultatCandidature(em.ACTION_MANUELLE, f"Envoi par email impossible : {e}")
        finally:
            self.agent.fermer_chat()
        if res.statut == em.POSTULE:
            res.commentaire = f"Candidature envoyée par email (Gmail) à {destinataire} — {res.commentaire}"
        return res

    def _envoi_email(self, destinataire: str, offre: dict, cv: Path, lm: Path) -> ResultatCandidature:
        if not self.config.get("email", {}).get("actif"):
            return ResultatCandidature(em.ACTION_MANUELLE,
                                       f"Candidature par email à {destinataire} (envoi email désactivé dans config.yaml)")
        if self.args.test:
            return ResultatCandidature(em.PRET_TEST, f"Mode test : email NON envoyé à {destinataire}")
        envoyer_candidature(self.config, self.profil, destinataire, offre, cv, lm)
        return ResultatCandidature(em.POSTULE, f"Candidature envoyée par email à {destinataire}")

    # ------------------------------------------------------------------
    # Candidature avec bilan, mémoire par site et rattrapage automatique
    # ------------------------------------------------------------------

    JS_RESULTATS_RECHERCHE = r"""
    (() => JSON.stringify([...document.querySelectorAll('a.result__a')].slice(0, 15)
        .map(a => ({titre: (a.innerText || '').trim(), url: a.href}))))()
    """
    DOMAINES_AGREGATEURS = ("linkedin.com", "indeed.com", "indeed.fr", "glassdoor.com", "glassdoor.fr",
                            "duckduckgo.com", "google.com", "jooble.org", "optioncarriere.com")

    def chercher_site_carriere(self, offre: dict, deja_essayees: set[str]) -> str:
        """Cherche la même offre sur le site carrière de l'entreprise. Renvoie une URL ou ''."""
        requete = " ".join(filter(None, [offre.get("nom_entreprise"), offre.get("titre_poste"),
                                         offre.get("lieu"), "stage candidature"]))
        self.logger.info(f"Recherche de l'offre sur le site de l'entreprise : « {requete} »")
        try:
            self.nav.onglet_unique()
            self.nav.aller("https://html.duckduckgo.com/html/?q=" + quote_plus(requete))
            brut = self.nav.b.eval(self.JS_RESULTATS_RECHERCHE)
            resultats = json.loads(brut) if isinstance(brut, str) else []
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"Recherche impossible : {e}")
            return ""

        candidats = []
        for r in resultats:
            url = r.get("url", "")
            if "duckduckgo.com/l/" in url:   # lien de redirection DuckDuckGo -> vraie URL
                url = parse_qs(urlparse(url).query).get("uddg", [""])[0]
            if url.startswith("http") and domaine(url) not in self.DOMAINES_AGREGATEURS and url not in deja_essayees:
                candidats.append({"titre": r.get("titre", ""), "url": url})
        if not candidats:
            self.logger.info("Aucun résultat exploitable.")
            return ""

        liste = "\n".join(f"{i + 1}. {c['titre']} — {c['url']}" for i, c in enumerate(candidats))
        message = (f"<offre>\nEntreprise : {offre.get('nom_entreprise', '')}\nPoste : {offre.get('titre_poste', '')}\n"
                   f"Lieu : {offre.get('lieu', '')}\nRésumé : {str(offre.get('description_offre', ''))[:800]}\n</offre>\n\n"
                   f"<resultats>\n{liste}\n</resultats>")
        try:
            choix = extraire_json(self.llm.generer(self.prompt_recherche, message, tache="diagnostic"))
        except (ValueError, ErreurLLM) as e:
            self.logger.warning(f"Choix du résultat impossible : {e}")
            return ""
        url = str(choix.get("url") or "")
        if url in {c["url"] for c in candidats} and choix.get("confiance") in ("haute", "moyenne"):
            self.logger.info(f"Offre trouvée sur : {url} ({choix.get('raison', '')})")
            return url
        self.logger.info(f"Pas de correspondance fiable ({choix.get('raison', '')}).")
        return ""

    def _chat_sature(self, n: int, e: Exception) -> str:
        """
        Réaction à un DeepSeek saturé. Ne concerne QUE ce cas — les autres erreurs gardent
        leur traitement habituel.

        1. On change TOUJOURS de navigateur, en rotation circulaire (1 → 2 → 3 → 4 → 1…) :
           celui-ci vient de montrer que son compte est saturé.
        2. L'offre est remise « À traiter » : elle est donc REJOUÉE aussitôt, depuis le début,
           sur le nouveau navigateur (c'est la première ligne en attente).
        3. Si elle échoue une DEUXIÈME fois, elle passe en « Erreur » et on continue avec la
           suivante — qui démarre, elle, sur un navigateur neuf.

        GARDE-FOU. Si TOUS les navigateurs échouent d'affilée — un tour complet sans une seule
        réponse de DeepSeek — ce n'est plus un compte saturé : c'est DeepSeek lui-même, ou une
        limite posée sur l'adresse IP. Continuer brûlerait la liste d'offres une à une. L'offre
        est alors remise « À traiter » et le robot se met en pause avant de reprendre.
        """
        nb_navigateurs = len(getattr(self.nav, "navigateurs", []) or [None])
        self._chat_sature_ce_tour = True
        self._echecs_chat_consecutifs += 1
        rejeux = self._rejeux_chat.get(n, 0) + 1
        self._rejeux_chat[n] = rejeux

        # L'offre sera REJOUÉE sur le nouveau navigateur (1re saturation, pas de pause générale) :
        # l'ancien peut être fermé, sauf s'il garde des onglets « Action manuelle ». Sinon on
        # passe à l'offre suivante et l'ancien est seulement réduit.
        rejouee = rejeux < 2 and self._echecs_chat_consecutifs < nb_navigateurs
        bascule = self.nav.basculer_navigateur(fermer_ancien=rejouee)
        if bascule:
            self._reinitialiser_chats()

        if not bascule or self._echecs_chat_consecutifs >= nb_navigateurs:
            self._pause_saturation = True
            self._rejeux_chat.pop(n, None)     # l'offre n'y est pour rien : compteur remis à zéro
            motif = (f"DeepSeek saturé sur les {nb_navigateurs} navigateur(s) d'affilée "
                     f"(limite par IP ou serveur surchargé) — offre remise en attente, pause.")
            self.logger.error(f"  {e}")
            self.logger.error(f"  {motif}")
            self.excel.definir_statut(n, em.A_TRAITER, motif)
            return em.A_TRAITER

        if rejeux >= 2:
            self._rejeux_chat.pop(n, None)
            motif = f"DeepSeek saturé sur deux navigateurs de suite : {e}"
            self.logger.error(f"Ligne {n} -> {em.ERREUR} : {motif}")
            self.excel.definir_statut(n, em.ERREUR, motif)
            return em.ERREUR

        motif = (f"DeepSeek saturé ({e}) — offre rejouée depuis le début sur le navigateur "
                 f"{self.nav.index_navigateur + 1}.")
        self.logger.warning(f"Ligne {n} -> {em.A_TRAITER} : {motif}")
        self.excel.definir_statut(n, em.A_TRAITER, motif)
        return em.A_TRAITER

    def _reinitialiser_chats(self) -> None:
        """
        Oublie conversations et onglets des chats web après une bascule de navigateur.

        Ces repères — identifiant d'onglet CDP, lien de la conversation, prompt système déjà
        transmis — désignaient des objets de l'ANCIEN Chrome. Ils n'existent pas dans le
        nouveau : sans cette remise à zéro, le robot viserait des onglets fantômes.

        On ne tente pas de refermer quoi que ce soit dans l'ancien navigateur : il n'est plus
        piloté, et le laisser ouvert tel quel est sans conséquence.
        """
        chat = getattr(self.agent, "llm_formulaire", None)
        if chat is not None:
            for attribut in ("_id_onglet", "_systeme_envoye", "_url_conversation",
                             "_dernier_message", "_dernier_envoi"):
                if hasattr(chat, attribut):
                    setattr(chat, attribut, "")
        # La conversation qui rédige le CV et la lettre vivait elle aussi dans l'autre Chrome.
        if getattr(self.generateur, "_chat_documents", None) is not None:
            self.generateur._chat_documents = None
            self.generateur._secours_latex = None
        self.logger.info("  Chats web réinitialisés pour le nouveau navigateur.")

    def postuler_avec_rattrapage(self, url: str, offre: dict, fournir_documents,
                                 dossier_captures: Path) -> ResultatCandidature:
        """
        Tente la candidature ; après chaque tentative, fait le bilan (cause + leçon pour le site).
        En cas d'échec rattrapable : nouvelle tentative guidée par le diagnostic, ou recherche
        de l'offre sur le site carrière de l'entreprise.
        """
        rattrapages_max = int(self.cfg_cand.get("rattrapages_max", 2))
        rattrapage_auto = bool(self.cfg_cand.get("rattrapage_automatique", True))
        conseil = ""
        deja_essayees = {url}
        parcours: list[str] = []

        # Un chat web saturé (ChatEpuise) n'est PAS traité ici : il remonte jusqu'à traiter(),
        # qui change de navigateur et rejoue l'offre (voir _chat_sature). Ainsi la même règle
        # vaut pour TOUS les appels à DeepSeek — lecture de l'offre comprise — et pas
        # seulement pour le formulaire.
        essai = 0
        # Inscription dont le formulaire a disparu pendant la confirmation de l'email :
        # on rejoue UNE fois, et une seule. Si la seconde tentative se perd aussi, insister
        # ne ferait que refaire le même chemin.
        rejeux_inscription = 0
        while essai <= rattrapages_max:
            lecons = self.memoire.lecons(url)
            regles = self.memoire.regles(url)
            if lecons:
                self.logger.info(f"  {len(lecons)} leçon(s) connue(s) pour {domaine(url)}")
            # PLAYBOOK : si ce site a déjà été réussi, on REJOUE la séquence mémorisée (sans LLM,
            # bien plus rapide). En cas d'échec, le mode normal (LLM) prend le relais.
            res = None
            replay_rate = False
            playbook = self.memoire.playbook(url) if self.cfg_cand.get("playbooks", True) else []
            if playbook and essai == 0:
                self.logger.info(f"  Playbook connu pour {domaine(url)} ({len(playbook)} actions) -> replay rapide.")
                try:
                    res = self.agent.replay_playbook(url, offre, fournir_documents,
                                                     dossier_captures, playbook)
                except Exception as e:  # noqa: BLE001 — un replay raté ne doit jamais bloquer
                    self.logger.info(f"  Replay du playbook impossible ({e}) -> mode normal.")
                    res = None
                if res is None:
                    replay_rate = True
                    self.memoire.playbook_echec(url)
                    self.logger.info("  Playbook inefficace -> on continue avec le LLM, "
                                     "depuis la page où le replay s'est arrêté.")
            if res is None:
                try:
                    res = self.agent.postuler(url, offre, fournir_documents, dossier_captures,
                                              lecons=lecons, conseil=conseil, regles=regles,
                                              reprendre_page=replay_rate)
                except InscriptionARejouer as e:
                    # Cas unique : le formulaire d'inscription a disparu pendant la
                    # confirmation de l'email. On rejoue la candidature depuis le début, sans
                    # consommer de rattrapage — et une seule fois.
                    rejeux_inscription += 1
                    if rejeux_inscription > 1:
                        self.logger.error(f"  {e} — déjà rejouée une fois, on s'arrête là.")
                        return ResultatCandidature(
                            em.ACTION_MANUELLE,
                            f"Inscription à terminer à la main : {e}")
                    self.logger.warning(f"  {e} -> candidature rejouée DEPUIS LE DÉBUT "
                                        f"(1 seule fois).")
                    self.agent.fermer_chat()
                    continue

            if MARQUE_LIMITE_EASY_APPLY in res.commentaire:
                self.agent.fermer_chat()
                return res                     # limite Easy Apply : rien à diagnostiquer
            # Mot de passe de secours utilisé sur un site : retenu pour s'y reconnecter plus tard.
            for hote, rang in (getattr(self.agent, "mdp_par_site", None) or {}).items():
                if self.memoire.enregistrer_regle(f"https://{hote}", f"mot_de_passe@{hote}", rang):
                    self.logger.info(f"  {hote} : compte candidat avec le mot de passe n° {rang} (retenu).")
            bilan = self.agent.bilan(offre, url, res, lecons)
            # Bilan fait : la conversation du formulaire a fini son travail. La candidature
            # suivante repartira d'une conversation vierge.
            self.agent.fermer_chat()
            site = res.url_finale or url
            if self.memoire.enregistrer(site, str(bilan.get("lecon", "")), res.statut):
                self.logger.info(f"  Nouvelle leçon pour {domaine(site)} : {bilan.get('lecon')}")
            # Règle exécutable apprise (le bilan a déduit une action mécanique pour ce site)
            regle = bilan.get("regle") or {}
            if isinstance(regle, dict) and regle.get("cle") is not None:
                if self.memoire.enregistrer_regle(site, str(regle["cle"]), regle.get("valeur", True)):
                    self.logger.info(f"  Nouvelle règle pour {domaine(site)} : {regle['cle']} = {regle.get('valeur')}")
            # Succès : on mémorise la séquence d'actions pour les prochaines fois (playbook)
            if (res.statut == em.POSTULE and self.cfg_cand.get("playbooks", True)
                    and getattr(self.agent, "actions_menees", None)):
                self.memoire.enregistrer_playbook(site, self.agent.actions_menees)
            explication = str(bilan.get("explication", "")).strip()
            strategie = str(bilan.get("strategie", "aucune")).lower()

            termine = (res.statut in (em.POSTULE, em.PRET_TEST, em.OFFRE_EXPIREE)
                       or res.soumission_cliquee)   # jamais de 2e envoi : risque de doublon
            echeance = getattr(self.agent, "echeance", None)
            peut_rattraper = (rattrapage_auto and essai < rattrapages_max
                              and strategie in ("reessayer", "site_carriere")
                              and not (echeance is not None and time.monotonic() > echeance))
            if termine or not peut_rattraper:
                if explication and res.statut not in (em.POSTULE, em.PRET_TEST):
                    res.commentaire += f" | Diagnostic : {explication}"
                if parcours:
                    res.commentaire = " → ".join(parcours) + " → " + res.commentaire
                return res

            conseil = str(bilan.get("conseil", "")).strip()
            parcours.append(f"Essai {essai + 1} : {res.statut} ({explication or res.commentaire})"[:250])
            if strategie == "site_carriere":
                nouvelle = self.chercher_site_carriere(offre, deja_essayees)
                if not nouvelle:
                    res.commentaire += f" | Diagnostic : {explication} | Offre introuvable sur le site de l'entreprise"
                    if parcours[:-1]:
                        res.commentaire = " → ".join(parcours[:-1]) + " → " + res.commentaire
                    return res
                url = nouvelle
                deja_essayees.add(url)
                parcours.append(f"bascule sur le site de l'entreprise : {url}")
            self.logger.info(f"  Rattrapage automatique ({strategie}) : {conseil or explication}")
            essai += 1   # un vrai rattrapage, lui, consomme un essai

        return res  # (inatteignable : la boucle renvoie toujours)

    def _fabrique_documents(self, offre: dict, dossier: Path, ligne_excel: int, notes: list):
        """
        Renvoie une fonction `fournir()` -> (cv_pdf, lm_pdf, texte_lettre) qui génère le CV et la
        lettre À LA PREMIÈRE demande (puis renvoie le cache). Met à jour l'Excel avec les chemins.
        """
        cache: dict = {}

        def _reutiliser() -> bool:
            """Réutilise le CV + la lettre déjà générés pour cette offre (s'ils existent encore)."""
            d = next((x for x in self.excel.lire_toutes() if x["_ligne"] == ligne_excel), {})
            cv_prec = Path(str(d.get("CV_PDF_Path", "")).strip() or "x")
            lm_prec = Path(str(d.get("LM_PDF_Path", "")).strip() or "x")
            if cv_prec.suffix == ".pdf" and cv_prec.exists() and lm_prec.exists():
                # Un CV déjà généré peut dater d'avant le contrôle de pagination : on le
                # revérifie avant de le réutiliser, sinon un CV trop long repart en boucle.
                pages_max = self.generateur.pages_max_cv
                pages = compter_pages(cv_prec)
                if pages_max and pages > pages_max:
                    self.logger.warning(f"  CV déjà généré mais {pages} page(s) > {pages_max} "
                                        f"-> ignoré, régénération complète.")
                    return False
                lettre = ""
                lm_tex = lm_prec.with_suffix(".tex")
                if lm_tex.exists():
                    lettre = latex_vers_texte(lm_tex.read_text(encoding="utf-8"))
                self.logger.info(f"  CV et lettre DÉJÀ générés pour cette offre -> réutilisés "
                                 f"({cv_prec.name}), pas de nouvelle génération.")
                cache.update(cv=cv_prec, lm=lm_prec, lettre=lettre)
                return True
            return False

        def fournir():
            if not cache and _reutiliser():
                return cache["cv"], cache["lm"], cache["lettre"]
            if not cache:
                self.logger.info("  Génération du CV et de la lettre pour cette offre…")
                if self.generateur.deepseek_actif:
                    # DeepSeek s'ouvre/ferme dans un nouvel onglet ; l'offre reste ouverte
                    cv_pdf, lm_pdf = self.generateur.generer_via_deepseek(offre, dossier, self.nav)
                    notes.extend(self.generateur.replis)   # ex. « CV ORIGINAL utilisé »
                else:
                    try:
                        cv_pdf = self.generateur.generer_cv(offre, dossier)
                    except ErreurGeneration as e:
                        if not self.cfg_cand.get("utiliser_cv_original_si_echec", True):
                            raise
                        self.logger.warning(f"{e} -> utilisation du CV original.")
                        cv_pdf = self.generateur.compiler_cv_original(dossier, offre)
                        pages_max = self.generateur.pages_max_cv
                        pages = compter_pages(cv_pdf)
                        if pages_max and pages > pages_max:
                            raise ErreurGeneration(
                                f"CV original lui-même trop long ({pages} page(s) > {pages_max}) : "
                                f"raccourcis input/cv_original.tex") from e
                        notes.append("CV original utilisé (personnalisation rejetée par les garde-fous)")
                    lm_pdf = self.generateur.generer_lm(offre, dossier)
                texte_lettre = latex_vers_texte(lm_pdf.with_suffix(".tex").read_text(encoding="utf-8"))
                self.excel.mettre_a_jour(ligne_excel, CV_PDF_Path=str(cv_pdf), LM_PDF_Path=str(lm_pdf))
                cache.update(cv=cv_pdf, lm=lm_pdf, lettre=texte_lettre)
            return cache["cv"], cache["lm"], cache["lettre"]

        return fournir

    def traiter(self, ligne: dict) -> str:
        """Traite une ligne de l'Excel de bout en bout. Renvoie le statut final."""
        n = ligne["_ligne"]
        tentatives = self.excel.marquer_en_cours(n)
        nom = str(ligne["Nom_Entreprise"]).strip() or "Entreprise"
        horodatage = datetime.now().strftime("%d_%m_%Y_%Hh%Mmin")   # ex. 19_09_2026_16h48min
        dossier = self.dossier_sortie / f"{slugifier(nom)}_{horodatage}"
        dossier.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"===== Ligne {n} : {nom} (tentative {tentatives}) =====")
        # Chien de garde : durée maximale de cette offre (voir AgentCandidature.duree_max_offre).
        duree_max = float(getattr(self.agent, "duree_max_offre", 0) or 0)
        self.agent.echeance = time.monotonic() + duree_max if duree_max > 0 else None
        self.agent.mdp_par_site = {}
        notes: list[str] = []
        # Chaque offre part d'une conversation de formulaire NEUVE. Une offre arrêtée tôt
        # (expirée, invalide…) ne passait pas par le bilan qui la ferme : l'offre suivante
        # reprenait alors la conversation de la précédente, et son onglet.
        self.agent.fermer_chat()

        try:
            offre = self.extraire_offre(ligne, dossier)
            # Nom d'entreprise découvert sur la page : on renomme le dossier avec le vrai nom
            if not str(ligne["Nom_Entreprise"]).strip() and offre["nom_entreprise"]:
                nouveau = self.dossier_sortie / f"{slugifier(offre['nom_entreprise'])}_{horodatage}"
                if not nouveau.exists():
                    try:
                        dossier = dossier.rename(nouveau)
                    except OSError:
                        pass  # dossier ouvert dans l'Explorateur : on garde l'ancien nom
            if not offre["offre_valide"]:
                raison = offre.get("raison_invalide") or "Offre indisponible"
                # Filet de sécurité : une page anti-robot que le modèle a jugée « invalide »
                # n'est pas une offre expirée — elle n'a juste pas pu être lue.
                if any(m in raison.lower() for m in MOTIFS_ANTI_ROBOT):
                    self.logger.info(f"Ligne {n} -> {em.ACTION_MANUELLE} : {raison}")
                    self.excel.definir_statut(n, em.ACTION_MANUELLE, f"Page anti-robot, offre non lue : {raison}")
                    return em.ACTION_MANUELLE
                self.logger.info(f"Ligne {n} -> {em.OFFRE_EXPIREE} : {raison}")
                self.excel.definir_statut(n, em.OFFRE_EXPIREE, raison)
                return em.OFFRE_EXPIREE
            hors_criteres = (not self.objectif.contrat_accepte(offre["type_contrat"])
                             or (self.objectif.verifie_duree and not offre["duree_compatible_6_mois"]))
            # Mode « postuler à tout » (option 5 du menu) : on ne demande plus ton avis sur le
            # contrat, on postule. Le type reste écrit dans l'Excel, tu vois donc après coup à
            # quoi le robot a postulé.
            if hors_criteres and self.agent.tout_postuler:
                self.logger.info(f"  Offre de type « {offre['type_contrat']} » (durée « "
                                 f"{offre.get('duree_indiquee', '')} ») : hors critères, mais mode "
                                 f"« postuler à tout » -> candidature quand même.")
            elif hors_criteres:
                motif = (f"Offre de type « {offre['type_contrat']} », durée « {offre.get('duree_indiquee', '')} » : "
                         f"vérifie si elle convient, puis remets « À traiter » (ou « Ignoré »).")
                self.excel.definir_statut(n, em.ACTION_MANUELLE, motif)
                return em.ACTION_MANUELLE

            # --- CV + Lettre : générés À LA DEMANDE ---
            # On garde la description et on commence à postuler ; le CV et la lettre ne sont
            # générés (DeepSeek + GPT) qu'au MOMENT où le formulaire réclame le fichier.
            # Ainsi, si l'offre bloque avant (connexion, CAPTCHA…), aucune génération inutile.
            fournir_documents = self._fabrique_documents(offre, dossier, n, notes)

            if self.args.documents_seulement:
                fournir_documents()   # ici, on génère tout de suite (c'est le but du mode)
                resultat = ResultatCandidature(em.PRET_TEST, "Documents générés (mode --documents-seulement)")
            # « Candidature par email » SANS adresse trouvée (ex. « Envoyez-nous votre CV » sur
            # Glassdoor) : la page a quand même son bouton « Postuler / Sign in to apply ».
            # On passe donc par le lien, au lieu d'abandonner l'offre.
            elif str(ligne["Lien_Offre"]).strip() and (offre["mode_candidature"] != "email"
                                                       or not offre["email_candidature"]):
                resultat = self.postuler_avec_rattrapage(str(ligne["Lien_Offre"]).strip(), offre,
                                                         fournir_documents, dossier / "captures")
            elif offre["email_candidature"]:
                if self._gmail_actif() and not (self.config.get("email", {}).get("actif")
                                                and envoi_smtp_configure(self.config)):
                    resultat = self._candidature_gmail(offre["email_candidature"], offre,
                                                       fournir_documents, dossier,
                                                       str(ligne["Lien_Offre"]).strip())
                else:
                    cv_pdf, lm_pdf, _ = fournir_documents()
                    resultat = self._envoi_email(offre["email_candidature"], offre, cv_pdf, lm_pdf)
            else:
                resultat = ResultatCandidature(em.ACTION_MANUELLE, "Aucun formulaire ni email de candidature trouvé")

            # Limite quotidienne Easy Apply de LinkedIn : l'offre reste « À traiter » (reprise plus
            # tard, à sa place dans l'Excel) et les 10 offres SUIVANTES sans Easy Apply passent
            # d'abord — elles se postulent sur le site de l'entreprise, que la limite ne bloque pas.
            if MARQUE_LIMITE_EASY_APPLY in resultat.commentaire:
                self._sans_easy_apply = self.OFFRES_SANS_EASY_APPLY
                motif = ("Limite quotidienne Easy Apply de LinkedIn atteinte : offre reportée (reprise "
                         "dans l'ordre de l'Excel dès que la limite est levée).")
                self.excel.definir_statut(n, em.A_TRAITER, motif)
                self.logger.info(f"Ligne {n} -> {em.A_TRAITER} : {motif} Passage aux "
                                 f"{self.OFFRES_SANS_EASY_APPLY} offres suivantes sans Easy Apply.")
                return em.A_TRAITER

            # Formulaire en échec mais l'offre donne une VRAIE adresse de candidature : on envoie
            # la candidature par Gmail plutôt que de l'abandonner. Jamais si l'envoi du site a
            # déjà été cliqué (candidature peut-être partie : pas de doublon).
            if (resultat.statut == em.ACTION_MANUELLE and offre.get("email_candidature")
                    and self._gmail_actif() and not self.args.documents_seulement
                    and not re.search(r"soumission cliquée|envoi cliqué", resultat.commentaire, re.I)):
                self.logger.info(f"  Formulaire en « Action manuelle » ({resultat.commentaire[:80]}) "
                                 f"-> candidature par email à {offre['email_candidature']}.")
                par_email = self._candidature_gmail(offre["email_candidature"], offre,
                                                    fournir_documents, dossier,
                                                    str(ligne["Lien_Offre"]).strip())
                if par_email.statut in (em.POSTULE, em.PRET_TEST):
                    resultat = par_email
                else:
                    notes.append(f"Envoi par email tenté sans succès : {par_email.commentaire[:150]}")

            # Mode « postuler à tout » : arrivé en « Action manuelle », c'est qu'un geste
            # HUMAIN est indispensable (CAPTCHA, création de compte, connexion) — les autres
            # « manuel » ont déjà été refusés trois fois. On laisse donc la page telle quelle
            # dans son onglet pour que l'utilisateur la règle, au lieu de la refermer en
            # passant à l'offre suivante.
            if resultat.statut == em.ACTION_MANUELLE and getattr(self.agent, "tout_postuler", False):
                self.nav.preserver_onglet_courant()

            commentaire = " | ".join([resultat.commentaire] + notes)
            self.excel.definir_statut(n, resultat.statut, commentaire)
            self.logger.info(f"Ligne {n} -> {resultat.statut} : {commentaire}")
            return resultat.statut

        except KeyboardInterrupt:
            self.excel.definir_statut(n, em.A_TRAITER, "Interrompu manuellement")
            raise
        except ChatEpuise as e:
            # DeepSeek saturé, à n'importe quel moment : lecture de l'offre, rédaction du CV,
            # formulaire. La règle est la même partout — voir _chat_sature.
            return self._chat_sature(n, e)
        except PageAntiRobot as e:
            self.logger.info(f"Ligne {n} -> {em.ACTION_MANUELLE} : {e}")
            self.excel.definir_statut(n, em.ACTION_MANUELLE, str(e))
            return em.ACTION_MANUELLE
        except Exception as e:  # noqa: BLE001 — on journalise tout et on passe à la suite
            attendue = isinstance(e, (ErreurOffre, ErreurGeneration, ErreurLLM))
            detail = "" if attendue else "\n" + traceback.format_exc()  # trace complète si erreur imprévue
            self.logger.error(f"Ligne {n} : {type(e).__name__} : {e}{detail}")
            if self.nav:
                self.nav.capture(dossier / "captures" / f"erreur_{datetime.now():%Y%m%d_%H%M%S}.png")
            max_t = int(self.cfg_cand.get("max_tentatives_par_offre", 2))
            suite = "" if tentatives < max_t else f" (abandon après {tentatives} tentatives)"
            self.excel.definir_statut(n, em.ERREUR, f"{type(e).__name__} : {str(e)[:500]}{suite}")
            return em.ERREUR

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def _ligne_demandee(self) -> dict | None:
        for d in self.excel.lire_toutes():
            if d["_ligne"] == self.args.ligne:
                return d
        self.logger.error(f"Ligne {self.args.ligne} introuvable ou vide dans l'Excel.")
        return None

    def _verifier_rapport(self) -> None:
        """Produit le rapport du jour une seule fois par jour, dès que l'heure choisie est passée."""
        if not self.cfg_rapport.get("actif", True):
            return
        aujourdhui = datetime.now().strftime("%Y-%m-%d")
        heure = str(self.cfg_rapport.get("heure", "20:00"))
        if datetime.now().strftime("%H:%M") < heure or self.memoire.lire_etat("dernier_rapport") == aujourdhui:
            return
        try:
            produire_rapport(self.config, self.profil, self.excel, self.memoire, self.logger)
            self.memoire.ecrire_etat("dernier_rapport", aujourdhui)
        except Exception as e:  # noqa: BLE001 — le rapport ne doit jamais arrêter le robot
            self.logger.error(f"Rapport quotidien impossible : {e}")

    def _dormir(self, secondes: float) -> None:
        """Attente découpée en tranches de 5 min, pour ne pas manquer l'heure du rapport."""
        fin = time.monotonic() + secondes
        while (reste := fin - time.monotonic()) > 0:
            time.sleep(min(reste, 300))
            self._verifier_rapport()

    def _attendre_demain(self) -> None:
        demain = (datetime.now() + timedelta(days=1)).replace(hour=8, minute=0, second=0)
        secondes = (demain - datetime.now()).total_seconds()
        self.logger.info(f"Quota journalier atteint. Reprise le {demain:%d/%m à %H:%M}.")
        self._dormir(max(secondes, 60))

    def lancer(self) -> None:
        max_t = int(self.cfg_cand.get("max_tentatives_par_offre", 2))
        quota = int(self.cfg_cand.get("max_candidatures_par_jour", 25))
        intervalle = float(self.cfg_cand.get("intervalle_verification_minutes", 30)) * 60
        pause = self.cfg_cand.get("pause_entre_candidatures_secondes", [60, 180])
        mode = "TEST (aucun envoi)" if not self.cfg_cand.get("soumission_automatique", True) else "ENVOI RÉEL"
        self.logger.info(f"Démarrage — mode {mode}")
        traitees = 0

        while True:
            self._verifier_rapport()
            try:
                sans_ea = self._sans_easy_apply > 0 and not self.args.ligne
                ligne = (self._ligne_demandee() if self.args.ligne else self.excel.prochaine_ligne(
                    max_t, exclure=self._est_easy_apply if sans_ea else None))
            except em.ExcelVerrouille as e:
                self.logger.error(f"{e} Nouvel essai dans 5 min.")
                self._dormir(300)
                continue

            if ligne is None and sans_ea:
                # Plus aucune offre sans Easy Apply : on laisse à LinkedIn le temps de lever sa
                # limite, puis retour à l'ordre normal.
                self.logger.info(f"Limite Easy Apply : plus aucune offre sans Easy Apply à traiter. "
                                 f"Nouvel essai dans l'ordre normal dans {intervalle / 60:.0f} min.")
                self._sans_easy_apply = 0
                self._dormir(intervalle)
                continue
            if ligne is None:
                if self.args.once or self.args.ligne:
                    self.logger.info("Plus aucune offre à traiter. Fin.")
                    return
                # Excel vide : scraping automatique (au plus un toutes les 2 h), puis on relit
                # l'Excel aussitôt pour traiter les offres ajoutées.
                if self._scraping_auto():
                    continue
                self.logger.info(f"Aucune offre en attente. Prochaine vérification dans {intervalle / 60:.0f} min.")
                self._dormir(intervalle)
                continue

            # quota = 0 : aucune limite par jour, le robot traite les offres sans jamais s'arrêter.
            if (quota > 0 and not self.args.documents_seulement
                    and self.excel.candidatures_du_jour() >= quota):
                if self.args.once:
                    self.logger.info("Quota journalier atteint. Fin.")
                    return
                self._attendre_demain()
                continue

            self._chat_sature_ce_tour = False
            try:
                self.nav.demarrer()  # relance Chrome s'il a été fermé / a planté
                self.traiter(ligne)
            except em.ExcelVerrouille as e:
                self.logger.error(str(e))

            # Garde-fou de la rotation : tous les navigateurs ont échoué d'affilée. Ce n'est
            # plus un compte saturé mais DeepSeek ou l'adresse IP — on s'arrête le temps que
            # ça passe, au lieu de brûler les offres suivantes une à une.
            if self._pause_saturation:
                self._pause_saturation = False
                self._echecs_chat_consecutifs = 0
                minutes = float(self.cfg_cand.get("pause_saturation_minutes", 30))
                reprise = datetime.now() + timedelta(minutes=minutes)
                self.logger.warning(f"DeepSeek saturé sur tous les navigateurs — pause de "
                                    f"{minutes:.0f} min, reprise vers {reprise:%H:%M} sur le "
                                    f"navigateur {self.nav.index_navigateur + 1}.")
                self._dormir(minutes * 60)
                continue
            if self._chat_sature_ce_tour:
                if self.args.ligne:
                    # --ligne relit toujours la même ligne, quel que soit son statut : sans
                    # cet arrêt, une saturation la ferait rejouer indéfiniment.
                    self.logger.info("Mode --ligne : pas de rejeu automatique après saturation. Fin.")
                    return
                continue     # offre remise « À traiter » : rejouée tout de suite, sans pause
            # L'offre est passée sans saturation : DeepSeek répond, la série d'échecs est close.
            self._echecs_chat_consecutifs = 0
            if sans_ea and self._sans_easy_apply > 0:
                self._sans_easy_apply -= 1          # une offre sans Easy Apply de faite
                if self._sans_easy_apply == 0:
                    self.logger.info("Limite Easy Apply : 10 offres sans Easy Apply traitées -> "
                                     "retour à l'ordre normal de l'Excel.")
            traitees += 1

            if self.args.ligne or (self.args.limite and traitees >= self.args.limite):
                self.logger.info(f"{traitees} offre(s) traitée(s). Fin.")
                return
            if self.excel.prochaine_ligne(max_t) is not None:
                pause_aleatoire(pause, self.logger)


def main() -> None:
    parser = argparse.ArgumentParser(description="Candidatures automatiques — orchestrateur")
    parser.add_argument("--once", action="store_true", help="traite les offres en attente puis s'arrête")
    parser.add_argument("--test", action="store_true", help="remplit les formulaires sans cliquer sur Envoyer")
    parser.add_argument("--documents-seulement", action="store_true",
                        help="génère uniquement CV + LM, sans candidater")
    parser.add_argument("--ligne", type=int, help="traite uniquement cette ligne de l'Excel (numéro de ligne Excel)")
    parser.add_argument("--limite", type=int, default=0, help="nombre max d'offres à traiter puis arrêt")
    parser.add_argument("--verifier", action="store_true", help="vérifie la configuration puis s'arrête")
    parser.add_argument("--rapport", action="store_true", help="produit (et envoie) le rapport du jour puis s'arrête")
    parser.add_argument("--tout-postuler", action="store_true",
                        help="postule à TOUT sans rien demander : le type de contrat (CDI, CDD, "
                             "alternance) et la durée n'arrêtent plus la candidature, et un « manuel » "
                             "du modèle est refusé tant que ce n'est pas un vrai blocage (CAPTCHA "
                             "à défi, code SMS, inscription impossible)")
    args = parser.parse_args()

    orchestrateur = Orchestrateur(args)
    if args.rapport:
        chemin = produire_rapport(orchestrateur.config, orchestrateur.profil, orchestrateur.excel,
                                  orchestrateur.memoire, orchestrateur.logger)
        print("\n" + chemin.read_text(encoding="utf-8"))
        return
    if not orchestrateur.verifier_prerequis():
        orchestrateur.logger.error("Corrige les points ci-dessus puis relance. (Voir README, section Configuration.)")
        sys.exit(1)
    if args.verifier:
        return
    try:
        orchestrateur.initialiser()
        orchestrateur.lancer()
    except KeyboardInterrupt:
        orchestrateur.logger.info("Arrêt demandé (Ctrl+C).")
    finally:
        orchestrateur.fermer_onglets_assistants()


if __name__ == "__main__":
    main()
