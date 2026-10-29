"""
Pilotage du CHAT WEB Gemini (abonnement Google), utilisé comme moteur de remplissage
de formulaire à la place de l'API OpenAI.

Principe : le chat Gemini est ouvert dans SON PROPRE onglet (l'onglet « B »), distinct
de l'onglet DeepSeek qui génère le CV et la lettre (onglet « A ») et de l'onglet du site
d'emploi (onglet « C »). Une conversation = UNE candidature : elle est ouverte au premier
besoin, gardée pendant tout le formulaire, puis fermée à la fin de la candidature.

Ce module expose la MÊME interface que ClientLLM.generer(...), pour que
candidature.py fonctionne à l'identique quel que soit le moteur :

    moteur = ClientGeminiChat(config, nav, logger)
    texte = moteur.generer(prompt_systeme, message, images=[jpeg], tache="formulaire")
    moteur.fermer()          # fin de candidature -> l'onglet est fermé

Le chat étant STATEFUL (contrairement à l'API OpenAI qui est sans mémoire), le préfixe
commun entre deux messages successifs — profil, offre, lettre, qui ne changent pas — n'est
envoyé qu'une fois : les tours suivants ne transmettent que ce qui a réellement changé.
"""

from __future__ import annotations

import json
import re
import logging
import time
from pathlib import Path

from confidentialite import courante as confidentialite
from utils import chemin_projet, extraire_json

URL_DEFAUT = "https://gemini.google.com/u/2/app?hl=fr"

# Réponses du modèle déjà affichées dans la conversation
JS_REPONSES = r"""
(() => {
  const els = [...document.querySelectorAll('message-content, model-response, [id^="model-response"]')];
  return JSON.stringify({n: els.length,
    dernier: els.length ? (els[els.length - 1].innerText || '') : ''});
})()
"""

# Champ de saisie (contenteditable Angular)
JS_COMPOSER = r"""
(() => {
  const e = [...document.querySelectorAll('[role=textbox], textarea')]
    .find(x => x.getBoundingClientRect().height > 10);
  if (!e) return null;
  const r = e.getBoundingClientRect();
  return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2});
})()
"""

# Bouton « Envoyer » actif
JS_ENVOYER = r"""
(() => {
  const e = [...document.querySelectorAll('button')].find(x =>
    /envoyer|send/i.test((x.getAttribute('aria-label') || '') + ' ' + (x.innerText || ''))
    && x.getBoundingClientRect().width > 5 && !x.disabled);
  if (!e) return 'introuvable';
  e.click();
  return 'ok';
})()
"""

# Un bouton « stop / arrêter la génération » est-il visible ? (réponse en cours)
# Il vivait dans deepseek_chat.py sans y être utilisé, et gemini_chat.py, qui s'en sert,
# ne l'importait pas : l'appel échouait, et « en cas de doute » _generation_en_cours
# répondait toujours OUI — chaque réponse attendait alors FIGE_SUR (25 s) pour rien.
JS_EN_COURS = r"""
(() => [...document.querySelectorAll('button, [role="button"], [aria-label]')].some(b => {
  const t = ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '')).toLowerCase();
  const r = b.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && /stop|arrêter|arreter|停止|interrompre/.test(t);
}))()
"""

# Bouton « + » (Importation et outils) : position pour un VRAI clic souris.
# Un .click() JavaScript ne suffit pas sur ces composants Angular.
JS_BOUTON_PLUS = r"""
(() => {
  const e = [...document.querySelectorAll('button,[role=button]')].find(x =>
    /importation et outils|joindre|ajouter des fichiers|import and tools/i
      .test(x.getAttribute('aria-label') || x.innerText || '')
    && x.getBoundingClientRect().width > 5);
  if (!e) return null;
  const r = e.getBoundingClientRect();
  return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2});
})()
"""

# Longueur du texte actuellement dans le champ de saisie : 0 = le message est parti.
JS_LONGUEUR_SAISIE = r"""
(() => {
  const e = [...document.querySelectorAll('[role=textbox], textarea')]
    .find(x => x.getBoundingClientRect().height > 10);
  return e ? (e.innerText || e.value || '').trim().length : -1;
})()
"""

# Pièce jointe bien prise en compte (vignette affichée)
JS_PIECE_JOINTE = r"""
(() => !!document.querySelector('img[src^="blob:"], img[src^="data:image"]'))()
"""


# Réponses d'esquive de Gemini : il arrive qu'il refuse une tâche qu'il traite pourtant très
# bien d'habitude (typiquement une réécriture LaTeX). Ce n'est pas un vrai refus de fond, juste
# un raté ponctuel — on repart donc d'une conversation neuve avant d'escalader.
MOTIFS_REFUS = re.compile(
    r"je ne peux pas vous aider|je ne suis qu'un mod[èe]le de langage|je ne comprends pas cela|"
    r"i can'?t help|i'?m (just |only )?a language model|i can'?t assist|"
    r"je ne suis pas en mesure|d[ée]sol[ée],? je ne peux",
    re.IGNORECASE)


# Rappelé à CHAQUE tour : le chat a de la mémoire pour le contenu, mais il dérive sur la
# forme attendue si on ne la redonne jamais.
RAPPEL_FORMAT = (
    "\n\n---\nRAPPEL DU FORMAT DE RÉPONSE, obligatoire : renvoie UNIQUEMENT un objet JSON, "
    'de la forme {"analyse": "...", "actions": [ ... ], "statut": "continuer"}. '
    'La clé se nomme bien "actions" (au pluriel, une LISTE), chaque action ayant un "type" '
    '("cliquer", "saisir", "selectionner", "televerser", "defiler", "attendre"…) — et surtout '
    'PAS {"action": ..., "index": ..., "raison": ...}, que le robot ne sait pas exécuter. '
    "Aucun texte ni balise Markdown autour du JSON."
)


# Messages d'erreur de Gemini lui-même (bandeau rouge, « Réessayer »…). Ce ne sont pas des
# réponses : inutile d'attendre la fin du délai, il faut rouvrir la conversation et réessayer.
MOTIFS_ERREUR = re.compile(
    r"une erreur s'?est produite|something went wrong|r[ée]essayer|try again|"
    r"impossible de traiter|unable to (process|complete)|erreur interne|"
    r"quelque chose s'?est mal pass",
    re.IGNORECASE)


def est_une_erreur_gemini(texte: str) -> bool:
    """Vrai si la « réponse » est en fait un message d'erreur affiché par Gemini."""
    t = (texte or "").strip()
    return bool(t) and len(t) < 300 and bool(MOTIFS_ERREUR.search(t))


def json_complet(texte: str) -> bool:
    """
    Vrai si la réponse contient un objet JSON exploitable jusqu'au bout.

    C'est le signal le plus sûr pour reconnaître une réponse COUPÉE : une action est un JSON,
    et un JSON est complet ou il ne l'est pas. Même raisonnement que « \\end{document} » pour
    un CV — on ne se fie pas à un minuteur, on vérifie le contenu.
    """
    try:
        extraire_json(texte)
        return True
    except ValueError:
        return False


def est_un_echo(envoye: str, reponse: str) -> bool:
    """
    Vrai si Gemini s'est contenté de RECOPIER la question au lieu d'y répondre.

    Cela arrive sur les très longs messages (réécriture LaTeX) : la « réponse » reprend le
    prompt mot pour mot. Elle contient donc \\documentclass et \\end{document} et passe les
    contrôles de forme, mais ne compile évidemment pas — on perdait un essai à chaque fois.
    """
    r = (reponse or "").strip()
    e = (envoye or "").strip()
    if len(r) < 200 or len(e) < 200:
        return False
    debut = e[:200]
    return debut in r[:len(debut) + 400]


def est_un_refus(texte: str) -> bool:
    """Vrai si la réponse est une esquive plutôt qu'un vrai résultat (courte ET évasive)."""
    t = (texte or "").strip()
    return bool(t) and len(t) < 600 and bool(MOTIFS_REFUS.search(t))


class ErreurGeminiChat(Exception):
    """Le chat Gemini n'a pas pu être piloté (onglet, saisie ou réponse)."""


class RefusGemini(ErreurGeminiChat):
    """Gemini a esquivé même après réouverture d'une conversation neuve : passer au secours."""


class ErreurGemini(ErreurGeminiChat):
    """Gemini a affiché son propre message d'erreur : rouvrir la conversation et réessayer."""


class ChatEpuise(ErreurGeminiChat):
    """
    Le chat web reste indisponible après plusieurs essais espacés : son compte est saturé.

    Ce n'est pas un échec de la candidature. L'orchestrateur bascule sur le navigateur
    suivant — un Chrome complet, déjà connecté à un AUTRE compte du chat — et reprend la
    candidature DEPUIS LE DÉBUT : la conversation d'avant vivait dans l'autre navigateur,
    son contexte est perdu, et reprendre à mi-parcours n'aurait aucun sens.
    """


class ClientGeminiChat:
    """Moteur de remplissage de formulaire basé sur le chat web Gemini."""

    # --- Ce qui est propre au SITE. Un moteur pour un autre chat (voir
    # ClientDeepSeekFormulaire) redéfinit uniquement ces attributs : la logique, elle,
    # reste STRICTEMENT la même puisqu'elle est héritée telle quelle.
    NOM = "Gemini"
    CLE_CONFIG = "gemini_chat"
    HOTE = "gemini.google"
    MARQUE_CONVERSATION = "/app/"      # une URL qui contient ça = une conversation ouverte
    JS_REPONSES = JS_REPONSES
    JS_COMPOSER = JS_COMPOSER
    JS_ENVOYER = JS_ENVOYER
    JS_BOUTON_PLUS = JS_BOUTON_PLUS
    JS_LONGUEUR_SAISIE = JS_LONGUEUR_SAISIE
    JS_PIECE_JOINTE = JS_PIECE_JOINTE
    URL_DEFAUT = URL_DEFAUT
    DOSSIER_TMP = "_gemini"
    TACHE_BILAN = "diagnostic"      # nom de tâche du bilan de fin de candidature
    # Tâches dont la réponse DOIT être un JSON : ce sont les seules où l'on peut affirmer
    # qu'une réponse est coupée. Une réponse en prose ou en LaTeX n'a pas de fin repérable.
    TACHES_JSON = ("formulaire", "diagnostic")
    # Vrai pendant un échange qui attend une ACTION en JSON : la réponse est alors finie dès
    # que son JSON est complet (voir _attendre_reponse).
    _attend_json = False
    # Texte figé aussi longtemps = terminé, même si le détecteur de génération dit le
    # contraire. Filet contre un bouton « arrêter » que l'interface aurait renommé.
    FIGE_SUR = 25.0
    RELANCE_COUPEE = ("Ta réponse précédente a été coupée avant la fin. Renvoie UNIQUEMENT "
                      "le JSON complet demandé, rien d'autre — pas de texte autour.")
    # Index Tappi (data-bpy-idx) du champ de saisie, lu apres nav.elements().
    JS_INDEX_SAISIE = r"""
    (() => {
      const e = [...document.querySelectorAll('[role=textbox], textarea')]
        .find(x => x.getBoundingClientRect().height > 10);
      return e ? e.getAttribute('data-bpy-idx') : null;
    })()
    """

    def __init__(self, config: dict, nav, logger: logging.Logger | None = None):
        cfg = (config.get(self.CLE_CONFIG) or {})
        self.nav = nav
        self.logger = logger or logging.getLogger("job_automation")
        self.url = str(cfg.get("url", self.URL_DEFAUT))
        self.timeout = float(cfg.get("timeout_reponse_secondes", 120))
        self.stabilite = float(cfg.get("stabilite_secondes", 4))
        # Chat MUET (serveur occupé, compte limité) : aucune réponse ne commence. Constaté après
        # ce délai au lieu d'attendre `timeout` en entier -> nouvel essai, puis bascule.
        self.delai_demarrage = float(cfg.get("delai_demarrage_secondes", 30))
        self.envoyer_images = bool(cfg.get("envoyer_captures", True))
        # Indisponibilité du chat (« Le serveur est occupé, réessayez plus tard », délai
        # dépassé…) : on ne se fie PAS au texte du message, qui change de formulation et de
        # langue, mais à sa PERSISTANCE. On repose la question en attendant de plus en plus
        # longtemps ; l'erreur ne remonte qu'après le dernier essai.
        self.reessais = max(1, int(cfg.get("reessais_indisponibilite", 3)))
        pauses = cfg.get("pauses_indisponibilite_secondes") or [20, 60, 120]
        self.pauses = [float(p) for p in pauses]
        # Si l'indisponibilité DURE malgré ces essais, ce chat lève ChatEpuise : c'est
        # l'orchestrateur qui bascule alors sur le navigateur suivant (voir
        # navigateur.basculer_navigateur). Aucune déconnexion n'est jamais tentée ici.
        # Le bilan de fin de candidature reste-t-il DANS la conversation qui a rempli le
        # formulaire ? Le modèle y a vu les pages : son diagnostic est plus juste.
        # Mettre false dans la config pour revenir à une conversation dédiée.
        self.bilan_dans_le_chat = bool(cfg.get("bilan_dans_le_chat", True))
        dossier = chemin_projet(config.get("chemins", {}).get("captures", "output/captures"))
        self._dossier_tmp = Path(dossier) / self.DOSSIER_TMP
        self._id_onglet: str = ""            # id CDP de l'onglet du chat (stable)
        self._systeme_envoye: str = ""       # prompt système déjà transmis à CETTE conversation
        self._dernier_message: str = ""      # pour n'envoyer que le delta au tour suivant
        self._dernier_envoi: str = ""        # texte réellement transmis (détection d'écho)
        self._url_conversation: str = ""     # lien du chat en cours : porte sa mémoire

    # -- onglets ---------------------------------------------------------

    def _ouvrir_onglet(self) -> None:
        """
        Ouvre l'onglet du chat, UNE fois par candidature. Il reste ensuite ouvert jusqu'à la
        fin de la candidature : la conversation garde ainsi sa mémoire, et on ne paie pas un
        rechargement à chaque tour.

        Pendant l'échange, on ÉPINGLE cet onglet (nav.epingler) : Tappi vise sinon toujours
        l'onglet le plus récemment créé, ce qui ferait lire la page du chat à la place de
        celle de l'offre. L'épinglage est rendu à l'onglet de l'offre dès l'échange terminé.
        """
        if self._id_onglet and any(getattr(t, "id", "") == self._id_onglet
                                   for t in self.nav.b.tabs()):
            return                                  # onglet déjà ouvert : on le réutilise
        # Nouvel ONGLET (même fenêtre) ; l'épinglage garantit que chacun vise la bonne page.
        # Si une conversation est DÉJÀ en cours (onglet fermé, planté, perdu en route), on
        # rouvre directement SON lien plutôt qu'un chat vierge : le profil, l'offre et les
        # étapes déjà faites y sont encore. Passer par un chat neuf obligeait à tout
        # retransmettre, et faisait repartir le modèle sans l'historique.
        reprise = bool(self._url_conversation)
        self._id_onglet = self.nav.ouvrir_onglet_assistant(self._url_conversation or self.url)
        self.nav.b.wait(4000)
        if not self._id_onglet:
            raise ErreurGeminiChat(f"L'onglet {self.NOM} ne s'est pas ouvert. Vérifie que le "
                                   f"profil Chrome du robot est connecté au compte.")
        precedent = self.nav.epingler(self._id_onglet)
        try:
            hote = str(self.nav.b.eval("location.host") or "")
        finally:
            self.nav.epingler(precedent)
        if self.HOTE not in hote:
            raise ErreurGeminiChat(f"L'onglet ouvert n'est pas {self.NOM} (host={hote}).")
        if reprise:
            self.logger.info(f"{self.NOM} : conversation en cours rouverte sur son lien "
                             f"enregistré (contexte conservé).")
        else:
            self.logger.info(f"{self.NOM} : nouvelle conversation ouverte "
                             f"(gardée pour la candidature).")

    def _memoriser_conversation(self) -> None:
        """
        Retient le lien de la conversation en cours. Gemini crée une URL .../app/<id> dès le
        premier message : c'est elle qui porte toute la mémoire du chat (profil, offre, CV,
        étapes déjà faites).
        """
        try:
            url = str(self.nav.b.url() or "")
        except Exception:  # noqa: BLE001
            return
        if self.HOTE in url and self.MARQUE_CONVERSATION in url:
            self._url_conversation = url

    def _verifier_conversation(self) -> None:
        """
        S'assure, avant chaque envoi, que l'onglet est bien sur la conversation en cours.
        Si Gemini a été rechargé ou redirigé vers l'accueil, on y revient — sinon la question
        partirait dans un chat vide, sans le profil ni l'historique de la candidature.
        """
        if not self._url_conversation:
            return
        try:
            courante = str(self.nav.b.url() or "")
        except Exception:  # noqa: BLE001
            return
        if self.MARQUE_CONVERSATION in courante:
            return
        self.logger.info(f"{self.NOM} : l'onglet a quitté la conversation "
                         f"-> retour au chat en cours.")
        self._recharger_conversation()

    def _recharger_conversation(self) -> bool:
        """
        Rouvre la MÊME conversation pour reposer la question, en conservant le contexte.

        Préférable à un chat vierge quand Gemini a bafouillé (écho, esquive) : le profil,
        l'offre et l'historique restent connus du modèle, inutile de tout retransmettre.
        Renvoie False s'il n'y a pas encore de conversation à rouvrir.
        """
        if not self._url_conversation:
            return False
        # L'onglet a pu disparaître (fermé, planté, session perdue). On en rouvre alors un
        # DIRECTEMENT SUR LE LIEN DE LA CONVERSATION, et surtout pas sur un chat vierge :
        # le profil, l'offre et les étapes déjà faites y sont encore, il n'y a rien à
        # retransmettre.
        if not any(getattr(t, "id", "") == self._id_onglet for t in self.nav.b.tabs()):
            self._id_onglet = self.nav.ouvrir_onglet_assistant(self._url_conversation)
            self.nav.b.wait(3500)
            if not self._id_onglet:
                return False
            self.nav.epingler(self._id_onglet)   # la suite de l'échange doit viser CE onglet
            self.logger.info(f"{self.NOM} : onglet perdu -> conversation rouverte sur son "
                             f"lien enregistré (contexte conservé).")
            self._dernier_message = ""
            self._dernier_envoi = ""
            return True
        precedent = self.nav.epingler(self._id_onglet)
        try:
            self.nav.b.open(self._url_conversation)
            self.nav.b.wait(3500)
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"{self.NOM} : rechargement de la conversation impossible ({e})")
            return False
        finally:
            self.nav.epingler(precedent)
        # Le contexte est toujours là : on ne renvoie que la question, pas tout le préambule.
        self._dernier_message = ""
        self._dernier_envoi = ""
        return True

    def _nouvelle_conversation(self) -> None:
        """
        Repart d'un chat vierge SANS changer d'onglet : on recharge simplement l'URL de base.
        L'onglet Gemini reste donc le même pendant toute la candidature.
        """
        precedent = self.nav.epingler(self._id_onglet)
        try:
            self.nav.b.open(self.url)
            self.nav.b.wait(3000)
        except Exception as e:  # noqa: BLE001 — au pire on garde la conversation en cours
            self.logger.debug(f"{self.NOM} : nouvelle conversation impossible ({e})")
        finally:
            self.nav.epingler(precedent)
        self._systeme_envoye = ""
        self._dernier_message = ""
        self._dernier_envoi = ""
        self._url_conversation = ""

    def _fermer_onglet(self) -> None:
        """Referme l'onglet du chat : l'onglet de l'offre redevient l'onglet courant."""
        for _ in range(3):
            cible = next((t for t in self.nav.b.tabs()
                          if getattr(t, "id", "") == self._id_onglet), None)
            if cible is None:
                break
            try:
                self.nav.b.close_tab(cible.index)
            except Exception:  # noqa: BLE001 — index périmé : on relit et on réessaie
                pass
            time.sleep(0.4)
        self._id_onglet = ""

    def fermer(self) -> None:
        """
        Fin de candidature : c'est ICI, et seulement ici, que l'onglet du chat est refermé.
        La candidature suivante repartira d'une conversation vierge.
        """
        ouvert = bool(self._id_onglet)
        self._fermer_onglet()
        if ouvert:
            self.logger.info(f"{self.NOM} : conversation fermée (fin de candidature).")
        self._systeme_envoye = ""
        self._dernier_message = ""
        # Oublier le lien est ESSENTIEL : sans ça, la candidature suivante rouvrirait la
        # conversation de la précédente et mélangerait les deux offres.
        self._url_conversation = ""

    # -- échange ---------------------------------------------------------

    def _eval(self, js: str):
        """
        eval() de Tappi, mais un résultat JS `null` revient sous forme de dict
        ({'type':'object','subtype':'null',...}) et non de None — ce qui rendait vrais
        des tests comme « if not pos ». On normalise en None.
        """
        try:
            valeur = self.nav.b.eval(js)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(valeur, dict) and valeur.get("subtype") == "null":
            return None
        return valeur

    def _etat(self) -> dict:
        try:
            brut = self.nav.b.eval(self.JS_REPONSES)
            return json.loads(brut) if isinstance(brut, str) else {"n": 0, "dernier": ""}
        except Exception:  # noqa: BLE001
            return {"n": 0, "dernier": ""}

    def _generation_en_cours(self) -> bool:
        """
        Le modèle est-il encore en train d'écrire (ou de réfléchir) ?

        Repéré par le bouton « arrêter la génération », visible tant que la réponse n'est pas
        finie. En cas de doute — page illisible — on répond OUI : mieux vaut attendre un peu
        trop qu'emporter une réponse à moitié écrite.
        """
        try:
            return bool(self.nav.b.eval(JS_EN_COURS))
        except Exception:  # noqa: BLE001
            return True

    def _relancer_reponse_coupee(self) -> str:
        """
        Relance COURTE après une réponse tronquée, dans la MÊME conversation.

        Le modèle a toujours la question et l'état de la page sous les yeux : lui renvoyer le
        message complet — des milliers de caractères et une capture — ne servirait à rien.
        Pire, le mécanisme de delta ne transmettrait presque rien, puisque ce message serait
        identique au précédent (déjà mémorisé comme reçu). Deux lignes suffisent, et elles
        disent exactement ce qui manque.
        """
        n_avant = self._etat()["n"]
        self._saisir_et_envoyer(self.RELANCE_COUPEE)
        return self._attendre_reponse(n_avant)

    def _clic_souris(self, x: float, y: float) -> None:
        """Vrai clic souris CDP : indispensable, le .click() JS est ignoré par l'UI Angular."""
        cdp = self.nav.b._connect_page()
        try:
            for evt in ("mousePressed", "mouseReleased"):
                cdp.send("Input.dispatchMouseEvent", type=evt, x=float(x), y=float(y),
                         button="left", clickCount=1, buttons=1)
        finally:
            cdp.close()

    def _joindre_image(self, jpeg: bytes) -> bool:
        """
        Joint une capture d'écran au message. Ouvre le menu « + » par un vrai clic souris,
        puis dépose le JPEG via CDP — en interceptant la fenêtre Windows de choix de fichier,
        qui sinon bloquerait le navigateur.
        """
        self._dossier_tmp.mkdir(parents=True, exist_ok=True)
        fichier = self._dossier_tmp / "page.jpg"
        fichier.write_bytes(jpeg)
        pos = self._eval(self.JS_BOUTON_PLUS)
        if not pos:
            return False
        coord = json.loads(pos)
        cdp = self.nav.b._connect_page()
        try:
            cdp.send("DOM.enable")
            cdp.send("Page.enable")
            cdp.send("Page.setInterceptFileChooserDialog", enabled=True)
            for evt in ("mousePressed", "mouseReleased"):
                cdp.send("Input.dispatchMouseEvent", type=evt, x=coord["x"], y=coord["y"],
                         button="left", clickCount=1, buttons=1)
            # Attendre que le champ fichier APPARAISSE, plutôt qu'un délai fixe : il est prêt
            # en ~0,3 s, on en attendait 1,8 s à chaque capture (une dizaine par candidature).
            oid, fin = None, time.monotonic() + 2.5
            while time.monotonic() < fin:
                res = cdp.send("Runtime.evaluate", returnByValue=False,
                               expression="document.querySelectorAll('input[type=file]')[0]")
                oid = res.get("result", {}).get("objectId")
                if oid:
                    break
                time.sleep(0.15)
            if not oid:
                return False
            cdp.send("DOM.setFileInputFiles", files=[str(fichier)], objectId=oid)
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"{self.NOM} : dépôt de la capture impossible ({e})")
            return False
        finally:
            try:
                cdp.send("Page.setInterceptFileChooserDialog", enabled=False)
            except Exception:  # noqa: BLE001
                pass
            cdp.close()
        # La vignette confirme la prise en compte. On vérifie souvent plutôt que d'attendre
        # une seconde entre deux essais : elle apparaît en général en moins d'une demi-seconde.
        fin = time.monotonic() + 5
        while time.monotonic() < fin:
            time.sleep(0.25)
            try:
                if self._eval(self.JS_PIECE_JOINTE):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _saisir_et_envoyer(self, texte: str) -> None:
        """
        Écrit le message puis l'envoie, et VÉRIFIE qu'il est bien parti.

        Le bouton « Envoyer » de Gemini est un composant Angular : un `.click()` JavaScript
        est souvent ignoré. On croyait pourtant l'envoi réussi (le bouton avait été trouvé),
        le texte restait dans la zone de saisie, et on attendait 120 s une réponse à une
        question jamais posée — d'où les « Aucune réponse de Gemini après 120 s ».
        La zone de saisie qui se vide est la seule preuve fiable que le message est parti.
        """
        # Vérification AVANT d'envoyer : est-on bien dans la conversation en cours ? Un
        # rechargement, une redirection ou un onglet reparti sur l'accueil ferait poser la
        # question dans un chat vide, sans le profil ni l'historique.
        self._verifier_conversation()
        # La page peut ne pas être encore prête : on laisse au composer le temps d'apparaître
        # plutôt que d'abandonner au premier coup d'œil (« Champ de saisie introuvable »).
        coord, fin = None, time.monotonic() + 8
        while time.monotonic() < fin:
            pos = self._eval(self.JS_COMPOSER)
            if pos:
                coord = json.loads(pos)
                break
            time.sleep(0.4)
        if coord is None:
            raise ErreurGeminiChat(f"Champ de saisie {self.NOM} introuvable après 8 s.")

        self._clic_souris(coord["x"], coord["y"])   # focus fiable
        time.sleep(0.3)
        # elements() numérote les éléments ; on lit l'index EXACT du champ de saisie plutôt
        # que de le deviner dans la liste (on y attrapait le bouton « Copier le prompt »).
        self.nav.b.elements()
        index = self._eval(self.JS_INDEX_SAISIE)
        if index is None:
            raise ErreurGeminiChat("Index du champ de saisie introuvable.")
        self.nav.b.type(int(index), texte)
        time.sleep(0.8)

        for tentative in (1, 2, 3):
            if tentative == 1:
                self._eval(self.JS_ENVOYER)                       # bouton « Envoyer »
            elif tentative == 2:
                self._clic_souris(coord["x"], coord["y"])    # refocus puis touche Entrée
                time.sleep(0.2)
                self._touche_entree()
            else:
                self._touche_entree()
            if self._saisie_videe():
                return
            self.logger.debug(f"{self.NOM} : message encore dans la zone de saisie "
                              f"(tentative {tentative}) — nouvel essai d'envoi.")
        raise ErreurGeminiChat("Le message n'a pas pu être envoyé : la zone de saisie "
                               "ne s'est pas vidée après 3 tentatives.")

    def _saisie_videe(self, max_secondes: float = 3.0) -> bool:
        """Vrai si la zone de saisie s'est vidée : le message est effectivement parti."""
        fin = time.monotonic() + max_secondes
        while time.monotonic() < fin:
            time.sleep(0.25)
            reste = self._eval(self.JS_LONGUEUR_SAISIE)
            if isinstance(reste, (int, float)) and reste == 0:
                return True
        return False

    def _touche_entree(self) -> None:
        """Envoie une vraie frappe Entrée (CDP) : le composant Angular y réagit, pas au JS."""
        cdp = self.nav.b._connect_page()
        try:
            for evt in ("keyDown", "keyUp"):
                cdp.send("Input.dispatchKeyEvent", type=evt, key="Enter", code="Enter",
                         windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        except Exception:  # noqa: BLE001
            pass
        finally:
            cdp.close()

    def _attendre_reponse(self, n_avant: int) -> str:
        """
        Attend une réponse NOUVELLE et stabilisée (le texte ne bouge plus).

        Un signe de vie est journalisé toutes les 15 s : une réécriture de CV peut occuper
        Gemini une bonne minute, pendant laquelle l'écran ne bouge pas — sans ce message on
        croit le robot planté et on l'interrompt alors qu'il travaille.
        """
        debut = fige_depuis = time.monotonic()
        fin = debut + self.timeout
        dernier, prochain_signe = "", debut + 15
        vu_en_cours = False   # le bouton « stop » a-t-il été vu pendant CETTE réponse ?
        while time.monotonic() < fin:
            # Relecture fréquente : l'action doit partir dès que le modèle a fini, pas
            # plusieurs secondes après.
            time.sleep(0.4)
            if time.monotonic() >= prochain_signe:
                prochain_signe += 15
                ecrit = len(dernier)
                self.logger.info(f"  {self.NOM} réfléchit depuis {time.monotonic() - debut:.0f} s"
                                 + (f" ({ecrit} caractères écrits)…" if ecrit else "…"))
            etat = self._etat()
            if etat["n"] <= n_avant:
                if not vu_en_cours and time.monotonic() - debut >= self.delai_demarrage:
                    if self._generation_en_cours():
                        vu_en_cours = True           # il réfléchit : on le laisse faire
                    else:
                        raise ErreurGeminiChat(f"{self.NOM} n'a pas commencé à répondre en "
                                               f"{self.delai_demarrage:.0f} s (serveur occupé ou compte limité).")
                continue
            # Gemini affiche parfois son propre message d'erreur (« Une erreur s'est
            # produite », « Réessayer »). Ce n'est pas une réponse : on rend la main tout de
            # suite au lieu d'attendre le délai complet — c'est ce qui faisait « réfléchir »
            # le robot pendant 45 s sur 40 caractères qui ne bougeaient plus.
            if est_une_erreur_gemini(etat["dernier"]):
                raise ErreurGemini(f"{self.NOM} a renvoyé une erreur : "
                                   f"« {' '.join(etat['dernier'].split())[:80]} »")
            en_cours = self._generation_en_cours()
            vu_en_cours = vu_en_cours or en_cours
            if etat["dernier"] != dernier:
                dernier, fige_depuis = etat["dernier"], time.monotonic()
            if not dernier:
                continue
            # FIN IMMÉDIATE, sans attendre que le texte reste figé :
            #  1) le bouton « stop » a été vu, puis a disparu : le modèle a terminé ;
            #  2) une ACTION est attendue (JSON) et elle est complète, modèle au repos.
            #     Un JSON ne se décode qu'une fois fermé : une réponse encore en cours
            #     d'écriture ne passe jamais ce test.
            if not en_cours and (vu_en_cours
                                 or (self._attend_json and json_complet(dernier))):
                return self._etat().get("dernier") or dernier   # dernière relecture
            # 3) FILET : aucun des deux signaux (détecteur en défaut, réponse en texte libre)
            #    -> on retombe sur l'ancienne règle, le texte figé pendant `stabilite` s.
            if time.monotonic() - fige_depuis >= self.stabilite:
                # FIGÉ NE VEUT PAS DIRE FINI. Pendant une pause du modèle, la page ne bouge
                # pas davantage qu'après une vraie fin de réponse — et on renvoyait alors une
                # réponse coupée en plein milieu, prise pour complète. Le bouton « arrêter la
                # génération » tranche : tant qu'il est là, le modèle écrit encore.
                # Et si ce détecteur est en défaut (interface modifiée), un figement très long
                # finit quand même par conclure : on ne reste jamais bloqué sur ce seul signal.
                if not en_cours or time.monotonic() - fige_depuis >= self.FIGE_SUR:
                    return dernier
        if not dernier:
            raise ErreurGeminiChat(f"Aucune réponse de {self.NOM} "
                                   f"après {self.timeout:.0f} s.")
        return dernier

    @staticmethod
    def _delta(precedent: str, message: str) -> str:
        """
        Le chat a de la mémoire : inutile de renvoyer le profil, l'offre et la lettre à chaque
        tour. On ne transmet que ce qui a changé depuis le message précédent.
        """
        if not precedent:
            return message
        maxi = min(len(precedent), len(message))
        commun = 0
        while commun < maxi and precedent[commun] == message[commun]:
            commun += 1
        commun = message.rfind("\n", 0, commun) + 1     # couper sur une ligne entière
        if commun < 800:
            return message
        return ("(Le profil, l'offre et les consignes du début de cette conversation restent "
                "valables — je ne renvoie que ce qui a changé.)\n\n" + message[commun:])

    # -- interface commune avec ClientLLM --------------------------------

    def generer(self, systeme: str, message: str, images: list[bytes] | None = None,
                tache: str | None = None) -> str:
        """Même signature que ClientLLM.generer : candidature.py ne voit aucune différence."""
        self._ouvrir_onglet()
        # Changement de tâche (formulaire -> réduction LaTeX -> bilan) : chacune a son prompt
        # système, donc il faut une conversation neuve. On REUTILISE l'onglet en le renvoyant
        # sur un chat vierge, au lieu de le fermer et d'en rouvrir un : un seul onglet Gemini
        # vit donc pendant toute la candidature, comme voulu.
        # EXCEPTION — le bilan de fin de candidature. On le laisse dans la conversation qui
        # vient de remplir le formulaire : le modèle y a VU les pages (captures, messages
        # d'erreur, fenêtres), son diagnostic est bien plus juste que depuis un simple
        # historique en texte. On change donc de consignes sans changer de conversation.
        meme_conversation = (bool(self._systeme_envoye) and self.bilan_dans_le_chat
                             and tache == self.TACHE_BILAN)
        if self._systeme_envoye and self._systeme_envoye != systeme and not meme_conversation:
            self._nouvelle_conversation()
        # L'onglet du chat RESTE ouvert pendant toute la candidature ; on l'épingle le temps
        # de l'échange, puis on rend la main à l'onglet de l'offre.
        onglet_offre = self.nav.epingler(self._id_onglet)
        try:
            self._attend_json = tache in self.TACHES_JSON
            reponse = self._echange_avec_patience(systeme, message, images, meme_conversation)
            # Réponse COUPÉE en plein milieu : le JSON attendu ne se décode pas. Plutôt que
            # de la transmettre telle quelle — l'appelant échouerait à la lire et brûlerait
            # une étape — on relance en deux lignes dans la même conversation.
            if tache in self.TACHES_JSON and reponse and not json_complet(reponse):
                self.logger.warning(f"{self.NOM} : réponse coupée (JSON incomplet, "
                                    f"{len(reponse)} caractères) -> relance courte.")
                try:
                    complement = self._relancer_reponse_coupee()
                except ErreurGeminiChat as e:
                    self.logger.warning(f"{self.NOM} : relance impossible ({e}) — on garde "
                                        f"la réponse telle quelle.")
                else:
                    if json_complet(complement):
                        self.logger.info(f"{self.NOM} : la relance a donné une réponse complète.")
                        reponse = complement
                    elif complement:
                        reponse = complement   # toujours incomplète : l'appelant le signalera
            # Deux ratés ponctuels, à traiter pareil : Gemini esquive la demande, ou il
            # RECOPIE la question au lieu d'y répondre. Dans les deux cas on repart d'une
            # conversation NEUVE et on repose la question une fois.
            echo = est_un_echo(self._dernier_envoi, reponse)
            if est_un_refus(reponse) or echo:
                motif = ("a recopié la question au lieu d'y répondre" if echo
                         else f"a esquivé (« {' '.join(reponse.split())[:60]}… »)")
                # On ROUVRE la même conversation : le profil, l'offre et les étapes déjà
                # faites y sont toujours connus. Un chat vierge obligerait à tout
                # retransmettre — plus long, et on repartirait sans l'historique.
                if self._recharger_conversation():
                    self.logger.warning(f"{self.NOM} {motif} -> même conversation rouverte, "
                                        f"question reposée.")
                else:
                    self.logger.warning(f"{self.NOM} {motif} -> conversation neuve "
                                        f"et nouvel essai.")
                    self._nouvelle_conversation()
                reponse = self._un_echange(systeme, message, images, meme_conversation)
                if est_un_refus(reponse) or est_un_echo(self._dernier_envoi, reponse):
                    raise RefusGemini(f"{self.NOM} n'a pas répondu correctement deux fois de "
                                      f"suite, y compris dans une conversation neuve.")
                self.logger.info(f"{self.NOM} : le nouvel essai a abouti.")
            # Repères de la réponse remis en vraies valeurs : « saisir {EMAIL} » tape le vrai email.
            return confidentialite().demasquer(reponse)
        finally:
            self.nav.epingler(onglet_offre)

    def _echange_avec_patience(self, systeme: str, message: str, images: list[bytes] | None,
                               meme_conversation: bool) -> str:
        """
        Un échange, avec plusieurs reprises ESPACÉES quand le chat se dit indisponible.

        « Le serveur est occupé, réessayez plus tard », un délai dépassé, un message qui ne
        part pas : ce sont presque toujours des incidents PASSAGERS. Le déclencheur n'est donc
        pas le TEXTE du message — il change de formulation, de langue, et on ne les connaît
        pas tous — mais sa PERSISTANCE : on repose la question en attendant de plus en plus
        longtemps. Si ça passe au 2e ou au 3e essai, la candidature continue comme si de rien
        n'était, au lieu d'être perdue pour une saturation de trente secondes.

        L'erreur ne remonte qu'après le dernier essai. À ce moment-là elle veut dire quelque
        chose : l'indisponibilité dure, ce n'est plus un hoquet.

        Entre deux essais, on rouvre le LIEN ENREGISTRÉ de la conversation : tout son contexte
        y est encore (profil, offre, étapes déjà faites). À défaut seulement, conversation
        neuve — plus lent, puisqu'il faut tout retransmettre, mais jamais bloquant.
        """
        derniere: Exception | None = None
        for essai in range(1, self.reessais + 1):
            try:
                return self._un_echange(systeme, message, images, meme_conversation)
            except (ErreurGemini, ErreurGeminiChat) as e:
                derniere = e
                if essai >= self.reessais:
                    break
                pause = self.pauses[min(essai, len(self.pauses)) - 1]
                self.logger.warning(f"{e}")
                self.logger.warning(f"  {self.NOM} indisponible (essai {essai}/{self.reessais}) "
                                    f"-> nouvelle tentative dans {pause:.0f} s.")
                time.sleep(pause)
                if self._recharger_conversation():
                    self.logger.info(f"  {self.NOM} : conversation rouverte sur son lien "
                                     f"enregistré (contexte conservé).")
                else:
                    self.logger.info(f"  {self.NOM} : lien de conversation indisponible "
                                     f"-> conversation neuve.")
                    self._nouvelle_conversation()
        # Indisponibilité DURABLE. C'est seulement ici — après plusieurs essais espacés, donc
        # jamais sur une saturation passagère — qu'on change de compte. Si la rotation
        # aboutit, on ne renvoie pas la question : le compte a changé, la conversation
        # précédente est perdue, et c'est toute la candidature qui doit repartir de zéro.
        # Indisponibilité DURABLE : le compte de CE navigateur est très probablement saturé
        # pour la journée. On ne tente aucune déconnexion — on signale simplement que ce chat
        # est épuisé. C'est l'orchestrateur qui décidera de basculer sur le navigateur suivant,
        # déjà connecté avec un autre compte, et de reprendre la candidature de zéro.
        self.logger.error(f"{self.NOM} toujours indisponible après {self.reessais} essais "
                          f"espacés — ce n'est plus un incident passager.")
        raise ChatEpuise(f"{self.NOM} indisponible après {self.reessais} essais espacés "
                         f"(limite du compte atteinte ?)") from derniere

    def _un_echange(self, systeme: str, message: str, images: list[bytes] | None,
                    consignes_neuves: bool = False) -> str:
        """Un aller-retour : compose le message (complet ou delta), envoie, attend la réponse."""
        premier = not self._systeme_envoye
        if premier or consignes_neuves:
            # `consignes_neuves` : on change de tâche SANS changer de conversation (bilan de
            # fin de candidature). Le modèle garde tout ce qu'il a vu, mais il lui faut les
            # nouvelles consignes — et surtout PAS le rappel du format des actions ci-dessous,
            # qui lui ferait renvoyer un JSON d'actions au lieu du JSON du bilan.
            envoi = f"{systeme}\n\n---\n\n{message}"
        else:
            # Le contrat de SORTIE n'était rappelé qu'au premier message : au fil de la
            # conversation Gemini s'en écartait et inventait son propre schéma
            # ({"action","index","raison"}), que le robot ne sait pas exécuter — il tournait
            # alors à vide. Le rappel est court, on le remet à chaque tour.
            envoi = self._delta(self._dernier_message, message) + RAPPEL_FORMAT

        n_avant = self._etat()["n"]
        if images and self.envoyer_images:
            if not self._joindre_image(images[0]):
                self.logger.debug(f"{self.NOM} : capture non jointe, envoi en texte seul.")
        # CONFIDENTIALITÉ : prénom, nom, email, téléphone et adresse ne partent JAMAIS vers le
        # chat — des repères ({PRENOM}, {EMAIL}…) à la place, comme pour le mot de passe.
        envoi = confidentialite().masquer(envoi)
        self._dernier_envoi = envoi
        self._saisir_et_envoyer(envoi)
        reponse = self._attendre_reponse(n_avant)
        self._memoriser_conversation()
        # On ne considère le prompt système « transmis » qu'après une réponse effective :
        # sinon un premier échange raté (délai dépassé) le marquait comme envoyé, et les
        # consignes de format n'étaient JAMAIS transmises de toute la conversation.
        self._systeme_envoye = systeme
        self._dernier_message = message
        return reponse
