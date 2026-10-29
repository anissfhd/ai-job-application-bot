"""
Pilotage du CHAT DeepSeek (chat.deepseek.com) pour générer le CV et la lettre
de motivation en LaTeX — à la place de l'API GPT, pour réduire le coût.

Déroulé pour une offre :
  1. ouvrir une nouvelle conversation DeepSeek ;
  2. envoyer, dans l'ordre, les 3 messages privés (config/deepseek/message_1..3.txt),
     en attendant que DeepSeek finisse de répondre après chacun ;
  3. envoyer les inputs (offre + CV original + LM originale) et attendre la réponse ;
  4. extraire les deux blocs LaTeX (CV + lettre) de la dernière réponse.

IMPORTANT : le contenu des 3 messages est PRIVÉ. Ce module lit les fichiers et les
envoie, mais ne les journalise jamais (les logs disent seulement « message 1/3 envoyé »).

Ce module ne fait AUCUNE vérification de contenu : les garde-fous (blocs protégés,
coordonnées, « 6 mois », 1 page…) sont appliqués ensuite par documents.py.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from navigateur import Navigateur
from tappi.core import CDPError
from gemini_chat import ChatEpuise
from confidentialite import courante as confidentialite
from utils import chemin_projet, reparer_guillemets

PLACEHOLDER = "COLLE ICI"   # texte des fichiers non encore remplis par l'utilisateur


class ErreurDeepSeek(Exception):
    """Le chat DeepSeek n'a pas pu être piloté (connexion, zone de saisie, réponse…)."""


# --- JavaScript : trouver la zone de saisie, envoyer, lire l'état --------------

# Écrit `txt` dans le champ de composition (textarea ou contenteditable) le plus bas/visible.
JS_ECRIRE = r"""
((txt) => {
  const visibles = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 120 && r.height > 8 && r.bottom <= innerHeight + 300; });
  if (!visibles.length) return 'no-input';
  const el = visibles[visibles.length - 1];      // la barre de saisie est en bas
  el.focus();
  if (el.tagName === 'TEXTAREA') {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(el, txt);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return 'textarea:' + el.value.length;
  }
  el.textContent = txt;
  el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: txt }));
  return 'editable:' + (el.textContent || '').length;
})
"""

# Clique le bouton d'envoi (icône flèche). Renvoie 'clicked' / 'disabled' / 'no-button'.
JS_ENVOYER = r"""
(() => {
  const composer = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 120 && r.bottom <= innerHeight + 300; }).pop();
  const zone = composer ? (composer.closest('form, div[class*="input"], div[class*="chat"], div') || document) : document;
  let cands = [...zone.querySelectorAll('button, [role="button"], div[role="button"]')];
  if (cands.length < 2) cands = [...document.querySelectorAll('button, [role="button"], div[role="button"]')];
  // bouton d'envoi : aria-label send/envoyer, ou type submit, ou dernier bouton près de la saisie
  let b = cands.find(x => /send|envoyer|发送/i.test((x.getAttribute('aria-label') || '') + (x.getAttribute('title') || '')));
  if (!b) b = cands.find(x => x.type === 'submit');
  if (!b) b = cands.reverse().find(x => { const r = x.getBoundingClientRect(); return r.width > 20 && r.width < 90 && r.height > 20 && r.height < 90 && r.bottom <= innerHeight + 10; });
  if (!b) return 'no-button';
  if (b.disabled || b.getAttribute('aria-disabled') === 'true') return 'disabled';
  b.click();
  return 'clicked';
})()
"""

# Longueur du texte de la ZONE DE CONVERSATION (<main>) : grandit pendant la génération,
# se stabilise à la fin. Bien plus fiable que document.body (barre latérale bruyante).
JS_LONGUEUR = "((document.querySelector('main') || document.body).innerText || '').length"

# Texte de la DERNIÈRE réponse du modèle (quel qu'en soit le contenu : JSON, LaTeX, prose).
# Sert au moteur de secours générique, quand Gemini est indisponible ou refuse.
JS_DERNIERE_REPONSE = r"""
(() => {
  const sel = '.ds-markdown, [class*="ds-markdown"], [class*="markdown"]';
  const els = [...document.querySelectorAll(sel)].filter(e => (e.innerText || '').trim());
  if (els.length) return JSON.stringify({n: els.length, texte: els[els.length - 1].innerText});
  // Repli : tout le fil de conversation (le découpage se fera côté Python)
  const main = document.querySelector('main') || document.body;
  return JSON.stringify({n: 0, texte: (main.innerText || '').slice(-6000)});
})()
"""

# Blocs LaTeX (<pre>) de la page contenant un document complet.
JS_BLOCS_LATEX = r"""
(() => {
  const blocs = [...document.querySelectorAll('pre')]
    .map(p => p.innerText || '')
    .filter(t => t.includes('\\documentclass') && t.includes('\\end{document}'));
  return JSON.stringify(blocs);
})()
"""

# Dernière réponse sous ses DEUX formes, pour les corrections LaTeX.
#
# Le markdown RENDU interprète les échappements : « \\ » s'y affiche « \ ». Lire `innerText`
# sur la bulle rendue divisait donc par deux TOUS les retours à la ligne LaTeX — le document
# revenait cassé (« Too many }'s ») alors que le modèle avait répondu juste. Le bloc <pre>,
# lui, est le texte BRUT tel qu'il a été écrit : c'est la seule source fidèle pour du code.
# On renvoie aussi le rendu, seul utile quand la réponse est en prose (pas de bloc de code).
JS_DERNIERE_REPONSE_BRUTE = r"""
(() => {
  // Bouton « arrêter la génération » visible = le modèle réfléchit ou écrit encore.
  const encours = [...document.querySelectorAll('button, [role="button"], [aria-label]')].some(b => {
    const t = ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '')).toLowerCase();
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && /stop|arrêter|arreter|停止|interrompre/.test(t);
  });
  const pres = [...document.querySelectorAll('pre')].map(p => p.innerText || '').filter(t => t.trim());
  const sel = '.ds-markdown, [class*="ds-markdown"], [class*="markdown"]';
  const els = [...document.querySelectorAll(sel)].filter(e => (e.innerText || '').trim());
  const rendu = els.length ? els[els.length - 1].innerText
              : (((document.querySelector('main') || document.body).innerText) || '').slice(-6000);
  return JSON.stringify({n: pres.length, pre: pres.length ? pres[pres.length - 1] : '',
                         rendu: rendu, encours: encours});
})()
"""


# TOUS les textes de réponse affichés (blocs <pre> et bulles rendues), pour le mode gabarit.
# DeepSeek virtualise son fil : les anciens messages sortent du DOM, donc compter les blocs
# ne dit rien. On compare par CONTENU : est nouveau ce qui n'était pas affiché avant l'envoi.
JS_TEXTES_REPONSES = r"""
(() => {
  const encours = [...document.querySelectorAll('button, [role="button"], [aria-label]')].some(b => {
    const t = ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '')).toLowerCase();
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && /stop|arrêter|arreter|停止|interrompre/.test(t);
  });
  const pres = [...document.querySelectorAll('pre')].map(p => p.innerText || '').filter(t => t.trim());
  const sel = '.ds-markdown, [class*="ds-markdown"]';
  const bulles = [...document.querySelectorAll(sel)].map(e => e.innerText || '').filter(t => t.trim());
  return JSON.stringify({textes: pres.concat(bulles), encours: encours});
})()
"""


def extraire_json(texte: str) -> dict | None:
    """Premier objet JSON complet d'un texte (bloc ```json ou JSON nu), sinon None."""
    t = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", texte.strip())
    debut, fin = t.find("{"), t.rfind("}")
    if debut < 0 or fin <= debut:
        return None
    try:
        d = json.loads(t[debut:fin + 1])
    except ValueError:
        try:   # guillemets du texte dont le Markdown du chat a mangé l'échappement
            d = json.loads(reparer_guillemets(t[debut:fin + 1]))
        except ValueError:
            return None
    return d if isinstance(d, dict) else None


class ChatDeepSeek:
    def __init__(self, config: dict, nav: Navigateur, logger: logging.Logger | None = None):
        self.nav = nav
        self.logger = logger or logging.getLogger("job_automation")
        cfg = config.get("deepseek_chat", {}) or {}
        self.url = cfg.get("url", "https://chat.deepseek.com/")
        self.dossier_messages = chemin_projet(cfg.get("dossier_messages", "config/deepseek"))
        self.attente_max = int(cfg.get("attente_max_secondes", 240))
        self.stabilite = float(cfg.get("stabilite_secondes", 6))
        self.attente_min = float(cfg.get("attente_min_secondes", 4))   # temps mini avant de conclure « fini »
        # DeepSeek MUET (serveur occupé, compte limité) : il ne commence même pas à répondre.
        # Constaté après ce délai au lieu d'attendre `attente_max` en entier -> nouvel essai,
        # puis bascule de navigateur en ~1 min au lieu de plus de 10.
        self.delai_demarrage = float(cfg.get("delai_demarrage_secondes", 30))
        # Indisponibilité de DeepSeek (« serveur occupé », « messages trop fréquents »…) :
        # comme pour le chat du formulaire, on ne se fie pas au TEXTE mais à la PERSISTANCE.
        # Après le dernier essai, ChatEpuise est levé et l'orchestrateur bascule sur le
        # navigateur suivant, déjà connecté avec un autre compte.
        self.reessais = max(1, int(cfg.get("reessais_indisponibilite", 3)))
        pauses = cfg.get("pauses_indisponibilite_secondes") or [20, 60, 120]
        self.pauses = [float(p) for p in pauses]
        # Réutiliser la MÊME conversation DeepSeek pour toutes les offres de la session :
        # les 3 messages privés ne sont envoyés qu'UNE fois (gain de ~1 min par offre).
        self.reutiliser = bool(cfg.get("reutiliser_conversation", True))
        self._onglet_ds: int | None = None      # index de l'onglet DeepSeek gardé ouvert
        # Identifiant CDP de CET onglet. Depuis que le formulaire est lui aussi rempli par un
        # chat DeepSeek, il y a DEUX onglets deepseek.com ouverts : se repérer sur l'URL
        # reviendrait à écrire dans la conversation de l'autre, voire à la refermer.
        self._id_onglet_ds: str = ""
        # Lien de CETTE conversation (le « lien 2 » : celle qui rédige le CV et la lettre).
        # C'est lui qui porte toute la mémoire du document ; on y revient au lieu d'ouvrir
        # une conversation de plus.
        self._url_conversation: str = ""
        self._messages_envoyes = False          # les 3 messages privés ont déjà été envoyés
        # Une conversation qui s'allonge sur des dizaines d'offres éloigne les 3 messages de
        # règles du début : DeepSeek les suit moins bien. On en ouvre une neuve toutes les N offres.
        self.offres_par_conversation = max(1, int(cfg.get("offres_par_conversation", 10)))
        self._offres_conversation = 0           # offres déjà traitées dans la conversation actuelle

    # -- messages privés -------------------------------------------------------

    def _lire_messages_prives(self) -> list[str]:
        """Lit les 3 messages privés. Erreur claire s'ils ne sont pas remplis (contenu non journalisé)."""
        messages = []
        for n in (1, 2, 3):
            fichier = self.dossier_messages / f"message_{n}.txt"
            if not fichier.exists():
                raise ErreurDeepSeek(f"Fichier manquant : {fichier}")
            contenu = fichier.read_text(encoding="utf-8").strip()
            if not contenu or PLACEHOLDER in contenu:
                raise ErreurDeepSeek(f"Le message {n} n'est pas encore rempli : colle ton texte dans {fichier}")
            messages.append(contenu)
        return messages

    # -- primitives navigateur -------------------------------------------------

    def _longueur(self) -> int:
        try:
            return int(self.nav.b.eval(JS_LONGUEUR) or 0)
        except CDPError:
            return 0

    def _longueur_saisie(self) -> int:
        """Longueur du texte actuellement dans la zone de saisie (0 = vide = message parti)."""
        js = r"""(() => { const el = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
                  .filter(e => { const r = e.getBoundingClientRect(); return r.width>120 && r.bottom<=innerHeight+300; }).pop();
                  return el ? (el.value != null ? el.value.length : (el.textContent||'').length) : -1; })()"""
        try:
            return int(self.nav.b.eval(js))
        except CDPError:
            return -1

    def _index_saisie(self) -> int:
        """Index Tappi de la zone de saisie DeepSeek (textarea)."""
        for e in self.nav.elements():
            if "textarea" in e.lower() or "textbox" in e.lower() or "Message à DeepSeek" in e:
                return int(e[1:e.index("]")])
        raise ErreurDeepSeek("Zone de saisie DeepSeek introuvable (connecté au chat ?).")

    def _envoyer_texte(self, texte: str) -> None:
        """
        Écrit le texte via paste() (fiable pour l'éditeur React de DeepSeek) puis l'envoie
        par la touche Entrée (repli : bouton d'envoi). Vérifie que la zone s'est vidée.
        """
        texte = confidentialite().masquer(texte)   # aucune donnée personnelle ne part (voir confidentialite.py)
        idx = self._index_saisie()
        self.nav.b.paste(idx, texte)        # paste() met à jour l'état React (vérifié)
        time.sleep(0.4)
        try:
            self.nav.b.keys("--enter")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)
        if self._longueur_saisie() <= 2:
            return   # zone vidée = message parti
        # repli : clic sur le bouton d'envoi
        clic = str(self.nav.b.eval(JS_ENVOYER))
        time.sleep(1.0)
        if self._longueur_saisie() <= 2:
            return
        raise ErreurDeepSeek(f"Le message n'est pas parti (Entrée sans effet, bouton: {clic}).")

    def _attendre_reponse(self) -> None:
        """
        Attend la fin d'une réponse : la longueur de <main> grandit pendant la génération
        (message + réponse en streaming), puis se fige. On conclut « fini » quand elle n'a
        pas bougé pendant `stabilite` s, et après un délai minimum (le temps que la réponse
        démarre). Robuste aux réponses courtes comme longues.
        """
        debut = time.monotonic()
        derniere_valeur = self._longueur()
        derniere_variation = time.monotonic()
        while time.monotonic() - debut < self.attente_max:
            time.sleep(1.0)
            actuelle = self._longueur()
            if actuelle != derniere_valeur:
                derniere_valeur = actuelle
                derniere_variation = time.monotonic()
            assez_attendu = (time.monotonic() - debut) >= self.attente_min
            fige = (time.monotonic() - derniere_variation) >= self.stabilite
            if assez_attendu and fige:
                return
        self.logger.warning("DeepSeek : délai max atteint (réponse peut-être incomplète).")

    # -- extraction ------------------------------------------------------------

    @staticmethod
    def _classer(blocs: list[str]) -> tuple[str, str]:
        """Range les blocs LaTeX en (cv, lm). CV = extarticle/section Profil ; LM = lettre/tikz."""
        cv = lm = ""
        for b in blocs:
            est_lm = ("Lettre de motivation" in b or "\\begin{tikzpicture}" in b
                      or ("{article}" in b and "\\section" not in b))
            est_cv = ("extarticle" in b or "\\section*{Profil}" in b or "Compétences" in b)
            if est_cv and not cv:
                cv = b
            elif est_lm and not lm:
                lm = b
        # Repli : 2 blocs non classés -> ordre d'apparition (CV puis LM)
        if (not cv or not lm) and len(blocs) >= 2:
            cv = cv or blocs[0]
            lm = lm or blocs[1]
        return cv.strip(), lm.strip()

    def _extraire_latex(self, nb_derniers: int = 4) -> tuple[str, str]:
        """Extrait (cv_tex, lm_tex) de la dernière réponse : blocs <pre>, sinon regex sur le texte.
        `nb_derniers` = 2 quand la conversation est réutilisée (les anciens documents restent
        affichés au-dessus : seuls les 2 derniers blocs sont ceux de l'offre en cours)."""
        blocs: list[str] = []
        try:
            brut = self.nav.b.eval(JS_BLOCS_LATEX)
            blocs = json.loads(brut) if isinstance(brut, str) else []
        except (CDPError, ValueError):
            blocs = []
        if len(blocs) < 2:   # repli : chercher les documents dans tout le texte visible
            texte = str(self.nav.b.eval("document.body ? document.body.innerText : ''") or "")
            blocs = re.findall(r"\\documentclass.*?\\end\{document\}", texte, re.DOTALL)
        # on garde les derniers documents complets (la dernière réponse)
        blocs = [b for b in blocs if "\\documentclass" in b and "\\end{document}" in b][-nb_derniers:]
        cv, lm = self._classer(blocs)
        if not cv or not lm:
            raise ErreurDeepSeek(f"CV/LM LaTeX introuvables dans la réponse DeepSeek "
                                 f"({len(blocs)} bloc(s) LaTeX détecté(s)).")
        return cv, lm

    # -- API publique ----------------------------------------------------------

    def _avec_patience(self, operation, quoi: str):
        """
        Répète `operation` en espaçant les essais tant que DeepSeek reste indisponible.

        Même principe que pour le chat du formulaire : le déclencheur n'est pas le TEXTE du
        message — « serveur occupé », « messages trop fréquents », et ceux qu'on ne connaît
        pas — mais sa PERSISTANCE. Une saturation passagère ne coûte alors qu'une attente.

        Après le dernier essai, on lève ChatEpuise plutôt qu'une erreur de génération : la
        limite frappe le COMPTE, donc ce navigateur entier. L'orchestrateur bascule alors sur
        le suivant et rejoue la candidature — ce que « Génération du CV/lettre impossible »
        ne déclenchait pas, et c'est ainsi que des candidatures étaient perdues.
        """
        derniere: Exception | None = None
        for essai in range(1, self.reessais + 1):
            try:
                # Réponse : repères ({PRENOM}, {EMAIL}…) remis en vraies valeurs avant tout usage.
                return confidentialite().demasquer_tout(operation())
            except ErreurDeepSeek as e:
                derniere = e
                if essai >= self.reessais:
                    break
                pause = self.pauses[min(essai, len(self.pauses)) - 1]
                self.logger.warning(f"DeepSeek (documents) : {e}")
                self.logger.warning(f"  {quoi} — nouvel essai dans {pause:.0f} s "
                                    f"({essai}/{self.reessais}).")
                time.sleep(pause)
        self.logger.error(f"DeepSeek (documents) : {quoi} impossible après {self.reessais} "
                          f"essais espacés — compte probablement saturé.")
        raise ChatEpuise(f"DeepSeek (documents) indisponible après {self.reessais} essais "
                         f"espacés ({quoi})") from derniere

    def generer(self, bloc_inputs: str) -> tuple[str, str]:
        """Rédige le CV et la lettre, en réessayant si DeepSeek est momentanément indisponible."""
        return self._avec_patience(lambda: self._generer_une_fois(bloc_inputs),
                                   "rédaction du CV et de la lettre")

    def demander(self, message: str, attendre_document: bool = False, ouvrir: bool = False) -> str:
        """Pose une question dans la conversation des documents, avec la même patience.
        ouvrir : si la conversation n'existe pas (encore), l'ouvrir au lieu d'échouer."""
        return self._avec_patience(
            lambda: self._demander_une_fois(message, attendre_document, ouvrir), "réécriture LaTeX")

    # -- mode gabarit : réponses JSON -------------------------------------------

    def generer_json(self, message: str, valide) -> dict:
        """
        Mode gabarit : prépare la conversation (messages privés à la 1re offre), envoie
        `message` et renvoie le JSON reçu. Rien reçu -> le MÊME message est renvoyé (patience) ;
        le robot ne clique jamais sur « Continuer ».
        """
        return self._avec_patience(lambda: self._json_une_fois(message, valide, preparer=True),
                                   "rédaction du CV et de la lettre (gabarit)")

    def demander_json(self, message: str, valide, ouvrir: bool = False) -> dict:
        """Correction dans la même conversation : renvoie le JSON des seules clés ajustées.
        ouvrir : si la conversation n'existe pas (CV réutilisé, robot relancé), l'ouvrir."""
        return self._avec_patience(
            lambda: self._json_une_fois(message, valide, preparer=False, ouvrir=ouvrir),
            "ajustement du CV (gabarit)")

    def _textes_reponses(self) -> dict:
        try:
            brut = self.nav.b.eval(JS_TEXTES_REPONSES)
            etat = json.loads(brut) if isinstance(brut, str) else {}
        except (CDPError, ValueError):
            return {}
        return etat if isinstance(etat, dict) else {}

    def _json_une_fois(self, message: str, valide, preparer: bool, ouvrir: bool = False) -> dict:
        message = confidentialite().masquer(message)
        onglet_offre = self.nav.epingler(None)
        try:
            if preparer:
                self._preparer_conversation()
            elif not self._activer_onglet_deepseek():
                if not ouvrir:
                    raise ErreurDeepSeek("La conversation des documents n'est plus joignable.")
                self._preparer_conversation()   # message complet : il se suffit à lui-même
            deja_vus = set(self._textes_reponses().get("textes") or [])
            self.logger.info("DeepSeek : envoi de l'offre (gabarit)…" if preparer
                             else "DeepSeek : envoi des ajustements…")
            self._envoyer_texte(message)
            fin = time.monotonic() + self.attente_max
            dernier, depuis = None, time.monotonic()
            debut, demarre = time.monotonic(), False
            while time.monotonic() < fin:
                time.sleep(1.0)
                etat = self._textes_reponses()
                if etat:
                    nouveaux = [t for t in (etat.get("textes") or [])
                                if t not in deja_vus and t.strip() not in message]
                    demarre = demarre or etat.get("encours") is True or bool(nouveaux)
                    if not demarre and time.monotonic() - debut >= self.delai_demarrage:
                        raise ErreurDeepSeek(f"DeepSeek n'a pas commencé à répondre en "
                                             f"{self.delai_demarrage:.0f} s (serveur occupé ou compte limité)")
                trouve = None
                for t in reversed(etat.get("textes") or []):
                    if t in deja_vus or t.strip() in message:
                        continue
                    d = extraire_json(t)
                    if d and valide(d):
                        trouve = d
                        break
                if trouve != dernier:
                    dernier, depuis = trouve, time.monotonic()
                # JSON complet, et génération finie (ou JSON inchangé depuis `stabilite` s).
                if dernier and (not etat.get("encours", True)
                                or time.monotonic() - depuis >= self.stabilite):
                    self._memoriser_conversation()
                    if preparer:
                        self._offres_conversation += 1   # une offre de plus dans cette conversation
                    return dernier
            raise ErreurDeepSeek(f"aucun JSON reçu en {self.attente_max} s")
        finally:
            self.nav.epingler(onglet_offre)

    def _preparer_conversation(self) -> None:
        """Ouvre (ou réutilise) la conversation des documents et y envoie les messages privés."""
        messages = self._lire_messages_prives()
        pleine = self._offres_conversation >= self.offres_par_conversation
        if pleine:
            self.logger.info(f"DeepSeek : {self._offres_conversation} offres dans cette conversation -> "
                             f"nouvelle conversation (tes 3 messages de règles renvoyés).")
            self._fermer_onglet_deepseek()
        if self.reutiliser and not pleine and self._activer_onglet_deepseek():
            self.logger.info("DeepSeek : conversation existante réutilisée (messages privés déjà envoyés).")
        else:
            self._messages_envoyes = False
            self._offres_conversation = 0
            nouvel_id = self.nav.ouvrir_onglet_assistant(self.url)
            self.nav.b.wait(3500)
            self.nav.epingler(nouvel_id or None)
            host = str(self.nav.b.eval("location.host") or "")
            if "deepseek" not in host:
                raise ErreurDeepSeek(f"L'onglet DeepSeek ne s'est pas ouvert (host={host}).")
            self._id_onglet_ds = nouvel_id or ""
            self._url_conversation = ""
            self._onglet_ds = self._index_onglet_courant()
        if not (self.reutiliser and self._messages_envoyes):
            for i, texte in enumerate(messages, start=1):
                self.logger.info(f"DeepSeek : envoi du message privé {i}/3…")
                self._envoyer_texte(texte)
                self._attendre_reponse()
            self._messages_envoyes = True
            self._memoriser_conversation()

    def _generer_une_fois(self, bloc_inputs: str) -> tuple[str, str]:
        """
        Déroulé complet : ouvre un nouveau chat, envoie les 3 messages privés puis
        `bloc_inputs` (offre + CV + LM originaux), et renvoie (cv_tex, lm_tex).
        Avec reutiliser_conversation, la même conversation est gardée ouverte : les
        3 messages privés ne sont renvoyés qu'à la première offre de la session.
        """
        bloc_inputs = confidentialite().masquer(bloc_inputs)
        messages = self._lire_messages_prives()   # vérifie qu'ils sont remplis AVANT d'ouvrir le chat
        # L'onglet de l'offre est ÉPINGLÉ (voir Navigateur.epingler) : sans épingler le nôtre,
        # toutes les commandes ci-dessous viseraient la page de l'offre au lieu de DeepSeek.
        # On rend l'épinglage à l'offre en sortant, quoi qu'il arrive.
        onglet_offre = self.nav.epingler(None)
        conversation_reutilisee = False
        try:
            if self.reutiliser and self._activer_onglet_deepseek():
                conversation_reutilisee = True
                self.logger.info("DeepSeek : conversation existante réutilisée (messages privés déjà envoyés).")
            else:
                self._messages_envoyes = False
                # Nouvel onglet dans la même fenêtre.
                nouvel_id = self.nav.ouvrir_onglet_assistant(self.url)
                self.nav.b.wait(3500)
                self.nav.epingler(nouvel_id or None)
                host = str(self.nav.b.eval("location.host") or "")
                if "deepseek" not in host:
                    raise ErreurDeepSeek(f"L'onglet DeepSeek ne s'est pas ouvert (host={host}).")
                self._id_onglet_ds = nouvel_id or ""
                self._url_conversation = ""      # la conversation précédente n'existe plus
                self._onglet_ds = self._index_onglet_courant()
        except Exception:
            self.nav.epingler(onglet_offre)
            raise
        try:
            if not (self.reutiliser and self._messages_envoyes):
                for i, message in enumerate(messages, start=1):
                    self.logger.info(f"DeepSeek : envoi du message privé {i}/3…")
                    self._envoyer_texte(message)
                    self._attendre_reponse()
                self._messages_envoyes = True

            self.logger.info("DeepSeek : envoi des inputs (offre + CV + LM)…")
            self._envoyer_texte(bloc_inputs)
            self._attendre_reponse()

            self._memoriser_conversation()
            cv, lm = self._extraire_latex(nb_derniers=2 if conversation_reutilisee else 4)
            self.logger.info(f"DeepSeek : CV ({len(cv)} car.) et LM ({len(lm)} car.) extraits.")
            return cv, lm
        finally:
            if not self.reutiliser:
                self._fermer_onglet_deepseek()   # sinon l'onglet reste ouvert pour l'offre suivante
            # Rendre l'épinglage à l'offre suffit : c'est lui, et non l'onglet « au premier
            # plan », qui détermine la page sur laquelle le robot lit et clique.
            self.nav.epingler(onglet_offre)

    def _memoriser_conversation(self) -> None:
        """Retient le lien de CETTE conversation : DeepSeek crée .../a/chat/s/<id> au 1er message."""
        try:
            url = str(self.nav.b.url() or "")
        except Exception:  # noqa: BLE001
            return
        if "deepseek" in url and "/chat/s/" in url:
            self._url_conversation = url

    def _demander_une_fois(self, message: str, attendre_document: bool = False,
                           ouvrir: bool = False) -> str:
        """
        Pose UNE question DANS la conversation qui a rédigé les documents, et renvoie la réponse.

        C'est par ici que passent les corrections LaTeX (erreur de compilation, réduction à
        une page) : le modèle y a déjà l'offre, le CV et la lettre, il n'y a donc rien à
        retransmettre — et surtout, on n'ouvre pas une TROISIÈME conversation DeepSeek.

        `attendre_document` : on SAIT qu'on attend un document LaTeX (c'est toujours le cas
        pour une réécriture). On n'essaie alors plus de deviner quand la réponse est finie —
        deviner faisait rendre la main PENDANT la phase de réflexion, et la candidature était
        perdue sur « n'a pas renvoyé un document LaTeX complet » alors que le modèle écrivait
        encore. On attend le document, point.
        """
        message = confidentialite().masquer(message)
        onglet_offre = self.nav.epingler(None)
        try:
            if not self._activer_onglet_deepseek():
                if not ouvrir:
                    raise ErreurDeepSeek("La conversation des documents n'est plus joignable.")
                self._preparer_conversation()   # question ouverte : le message est complet
            # Blocs de code DÉJÀ affichés (documents des offres précédentes, conversation
            # réutilisée) : la réponse attendue sera un bloc EN PLUS de ceux-là.
            avant = self._etat_reponse()
            nb_blocs = int(avant.get("n") or 0)
            self._envoyer_texte(message)
            # Document LaTeX : attente prudente (voir _attendre_document_complet). Réponse en
            # prose (question technique) : _attendre_document_complet sait seul quand elle est
            # finie — l'attente de stabilité ajoutait ~4 s pour rien.
            if attendre_document:
                self._attendre_reponse()
            self._memoriser_conversation()
            texte = self._attendre_document_complet(nb_blocs, message, attendre_document,
                                                    rendu_avant=str(avant.get("rendu") or ""))
            if not texte.strip():
                raise ErreurDeepSeek("Réponse vide de la conversation des documents.")
            return texte.strip()
        finally:
            self.nav.epingler(onglet_offre)

    def _etat_reponse(self) -> dict:
        """{n: nombre de blocs <pre>, pre: dernier bloc BRUT, rendu: dernière bulle rendue}."""
        try:
            brut = self.nav.b.eval(JS_DERNIERE_REPONSE_BRUTE)
            etat = json.loads(brut) if isinstance(brut, str) else {}
        except (CDPError, ValueError) as e:
            self.logger.debug(f"DeepSeek (documents) : état de la réponse illisible ({e})")
            return {}
        return etat if isinstance(etat, dict) else {}

    # Un texte parfaitement immobile aussi longtemps est terminé, même si le détecteur
    # « génération en cours » se trompe. Bien plus long que `stabilite`, qui mesure une
    # pause d'écriture : ici on veut être sûr, pas rapide.
    FIGE_SUR = 45.0

    def _attendre_document_complet(self, blocs_avant: int, envoye: str,
                                   exiger_document: bool = False, rendu_avant: str = "") -> str:
        """
        Attend un document LaTeX COMPLET dans un bloc <pre> nouvellement apparu, et le renvoie
        BRUT (voir JS_DERNIERE_REPONSE_BRUTE : le rendu markdown, lui, mange les « \\\\ »).

        `_attendre_reponse` ne suffit pas ici : pendant la phase de RÉFLEXION de DeepSeek la
        page ne bouge pas, elle est donc jugée « figée » au bout de `stabilite` secondes et on
        lisait une réponse encore vide — d'où un « document LaTeX incomplet » et la candidature
        perdue alors que la bonne réponse arrivait quelques secondes plus tard. Un document
        complet, lui, se reconnaît à coup sûr : on l'attend au lieu de deviner.

        `exiger_document` : on attend un document et RIEN d'autre. Aucune sortie anticipée sur
        de la prose — c'est ce qui avait fait perdre trois candidatures d'affilée, le détecteur
        « génération en cours » ne reconnaissant pas toujours le bouton d'arrêt de DeepSeek.
        Mieux vaut attendre le délai max une fois que rendre une réponse à moitié écrite.

        Deux garde-fous : le bloc doit être NOUVEAU (sinon on relirait le document de l'offre
        précédente), et ne pas être l'écho du document qu'on vient d'envoyer.
        """
        fin = time.monotonic() + self.attente_max
        dernier_rendu, variation = "", time.monotonic()
        vu_encours = False   # le bouton « stop » a-t-il été vu pendant CETTE réponse ?
        while time.monotonic() < fin:
            etat = self._etat_reponse()
            pre = str(etat.get("pre") or "")
            # Le cas nominal : un bloc de code neuf contenant un document complet.
            if (int(etat.get("n") or 0) > blocs_avant and "\\documentclass" in pre
                    and "\\end{document}" in pre and pre.strip() not in envoye):
                return pre
            rendu = str(etat.get("rendu") or "")
            if rendu != dernier_rendu:
                dernier_rendu, variation = rendu, time.monotonic()
            fige = time.monotonic() - variation
            # Tant que le bouton « arrêter la génération » est visible, le modèle réfléchit ou
            # écrit : on ne conclut RIEN. Clé absente (page illisible) = on suppose « en cours »,
            # jamais l'inverse. Mais ce détecteur n'est pas fiable sur toutes les versions de
            # l'interface : il ne sert donc qu'à conclure PLUS TÔT, jamais à conclure tout court.
            encours = bool(etat.get("encours", True))
            vu_encours = vu_encours or encours
            fini = not encours
            # Document écrit hors bloc de code : accepté en dernier recours seulement, une fois
            # la génération finie ET le texte stabilisé (ses « \\ » sont abîmés, voir plus haut).
            if ("\\documentclass" in rendu and "\\end{document}" in rendu
                    and ((fini and fige >= self.stabilite) or fige >= self.FIGE_SUR)):
                self.logger.warning("DeepSeek (documents) : document reçu hors bloc de code "
                                    "-> retours à la ligne LaTeX peut-être abîmés.")
                return rendu
            # Réponse NOUVELLE : texte différent de la bulle d'avant l'envoi, OU génération vue
            # en cours puis terminée. Sans ce second cas, une réponse identique à la précédente
            # (« Moins d'un an » deux fois de suite) n'était jamais reconnue : 5 min d'attente.
            if (not exiger_document and rendu.strip() and "\\documentclass" not in rendu
                    and (rendu != rendu_avant or (vu_encours and fini))):
                # Question ordinaire (prose) : rendue DÈS que le modèle a fini — bouton « stop »
                # vu puis disparu. Sans ce signal (détecteur en défaut), texte stable requis.
                # `rendu_avant` : la bulle d'avant l'envoi n'est jamais prise pour la réponse.
                if (fini and (vu_encours or fige >= self.stabilite)) or fige >= self.FIGE_SUR:
                    return rendu
            time.sleep(0.5)
        etat = self._etat_reponse()
        pre = str(etat.get("pre") or "")
        self.logger.warning("DeepSeek (documents) : aucun document complet reçu avant le délai "
                            f"max ({self.attente_max} s).")
        # Même exigence qu'au-dessus : le bloc doit être NOUVEAU. Sans ce test, un délai
        # dépassé renvoyait le dernier bloc affiché — c'est-à-dire le document d'une offre
        # PRÉCÉDENTE, qui serait alors parti dans cette candidature-ci.
        if (int(etat.get("n") or 0) > blocs_avant and "\\documentclass" in pre
                and pre.strip() not in envoye):
            return pre
        return str(etat.get("rendu") or "")

    def _index_onglet_courant(self) -> int | None:
        """Index Tappi de NOTRE onglet DeepSeek (celui des documents), repéré par son id."""
        try:
            for t in self.nav.b.tabs():
                if getattr(t, "id", "") == self._id_onglet_ds:
                    return t.index
        except Exception:  # noqa: BLE001
            pass
        return None

    def _activer_onglet_deepseek(self) -> bool:
        """Re-sélectionne l'onglet DeepSeek gardé ouvert. Renvoie False s'il n'existe plus."""
        if self._onglet_ds is None:
            return False
        try:
            for t in self.nav.b.tabs():
                if getattr(t, "id", "") == self._id_onglet_ds:
                    self._onglet_ds = t.index      # l'index a pu bouger entre-temps
                    self.nav.b.tab(t.index)
                    self.nav.epingler(self._id_onglet_ds)
                    time.sleep(0.8)
                    return True
        except Exception:  # noqa: BLE001
            pass
        # L'onglet a disparu mais la conversation, elle, existe toujours : on rouvre SON
        # LIEN. Repartir d'un chat vierge perdrait le CV, la lettre et les messages privés.
        if self._url_conversation:
            try:
                self._id_onglet_ds = self.nav.ouvrir_onglet_assistant(self._url_conversation)
                self.nav.b.wait(3500)
                if self._id_onglet_ds:
                    self.nav.epingler(self._id_onglet_ds)
                    self._onglet_ds = self._index_onglet_courant()
                    self.logger.info("DeepSeek (documents) : onglet perdu -> conversation "
                                     "rouverte sur son lien enregistré.")
                    return True
            except Exception:  # noqa: BLE001
                pass
        self._onglet_ds = None
        self._id_onglet_ds = ""
        self._messages_envoyes = False
        return False

    def _fermer_onglet_deepseek(self) -> None:
        """
        Ferme NOTRE onglet DeepSeek (celui des documents) pour rendre la main à l'offre.
        Repéré par identifiant : l'onglet DeepSeek du FORMULAIRE ne doit pas y passer.
        """
        try:
            for t in self.nav.b.tabs():
                if getattr(t, "id", "") == self._id_onglet_ds:
                    self.nav.b.close_tab(t.index)
                    break
        except Exception:  # noqa: BLE001
            pass
        self._id_onglet_ds = ""


class ClientDeepSeekDocuments:
    """
    Moteur de réécriture LaTeX qui travaille DANS la conversation des documents (« lien 2 »),
    celle qui a justement rédigé le CV et la lettre.

    Même interface que ClientLLM.generer(...), mais le prompt système et le document ne sont
    PAS retransmis : la conversation les connaît déjà. Remplace ClientDeepSeekTexte, qui
    ouvrait une troisième conversation DeepSeek à chaque correction.
    """

    def __init__(self, chat: "ChatDeepSeek", logger: logging.Logger | None = None):
        self._chat = chat
        self.logger = logger or chat.logger

    def generer(self, systeme: str, message: str, images: list[bytes] | None = None,
                tache: str | None = None) -> str:
        if images:
            self.logger.debug("DeepSeek (documents) : les captures ne sont pas transmises.")
        # `systeme` est volontairement ignoré (la conversation connaît déjà sa consigne) — donc
        # tout ce qui doit atteindre le modèle par ici doit être DANS le message.
        # Réécriture LaTeX : on attend un document complet, jamais autre chose.
        # Question TECHNIQUE du formulaire : la réponse est de la PROSE. Exiger un document
        # faisait attendre le délai max (240 s) une réponse pourtant affichée depuis longtemps.
        return self._chat.demander(message, attendre_document=(tache != "question_technique"),
                                   ouvrir=(tache == "question_technique"))

    def fermer(self) -> None:
        """La conversation des documents est gérée ailleurs : rien à fermer ici."""


class ClientDeepSeekTexte:
    """
    Moteur de SECOURS générique basé sur le chat DeepSeek, avec la même interface que
    ClientLLM.generer(...). Il prend le relais quand Gemini refuse ou renvoie une réponse
    inexploitable — typiquement pour réécrire du LaTeX, tâche où DeepSeek est meilleur
    puisque c'est lui qui a rédigé le document au départ.

    Il travaille dans SA PROPRE conversation, distincte de celle qui génère le CV : on ne
    veut pas polluer le fil qui contient les messages privés et les documents.

    Les images ne sont pas transmises (le pilote DeepSeek n'envoie que du texte).
    """

    def __init__(self, config: dict, nav: Navigateur, logger: logging.Logger | None = None):
        self._chat = ChatDeepSeek(config, nav, logger)
        self._chat.reutiliser = False          # conversation dédiée, jamais celle des documents
        self.nav = nav
        self.logger = logger or logging.getLogger("job_automation")
        self._url_retour = ""
        self._id_onglet = ""      # onglet ouvert par CE moteur, le seul qu'il a le droit de fermer

    def _reponse_courante(self) -> dict:
        try:
            brut = self.nav.b.eval(JS_DERNIERE_REPONSE)
            return json.loads(brut) if isinstance(brut, str) else {"n": 0, "texte": ""}
        except (CDPError, ValueError):
            return {"n": 0, "texte": ""}

    def generer(self, systeme: str, message: str, images: list[bytes] | None = None,
                tache: str | None = None) -> str:
        """Une question = une conversation neuve, fermée juste après."""
        try:
            self._url_retour = str(self.nav.b.url() or "")
        except Exception:  # noqa: BLE001
            self._url_retour = ""
        if images:
            self.logger.debug("DeepSeek (secours) : les captures ne sont pas transmises.")
        # L'onglet de l'offre est épinglé : sans épingler le nôtre, toutes les commandes
        # ci-dessous viseraient la page de l'offre — on ne se reconnaîtrait pas et on
        # abandonnerait aussitôt (« L'onglet DeepSeek de secours ne s'est pas ouvert »).
        self._id_onglet = self.nav.ouvrir_onglet_assistant(self._chat.url)
        self.nav.b.wait(3500)
        onglet_offre = self.nav.epingler(self._id_onglet or None)
        try:
            if "deepseek" not in str(self.nav.b.eval("location.host") or ""):
                raise ErreurDeepSeek("L'onglet DeepSeek de secours ne s'est pas ouvert.")
            avant = self._reponse_courante()["n"]
            self._chat._envoyer_texte(f"{systeme}\n\n---\n\n{message}")
            self._chat._attendre_reponse()
            apres = self._reponse_courante()
            if apres["n"] and apres["n"] <= avant:
                raise ErreurDeepSeek("DeepSeek (secours) n'a produit aucune réponse nouvelle.")
            texte = str(apres.get("texte") or "").strip()
            if not texte:
                raise ErreurDeepSeek("DeepSeek (secours) : réponse vide.")
            return texte
        finally:
            self.fermer()
            self.nav.epingler(onglet_offre)

    def fermer(self) -> None:
        """Referme UNIQUEMENT la conversation de secours, puis rend la main à l'onglet d'origine."""
        for _ in range(3):
            if not self._id_onglet:
                break
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
        if not self._url_retour:
            return
        try:
            for t in self.nav.b.tabs():
                if (t.url or "") == self._url_retour:
                    self.nav.b.tab(t.index)
                    return
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    # Test manuel : lit une offre + le CV/LM originaux, pilote DeepSeek, sauvegarde les .tex.
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from utils import charger_config, configurer_logs

    parser = argparse.ArgumentParser(description="Test du pilotage du chat DeepSeek")
    parser.add_argument("--offre", required=True, help="fichier texte contenant l'offre (entreprise + description)")
    parser.add_argument("--sortie", default="output/deepseek_test", help="dossier où écrire cv.tex et lm.tex")
    args = parser.parse_args()

    config, _ = charger_config()
    logger = configurer_logs(chemin_projet(config["chemins"]["logs"]))
    nav = Navigateur(config, logger)
    nav.demarrer()

    cv_orig = chemin_projet(config["chemins"]["cv_original"]).read_text(encoding="utf-8")
    lm_orig = chemin_projet(config["chemins"]["lm_original"]).read_text(encoding="utf-8")
    offre = Path(args.offre).read_text(encoding="utf-8")
    bloc = (f"=== OFFRE ===\n{offre}\n\n"
            f"=== CV ORIGINAL (LaTeX) ===\n{cv_orig}\n\n"
            f"=== LETTRE DE MOTIVATION ORIGINALE (LaTeX) ===\n{lm_orig}\n")

    chat = ChatDeepSeek(config, nav, logger)
    cv, lm = chat.generer(bloc)
    dossier = chemin_projet(args.sortie); dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "cv.tex").write_text(cv, encoding="utf-8")
    (dossier / "lm.tex").write_text(lm, encoding="utf-8")
    print(f"OK — CV: {len(cv)} car., LM: {len(lm)} car. -> {dossier}")
