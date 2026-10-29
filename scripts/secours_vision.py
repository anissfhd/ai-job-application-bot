"""
SECOURS VISION — un second outil, utilisé UNIQUEMENT quand le robot est bloqué.

Le robot normal voit la page à travers une LISTE d'éléments numérotés. Quand cette liste est
trompeuse (élément absent, doublon, composant maison…), il reste bloqué. Le secours, lui,
regarde la CAPTURE D'ÉCRAN avec un modèle qui voit vraiment l'image (Claude ou GPT, par API)
et clique aux COORDONNÉES, comme un humain avec une souris : il ne dépend plus de la liste.

Garanties (voir aussi candidature.py, où il est appelé) :
- il ne se déclenche que juste avant un abandon (blocage, « manuel », « échec ») ;
- il débloque l'étape en cours puis RENDS LA MAIN au robot normal, qui reprend sa logique ;
- il ne clique JAMAIS l'envoi final, ni un bouton d'upload, ni une case d'alerte / newsletter,
  et ne saisit jamais dans un champ mot de passe ;
- sans clé API, ou si l'API échoue (pas de crédit, clé invalide, réseau), il s'écarte en
  silence pour toute la session : le robot fait exactement ce qu'il faisait avant.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.request

# Boutons que le secours ne clique JAMAIS : l'envoi final reste au robot normal (verrous CV /
# lettre, mode test, détection de la confirmation).
ENVOI_FINAL = re.compile(
    r"submit|envoyer|soumettre|send application|postuler|apply now|valider (ma|la) candidature|"
    r"confirmer (ma|la) candidature|finish|terminer",
    re.IGNORECASE)
BOUTON_FICHIER = re.compile(r"upload|téléverser|télécharger|parcourir|browse|importer|joindre|attach",
                            re.IGNORECASE)
CASE_INTERDITE = re.compile(r"alert|newsletter|abonne|subscribe|suivre|follow|top choice|premium|"
                            r"recevoir|receive|notification|partenaires|partners|promot", re.IGNORECASE)

SYSTEME = """Tu es un assistant qui DÉBLOQUE une étape d'un formulaire de candidature en ligne.
Un robot remplit ce formulaire mais il est bloqué ; tu vois la capture d'écran actuelle.
Tu agis comme un humain avec une souris : tu donnes des COORDONNÉES en pixels de l'image.

Réponds UNIQUEMENT par un objet JSON, une seule action à la fois :
{"action": "cliquer", "x": 512, "y": 300, "pourquoi": "cocher l'option Non"}
{"action": "taper", "x": 512, "y": 300, "texte": "...", "pourquoi": "..."}   (clique le champ puis écrit)
{"action": "defiler", "x": 512, "y": 300, "sens": "bas"}                     (ou "haut")
{"action": "fini", "resume": "ce qui a été débloqué"}
{"action": "impossible", "raison": "..."}

Règles :
- Ton but : corriger ce qui bloque l'étape visible (option à cocher, champ obligatoire vide ou en
  erreur, liste à choisir…), puis passer à l'étape SUIVANTE (« Suivant / Next / Continue / Review »).
- Réponds « fini » dès que l'étape suivante s'affiche, ou dès que la page de revue finale est là.
- Ne clique JAMAIS l'envoi final (« Submit application », « Envoyer », « Postuler ») : le robot s'en
  charge. Ne clique jamais un bouton d'upload de fichier. Ne coche jamais une case d'alerte, de
  newsletter, d'abonnement, de suivi ou de mise en avant (« top choice »).
- N'écris jamais de mot de passe ni de donnée bancaire.
- Réponds d'après le PROFIL fourni ; n'invente rien. Si une question n'a pas de réponse dans le
  profil, choisis l'option la plus neutre.
- Vise le CENTRE de l'élément (la pastille d'un bouton radio, la case, le bouton)."""


class SecoursVision:
    def __init__(self, config: dict, nav, logger: logging.Logger | None = None):
        self.nav = nav
        self.logger = logger or logging.getLogger("job_automation")
        cfg = config.get("secours_vision") or {}
        # Fournisseurs essayés DANS L'ORDRE ; seuls ceux qui ont une clé comptent. Un
        # fournisseur qui refuse (clé invalide, crédits épuisés) est écarté pour la session et
        # le suivant prend le relais. Ancien format (fournisseur / modele / cle_api) accepté.
        liste = cfg.get("fournisseurs") or [{"nom": cfg.get("fournisseur", "claude"),
                                             "modele": cfg.get("modele"), "cle_api": cfg.get("cle_api")}]
        modele_openai = ((config.get("llm") or {}).get("openai") or {}).get("modele", "gpt-4o")
        defauts = {"claude": "claude-sonnet-5", "openai": modele_openai,
                   "nvidia": "moonshotai/kimi-k3"}
        self.fournisseurs = []
        for f in liste:
            nom = str((f or {}).get("nom", "")).lower()
            cle = str((f or {}).get("cle_api") or "").strip()
            if nom in defauts and cle:
                self.fournisseurs.append({"nom": nom, "cle": cle,
                                          "modele": str(f.get("modele") or defauts[nom])})
        self.max_actions = int(cfg.get("max_actions", 8))
        self.max_par_candidature = int(cfg.get("max_secours_par_candidature", 2))
        self.largeur_max = int(cfg.get("largeur_capture", 1280))
        # Un modèle qui ne répond pas dans ce délai est mis de côté `pause_lent_minutes` : on
        # n'attend plus des minutes à chaque blocage (fournisseur gratuit saturé).
        self.delai_reponse = float(cfg.get("delai_reponse_secondes", 60))
        self.pause_lent = float(cfg.get("pause_lent_minutes", 30)) * 60
        # Hors service pour la session : désactivé, aucune clé, ou tous les fournisseurs écartés.
        self.hors_service = (not bool(cfg.get("actif", True))) or not self.fournisseurs
        self._raison_hors_service = ("désactivé dans la config" if not cfg.get("actif", True)
                                     else "aucune clé API (secours_vision)")
        self._signale = False

    # -- disponibilité ---------------------------------------------------------------
    def disponible(self) -> bool:
        if not self.hors_service and self.fournisseurs and all(
                f.get("pause_jusqua", 0) > time.monotonic() for f in self.fournisseurs):
            return False             # tous mis de côté (trop lents) pour l'instant
        if self.hors_service and not self._signale:
            self._signale = True
            self.logger.info(f"  Secours vision non utilisé : {self._raison_hors_service} "
                             f"-> comportement habituel du robot.")
        return not self.hors_service

    def _mettre_hors_service(self, raison: str) -> None:
        self.hors_service = True
        self._raison_hors_service = raison
        self._signale = True
        self.logger.warning(f"  Secours vision indisponible ({raison}) -> désactivé pour cette "
                            f"session, le robot continue normalement.")

    # -- capture et coordonnées ----------------------------------------------------
    def _capture(self) -> tuple[bytes, float, int, int] | None:
        """(jpeg, échelle image/CSS, largeur, hauteur de l'image) de la zone visible.
        Données personnelles floutées sur l'image (confidentialite.py)."""
        self.nav.flouter_prives()
        try:
            return self._capture_brute()
        finally:
            self.nav.deflouter()

    def _capture_brute(self) -> tuple[bytes, float, int, int] | None:
        cdp = self.nav.b._connect_page()
        try:
            m = cdp.send("Page.getLayoutMetrics")
            vv = m.get("cssVisualViewport") or m.get("visualViewport") or {}
            larg, haut = float(vv.get("clientWidth", 1280)), float(vv.get("clientHeight", 800))
            echelle = min(1.0, self.largeur_max / max(1.0, larg))
            res = cdp.send("Page.captureScreenshot", format="jpeg", quality=70,
                           clip={"x": float(vv.get("pageX", 0)), "y": float(vv.get("pageY", 0)),
                                 "width": larg, "height": haut, "scale": echelle})
            return (base64.b64decode(res.get("data", "")), echelle,
                    int(larg * echelle), int(haut * echelle))
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Secours vision : capture impossible ({e})")
            return None
        finally:
            cdp.close()

    def _element_au_point(self, x: float, y: float) -> dict:
        """Texte / type de l'élément sous (x, y), shadow DOM et cadres compris (garde-fous)."""
        js = r"""
((x, y) => {
  let doc = document, el = document.elementFromPoint(x, y), dx = 0, dy = 0;
  for (let i = 0; i < 10 && el; i++) {
    if (el.shadowRoot) { const e2 = el.shadowRoot.elementFromPoint(x - dx, y - dy); if (!e2 || e2 === el) break; el = e2; continue; }
    if (el.tagName === 'IFRAME') { let d = null; try { d = el.contentDocument; } catch (e) {}
      if (!d) break; const r = el.getBoundingClientRect(); dx += r.left; dy += r.top;
      const e2 = d.elementFromPoint(x - dx, y - dy); if (!e2) break; el = e2; continue; }
    break;
  }
  if (!el) return JSON.stringify({});
  const cible = el.closest('button, a, label, [role=button], [role=checkbox], [role=radio], input, select, textarea') || el;
  const txt = ((cible.innerText || '') + ' ' + (cible.getAttribute('aria-label') || '') + ' ' +
               (cible.value && cible.tagName === 'INPUT' && /submit|button/.test(cible.type) ? cible.value : '')).replace(/\s+/g, ' ').trim();
  return JSON.stringify({tag: cible.tagName, type: cible.type || '', role: cible.getAttribute('role') || '',
                         texte: txt.slice(0, 150)});
})"""
        try:
            brut = self.nav.b.eval(f"({js})({x}, {y})")
            return json.loads(brut) if isinstance(brut, str) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _souris(self, x: float, y: float) -> None:
        cdp = self.nav.b._connect_page()
        try:
            cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            for t in ("mousePressed", "mouseReleased"):
                cdp.send("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
        finally:
            cdp.close()

    def _refus(self, x: float, y: float) -> str:
        info = self._element_au_point(x, y)
        texte = f"{info.get('texte', '')} {info.get('type', '')}"
        if info.get("type") == "password":
            return "champ mot de passe"
        if ENVOI_FINAL.search(info.get("texte", "")) or info.get("type") == "submit" and \
                ENVOI_FINAL.search(texte):
            return f"envoi final (« {info.get('texte', '')[:60]} »), réservé au robot"
        if BOUTON_FICHIER.search(info.get("texte", "")):
            return "bouton d'upload (réservé au robot)"
        if (info.get("type") == "checkbox" or info.get("role") == "checkbox") and \
                CASE_INTERDITE.search(info.get("texte", "")):
            return f"case d'alerte / abonnement (« {info.get('texte', '')[:60]} »)"
        return ""

    # -- appel au modèle -------------------------------------------------------------
    def _appel(self, f: dict, image: bytes, texte: str) -> str:
        """Un appel à UN fournisseur ; renvoie le texte de la réponse (lève en cas d'erreur)."""
        b64 = base64.b64encode(image).decode()
        if f["nom"] == "claude":
            url = "https://api.anthropic.com/v1/messages"
            corps = {"model": f["modele"], "max_tokens": 2000, "system": SYSTEME,
                     "messages": [{"role": "user", "content": [
                         {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                         {"type": "text", "text": texte}]}]}
            entetes = {"x-api-key": f["cle"], "anthropic-version": "2023-06-01",
                       "content-type": "application/json"}
        else:
            # OpenAI et NVIDIA (build.nvidia.com) : même format « chat completions ».
            url = ("https://integrate.api.nvidia.com/v1/chat/completions" if f["nom"] == "nvidia"
                   else "https://api.openai.com/v1/chat/completions")
            image_part = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}
            if f["nom"] == "nvidia":
                # Certains modèles vision de NVIDIA refusent le rôle « system » : consignes en tête.
                messages = [{"role": "user", "content": [{"type": "text", "text": SYSTEME + "\n\n" + texte},
                                                          image_part]}]
            else:
                messages = [{"role": "system", "content": SYSTEME},
                            {"role": "user", "content": [{"type": "text", "text": texte}, image_part]}]
            corps = {"model": f["modele"], "max_tokens": 2000, "temperature": 0.2, "messages": messages}
            entetes = {"Authorization": f"Bearer {f['cle']}", "content-type": "application/json",
                       "accept": "application/json"}
        req = urllib.request.Request(url, data=json.dumps(corps).encode(), headers=entetes, method="POST")
        with urllib.request.urlopen(req, timeout=self.delai_reponse) as r:
            rep = json.loads(r.read())
        if f["nom"] == "claude":
            return "".join(b.get("text", "") for b in rep.get("content", []) if b.get("type") == "text")
        return rep["choices"][0]["message"]["content"] or ""

    def _demander(self, image: bytes, texte: str) -> dict:
        from confidentialite import courante as confidentialite
        return confidentialite().demasquer_tout(self._demander_brut(image, confidentialite().masquer(texte)))

    def _demander_brut(self, image: bytes, texte: str) -> dict:
        """
        Pose la question au premier fournisseur disponible. Un fournisseur qui refuse (clé,
        crédits) est écarté pour la session et on passe au suivant ; une erreur passagère
        (réseau, limite par minute) fait seulement passer au suivant pour CET appel.
        """
        derniere: Exception | None = None
        for f in list(self.fournisseurs):
            if f.get("pause_jusqua", 0) > time.monotonic():
                continue                     # trop lent récemment : mis de côté un moment
            try:
                sortie = self._appel(f, image, texte)
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode(errors="replace")[:160]
                except Exception:  # noqa: BLE001
                    pass
                derniere = e
                d = detail.lower()
                if e.code in (401, 402, 403, 404) or any(m in d for m in ("credit", "quota", "billing", "insufficient")):
                    self.fournisseurs.remove(f)
                    self.logger.warning(f"  Secours vision : {f['nom']} écarté pour la session "
                                        f"(HTTP {e.code} : {detail}).")
                else:
                    self.logger.warning(f"  Secours vision : {f['nom']} indisponible pour l'instant "
                                        f"(HTTP {e.code}) -> fournisseur suivant.")
                continue
            except Exception as e:  # noqa: BLE001 — réseau, format…
                derniere = e
                if isinstance(e, TimeoutError) or "timed out" in str(e):
                    f["pause_jusqua"] = time.monotonic() + self.pause_lent
                    self.logger.warning(f"  Secours vision : {f['nom']} ({f['modele']}) trop lent "
                                        f"-> mis de côté {self.pause_lent / 60:.0f} min.")
                    continue
                self.logger.warning(f"  Secours vision : {f['nom']} : {type(e).__name__} : "
                                    f"{str(e)[:120]} -> fournisseur suivant.")
                continue
            m = re.search(r"\{.*\}", sortie, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except ValueError:
                    pass
            derniere = ValueError(f"réponse illisible de {f['nom']}")
        if not self.fournisseurs:
            self._mettre_hors_service("tous les fournisseurs ont refusé (clé ou crédits)")
        raise RuntimeError(f"aucun fournisseur n'a répondu ({derniere})")

    # -- boucle de secours ---------------------------------------------------------
    def debloquer(self, probleme: str, contexte: str) -> tuple[bool, str]:
        """
        Tente de débloquer l'étape visible. Renvoie (réussi, compte rendu). Ne lève jamais :
        toute erreur rend (False, …) et le robot poursuit avec sa logique habituelle.
        """
        if not self.disponible():
            return False, ""
        faites: list[str] = []
        self.logger.info(f"  Secours vision : tentative de déblocage (« {probleme[:100]} »)…")
        for n in range(1, self.max_actions + 1):
            cap = self._capture()
            if not cap:
                return False, "capture impossible"
            image, echelle, larg, haut = cap
            message = (f"PROBLÈME QUI BLOQUE LE ROBOT : {probleme}\n\n"
                       f"CONTEXTE (profil du candidat, offre) :\n{contexte[:6000]}\n\n"
                       f"ACTIONS DÉJÀ FAITES PAR TOI : {' | '.join(faites) or 'aucune'}\n\n"
                       f"Image : {larg} x {haut} pixels. Action suivante ?")
            try:
                decision = self._demander(image, message)
            except Exception as e:  # noqa: BLE001 — aucun fournisseur n'a répondu
                self.logger.warning(f"  Secours vision : {str(e)[:160]} -> abandon du secours.")
                return False, ""

            action = str(decision.get("action", "")).lower()
            if action == "fini":
                resume = str(decision.get("resume", "")) or "étape débloquée"
                self.logger.info(f"  Secours vision : terminé — {resume}")
                return bool(faites), f"{resume} (actions : {' | '.join(faites)})"
            if action == "impossible" or action not in ("cliquer", "taper", "defiler"):
                self.logger.info(f"  Secours vision : pas de solution ({decision.get('raison', action)}).")
                return False, ""
            try:
                x = float(decision.get("x")) / echelle
                y = float(decision.get("y")) / echelle
            except (TypeError, ValueError):
                return False, ""
            if action == "defiler":
                cdp = self.nav.b._connect_page()
                try:
                    cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0,
                             deltaY=-500 if str(decision.get("sens")) == "haut" else 500)
                finally:
                    cdp.close()
                faites.append(f"défilé {decision.get('sens', 'bas')}")
                time.sleep(0.8)
                continue
            refus = self._refus(x, y)
            if refus:
                self.logger.info(f"  Secours vision : action refusée ({refus}).")
                faites.append(f"REFUSÉ par le robot : {refus} — ne le refais pas")
                continue
            self._souris(x, y)
            if action == "taper":
                texte = str(decision.get("texte", ""))
                time.sleep(0.3)
                cdp = self.nav.b._connect_page()
                try:
                    # Sélectionne le contenu existant (Ctrl+A) puis écrit par-dessus.
                    for t in ("rawKeyDown", "keyUp"):
                        cdp.send("Input.dispatchKeyEvent", type=t, modifiers=2, key="a", code="KeyA",
                                 windowsVirtualKeyCode=65)
                    cdp.send("Input.insertText", text=texte)
                finally:
                    cdp.close()
                faites.append(f"tapé « {texte[:40]} » ({decision.get('pourquoi', '')})")
            else:
                faites.append(f"cliqué ({decision.get('pourquoi', '')})")
            self.logger.info(f"  Secours vision : {faites[-1]}")
            time.sleep(1.2)
            try:
                self.nav.attendre_apres_action()
            except Exception:  # noqa: BLE001
                pass
        return bool(faites), f"limite d'actions atteinte (actions : {' | '.join(faites)})"
