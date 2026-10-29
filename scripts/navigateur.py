"""
Pilotage de Chrome via Tappi (protocole CDP).

Tappi se connecte à un Chrome lancé avec --remote-debugging-port. Ce Chrome
utilise un profil dédié (config: navigateur.profil_chrome) : tes connexions
aux sites d'emploi et tes extensions Chrome y restent enregistrées.
"""

from __future__ import annotations

import base64
import difflib
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from tappi import Browser
from tappi.core import BrowserNotRunning, CDPError, CDPSession, _find_chrome

from utils import chemin_projet

# ------------------------------------------------------------------------------------------------
# Aucune commande envoyée à Chrome ne peut plus bloquer le robot
# ------------------------------------------------------------------------------------------------
# Tappi attendait la réponse de Chrome SANS limite de temps (CDPSession.send). Une alerte
# JavaScript d'un site (« Vous avez téléchargé le CV avec succès » + OK) fige la page : Chrome ne
# répond plus, et le robot est resté bloqué des heures (vu le 26/09). Le surveillant d'alertes
# (GardeAlertes, plus bas) ferme ces fenêtres ; cette limite est le filet de sécurité pour tout
# autre blocage : au-delà, la commande échoue (CDPError, déjà gérée partout) et le robot continue.
DELAI_MAX_COMMANDE_CDP = 60.0


def _envoyer_avec_delai(self, method: str, **params):
    """CDPSession.send de Tappi, à l'identique, mais avec une limite de temps."""
    self._id += 1
    msg_id = self._id
    self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
    fin = time.monotonic() + DELAI_MAX_COMMANDE_CDP
    while True:
        reste = fin - time.monotonic()
        if reste <= 0:
            raise CDPError(f"Chrome n'a pas répondu en {DELAI_MAX_COMMANDE_CDP:.0f} s ({method}) : "
                           f"page figée")
        try:
            raw = self._ws.recv(timeout=reste)
        except TimeoutError:
            continue
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            if "error" in msg:
                raise CDPError(msg["error"].get("message", str(msg["error"])))
            return msg.get("result", {})


CDPSession.send = _envoyer_avec_delai


class GardeAlertes(threading.Thread):
    """
    Ferme AUSSITÔT (bouton OK) toute fenêtre JavaScript d'un site : alert(), confirm(),
    prompt(), « Quitter la page ? ». Tant qu'une de ces fenêtres est ouverte, Chrome ne répond
    plus à aucune commande sur la page.

    Chrome ne laisse fermer une telle fenêtre que par un client qui ÉCOUTAIT déjà la page à son
    ouverture (vérifié : après coup, « No dialog is showing »). Ce surveillant garde donc une
    connexion ouverte sur chaque onglet (et chaque cadre d'un autre site) du navigateur ACTIF,
    et suit la rotation des navigateurs. Il ne fait rien d'autre et ne bloque jamais le robot.
    """

    def __init__(self, nav: "Navigateur"):
        super().__init__(daemon=True, name="GardeAlertes")
        self.nav = nav
        self._connexions: dict[str, object] = {}    # id de la cible -> websocket
        self._port = ""
        self._dernier_inventaire = 0.0
        self._verrou = threading.Lock()
        self._fermees: list[str] = []               # messages pas encore lus par le robot

    def alertes_fermees(self) -> list[str]:
        """Messages des fenêtres fermées depuis le dernier appel (puis la liste est vidée)."""
        with self._verrou:
            res, self._fermees = self._fermees, []
        return res

    def run(self) -> None:
        while True:
            try:
                self._tour()
            except Exception:  # noqa: BLE001 — le surveillant ne s'arrête jamais
                time.sleep(1.0)
            time.sleep(0.2)

    def _fermer_tout(self) -> None:
        for ws in self._connexions.values():
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._connexions.clear()

    def _tour(self) -> None:
        from websockets.sync.client import connect   # même bibliothèque que Tappi
        port = str(self.nav.cdp_url).rsplit(":", 1)[-1].split("/")[0]
        if port != self._port:                       # rotation : autre navigateur
            self._fermer_tout()
            self._port = port
        if time.monotonic() - self._dernier_inventaire > 1.0:
            self._dernier_inventaire = time.monotonic()
            try:
                cibles = json.loads(urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2).read())
            except Exception:  # noqa: BLE001 — navigateur fermé ou en cours de lancement
                self._fermer_tout()
                return
            voulues = {c["id"]: c for c in cibles
                       if c.get("type") in ("page", "iframe")
                       and str(c.get("url", "")).startswith(("http", "about:blank"))}
            for tid in [t for t in self._connexions if t not in voulues]:
                try:
                    self._connexions.pop(tid).close()
                except Exception:  # noqa: BLE001
                    pass
            for tid in voulues:
                if tid in self._connexions:
                    continue
                try:
                    ws = connect(f"ws://127.0.0.1:{port}/devtools/page/{tid}", open_timeout=3,
                                 max_size=50_000_000)
                    ws.send(json.dumps({"id": 1, "method": "Page.enable", "params": {}}))
                    self._connexions[tid] = ws
                except Exception:  # noqa: BLE001 — cible disparue entre-temps
                    continue
        for tid, ws in list(self._connexions.items()):
            while True:
                try:
                    msg = json.loads(ws.recv(timeout=0))
                except TimeoutError:
                    break
                except Exception:  # noqa: BLE001 — onglet fermé : connexion retirée
                    self._connexions.pop(tid, None)
                    break
                if msg.get("method") != "Page.javascriptDialogOpening":
                    continue
                p = msg.get("params", {})
                try:
                    ws.send(json.dumps({"id": 2, "method": "Page.handleJavaScriptDialog",
                                        "params": {"accept": True, "promptText": ""}}))
                except Exception:  # noqa: BLE001
                    continue
                texte = " ".join(str(p.get("message", "")).split())[:300]
                self.nav.logger.info(f"  Fenêtre « {p.get('type', 'alert')} » du site fermée "
                                     f"automatiquement (OK) : « {texte} »")
                with self._verrou:
                    self._fermees.append(texte)

# Floute les zones qui contiennent une donnée personnelle (motifs de confidentialite.py).
JS_FLOUTER_PRIVES = r"""
((motifs) => {
  const res = motifs.map(m => { try { return new RegExp(m, 'i'); } catch (e) { return null; } }).filter(Boolean);
  if (!res.length) return 0;
  const touche = t => res.some(r => r.test(t));
  const racines = [];
  const ajouter = r => {
    racines.push(r);
    for (const el of r.querySelectorAll('*')) {
      if (el.shadowRoot) ajouter(el.shadowRoot);
      if (el.tagName === 'IFRAME') { try { if (el.contentDocument) ajouter(el.contentDocument); } catch (e) {} }
    }
  };
  ajouter(document);
  let n = 0;
  for (const r of racines) {
    const doc = r.ownerDocument || r;
    if (!r.querySelector('style[data-bpy-flou]')) {
      const s = doc.createElement('style');
      s.setAttribute('data-bpy-flou', '1');
      s.textContent = '.bpy-prive{filter:blur(9px)!important;}';
      (r.head || r).appendChild(s);
    }
    for (const el of r.querySelectorAll('input, textarea')) {
      if (el.value && touche(el.value)) { el.classList.add('bpy-prive'); n++; }
    }
    const w = doc.createTreeWalker(r, NodeFilter.SHOW_TEXT);
    let t;
    while ((t = w.nextNode())) {
      const p = t.parentElement;
      if (p && t.nodeValue && t.nodeValue.trim() && touche(t.nodeValue) && !p.classList.contains('bpy-prive')) {
        p.classList.add('bpy-prive'); n++;
      }
    }
  }
  return n;
})(%s)
"""

JS_DEFLOUTER = r"""
(() => {
  const racines = [];
  const ajouter = r => {
    racines.push(r);
    for (const el of r.querySelectorAll('*')) {
      if (el.shadowRoot) ajouter(el.shadowRoot);
      if (el.tagName === 'IFRAME') { try { if (el.contentDocument) ajouter(el.contentDocument); } catch (e) {} }
    }
  };
  ajouter(document);
  for (const r of racines) {
    r.querySelectorAll('.bpy-prive').forEach(el => el.classList.remove('bpy-prive'));
    r.querySelectorAll('style[data-bpy-flou]').forEach(s => s.remove());
  }
  return 1;
})()
"""

# Empêche l'ouverture de nouveaux onglets : les liens « target=_blank » et
# window.open() s'ouvrent dans l'onglet courant (Tappi pilote un seul onglet).
JS_NEUTRALISER_ONGLETS = r"""
(() => {
  document.querySelectorAll('a[target], form[target]').forEach(a => a.setAttribute('target', '_self'));
  if (!window.__jaOpen) {
    window.__jaOpen = true;
    // Beaucoup de sites (Glassdoor « Easy Apply »…) ouvrent d'abord un onglet VIDE, puis y
    // chargent la vraie adresse : w = window.open('about:blank'); w.location = url.
    // Suivre « about:blank » dans l'onglet courant effaçait la page de l'offre. On l'ignore :
    // la vraie adresse, écrite ensuite dans w.location, s'ouvre alors ici même.
    window.open = (u) => {
      if (u && !/^\s*about:blank/i.test(String(u))) { location.href = u; }
      return window;
    };
  }
  return true;
})()
"""

# Liste les champs <input type=file> (même cachés) et leur attribue un numéro
JS_CHAMPS_FICHIER = r"""
(() => {
  const tous = [];
  const parcourir = (racine) => {
    racine.querySelectorAll('input[type=file]').forEach(e => tous.push(e));
    racine.querySelectorAll('*').forEach(el => { if (el.shadowRoot) parcourir(el.shadowRoot); });
  };
  parcourir(document);
  return JSON.stringify(tous.map((el, i) => {
    el.setAttribute('data-ja-file', String(i));
    let libelle = [el.getAttribute('aria-label'), el.name, el.id].filter(Boolean).join(' ');
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) libelle = l.innerText + ' ' + libelle;
    }
    const parent = el.closest('label, fieldset, section, div');
    const contexte = parent ? (parent.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 150) : '';
    const fichier = (el.files && el.files.length) ? el.files[0].name : '';
    return {input_fichier: i, libelle: libelle.trim().slice(0, 100), contexte, accept: el.accept || '', deja_rempli: fichier};
  }));
})()
"""

# Options des listes déroulantes déjà indexées par Tappi (attribut data-bpy-idx)
JS_OPTIONS_SELECT = r"""
(() => {
  const res = {};
  document.querySelectorAll('select[data-bpy-idx]').forEach(s => {
    res[s.getAttribute('data-bpy-idx')] = [...s.options].map(o => o.text.trim()).filter(Boolean).slice(0, 40);
  });
  return JSON.stringify(res);
})()
"""

# Iframes visibles (certains sites intègrent le formulaire dans une iframe)
JS_IFRAMES = r"""
(() => JSON.stringify([...document.querySelectorAll('iframe')]
  .filter(f => f.src && f.offsetWidth > 200 && f.offsetHeight > 150)
  .map(f => f.src).filter(s => !/recaptcha|hcaptcha|turnstile|challenges\.cloudflare|doubleclick|youtube|google\.com\/maps/i.test(s))
  .slice(0, 10)))()
"""

# Indice de CAPTCHA visible (information transmise au LLM)
JS_CAPTCHA = r"""
(() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 20 || r.bottom < 0 || r.right < 0 || r.top > innerHeight + 2000) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity || '1') > 0.05;
  };
  const visibles = [...document.querySelectorAll('iframe')].filter(vis);
  const challenge = visibles.some(f => /recaptcha\/(api2|enterprise)\/bframe|hcaptcha|turnstile|challenges\.cloudflare|arkoselabs|funcaptcha|geetest|awswaf|leminnow/i.test(f.src || '')
                                       || (/recaptcha/i.test(f.src || '') && !/size=invisible/i.test(f.src || '')));
  const widgets = [...document.querySelectorAll('.geetest_panel, .geetest_box, .geetest_holder, ' +
    'awswaf-captcha, #lemin-cropped-captcha, [id^="lemin-"], .h-captcha, .cf-turnstile, #px-captcha')].filter(vis);
  return challenge || widgets.length > 0;
})()
"""

# Page de vérification anti-robot affichée À LA PLACE de l'offre (Cloudflare « Vérification
# supplémentaire requise », « Just a moment… », « Verify you are human »…). On ne la
# confond pas avec une offre qui contient un petit CAPTCHA : la page doit être courte.
JS_PAGE_ANTI_ROBOT = r"""
(() => {
  const t = (document.body && document.body.innerText || '').replace(/\s+/g, ' ');
  if (t.length > 2500) return false;
  const motifs = /v[ée]rification suppl[ée]mentaire requise|additional verification required|v[ée]rifiez que vous [êe]tes (un )?humain|verify you are human|just a moment|checking your browser|v[ée]rification de votre navigateur|ray id|erreurs cloudflare|cloudflare errors|press (and|&) hold|appuyez (de fa[çc]on prolong[ée]e|longuement)/i;
  return motifs.test(t) || !!document.querySelector('#challenge-form, #challenge-stage, .cf-turnstile, [id^="cf-chl-widget"], #px-captcha');
})()
"""

# Case anti-robot VALIDÉE : le widget a écrit sa réponse dans son champ caché.
JS_CASE_VALIDEE = r"""
(() => [...document.querySelectorAll(
  'input[name="cf-turnstile-response"], textarea[name="g-recaptcha-response"], ' +
  'textarea[name="h-captcha-response"], [name*="captcha-response" i]')]
  .some(e => (e.value || '').trim().length > 20))()
"""

# État léger de la page, pour savoir si elle est utilisable (toujours une chaîne JSON :
# Tappi ne renvoie pas None pour un « null » JavaScript).
JS_SIGNATURE_PAGE = r"""
(() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const n = [...document.querySelectorAll('a[href], button, input, select, textarea, [role="button"]')]
    .filter(vis).length;
  return JSON.stringify({url: location.href, n, t: (document.body ? document.body.innerText : '').length,
                         pret: document.readyState === 'complete'});
})()
"""

# Page dans un état TRANSITOIRE : bouton « Loading… / Saving… / Submitting… », zone marquée
# aria-busy, ou page quasi vide qui annonce un chargement. (La barre de progression « 100 % »
# d'Indeed est un role=progressbar permanent : elle n'est volontairement pas prise en compte.)
JS_CHARGEMENT_EN_COURS = r"""
(() => {
  // Renvoie 2 = chargement CERTAIN (message « Loading… », zone aria-busy, barre de progression
  // sans valeur), 1 = indice FAIBLE (élément dont la classe contient spinner / loader), 0 = rien.
  const vis = el => { if (!el) return false; const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(el); return cs.visibility !== 'hidden' && cs.display !== 'none'; };
  // Seulement les iframes RÉELLEMENT affichées : LinkedIn garde une iframe cachée de
  // préchargement (/preload/) dont la balise <html> porte « app-loader » en permanence.
  const affichee = f => { const r = f.getBoundingClientRect();
    return vis(f) && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth; };
  const docs = [document];
  for (let i = 0; i < docs.length; i++)
    for (const f of docs[i].querySelectorAll('iframe'))
      try { if (f.contentDocument && affichee(f)) docs.push(f.contentDocument); } catch (e) {}
  // Le texte ENTIER doit être un message de chargement (« Loading… », « Preparing review »…) :
  // une phrase de description qui commence par « Préparation … » n'en est pas un.
  const motif = /^\s*(loading|chargement( en cours)?|saving( and continuing)?|enregistrement en cours|submitting( your application)?|envoi en cours|preparing( review| your application)?|pr[ée]paration en cours|uploading( your file)?|t[ée]l[ée]versement en cours|processing|traitement en cours|please wait|veuillez patienter)\s*(\.\.\.|…|\.)?\s*$/i;
  let faible = false;
  for (const d of docs) {
    if (!d.body) continue;
    const tw = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = tw.nextNode())) {
      const t = n.textContent;
      if (t.length < 80 && motif.test(t) && vis(n.parentElement)) return 2;
    }
    if ([...d.querySelectorAll('[aria-busy="true"], [role="progressbar"]:not([aria-valuenow])')].some(vis)) return 2;
    // Nom de classe seul : jamais la racine de la page (<html>, <body> décrivent l'état de
    // l'application, pas un chargement en cours), et un vrai spinner est petit.
    if (!faible) faible = [...d.querySelectorAll('[class*="spinner" i], [class*="loader" i]')].some(e => {
      if (e === d.documentElement || e === d.body || !vis(e)) return false;
      const r = e.getBoundingClientRect();
      return r.width * r.height < 0.25 * innerWidth * innerHeight;
    });
  }
  return faible ? 1 : 0;
})()
"""

# Listes « maison » (role=combobox) : Tappi écarte les DOUBLONS (même type + même texte). Cinq
# listes « Select an option » n'en font qu'une pour lui : seule la 1re est numérotée, les autres
# sont invisibles pour le modèle. On donne à chacune un nom UNIQUE — sa question — avant la
# lecture (aria-label, recalculé à chaque fois, marqué data-bpy-lbl pour le distinguer d'un
# aria-label d'origine).
JS_NOMMER_LISTES = r"""
(() => {
  let n = 0;
  for (const el of document.querySelectorAll('[role=combobox], [aria-haspopup=listbox]')) {
    if (el.hasAttribute('aria-label') && !el.hasAttribute('data-bpy-lbl')) continue;
    const ids = el.getAttribute('aria-labelledby');
    let q = ids ? ids.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ') : '';
    q = q.replace(/\s+/g, ' ').replace(/\*?\s*required\s*$/i, '').trim();
    if (!q) continue;
    const valeur = (el.innerText || '').replace(/\s+/g, ' ').trim();
    el.setAttribute('aria-label', (q + ' — ' + valeur).slice(0, 110));
    el.setAttribute('data-bpy-lbl', '1');
    n++;
  }
  return n;
})()
"""

# Cases et boutons radio SANS nom accessible : Tappi les décrit par leur nom technique
# (« radio-group-«ru» [on] »), identique pour « Oui » et « Non » d'une même question ; il les
# prend alors pour des doublons et n'en numérote qu'un — le robot ne pouvait plus répondre
# « Non ». On leur donne le texte affiché à côté (label, aria-labelledby, texte voisin).
JS_NOMMER_CASES = r"""
(() => {
  // Toutes les racines : la page, ses cadres de même origine ET les shadow DOM (formulaires
  // construits en composants isolés), comme partout ailleurs dans le robot.
  const racines = [];
  const ajouter = r => {
    racines.push(r);
    for (const el of r.querySelectorAll('*')) {
      if (el.shadowRoot) ajouter(el.shadowRoot);
      if (el.tagName === 'IFRAME') { try { if (el.contentDocument) ajouter(el.contentDocument); } catch (e) {} }
    }
  };
  ajouter(document);
  const net = t => (t || '').replace(/\s+/g, ' ').trim();
  const CASES = 'input[type=radio], input[type=checkbox], [role=radio], [role=checkbox]';
  // Texte PROPRE de l'option (« Yes », « No »…), sans passer par aria-labelledby, qui désigne
  // souvent la QUESTION commune à toutes les options.
  const texteOption = el => {
    const r = el.getRootNode();
    let t = '';
    if (el.labels && el.labels.length) t = [...el.labels].map(l => l.innerText).join(' ');
    if (!t && el.id && r.querySelector) { const l = r.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t && el.closest('label')) t = el.closest('label').innerText;
    if (!t && el.getAttribute('role')) t = el.innerText;
    if (!t) { let s = el.nextSibling;
              while (s && !net(s.textContent)) s = s.nextSibling;
              if (s) t = s.textContent; }
    return net(t).slice(0, 100);
  };
  let n = 0;
  for (const d of racines) {
    const elts = [...d.querySelectorAll(CASES)];
    // 1) Nom lisible pour les options qui n'en ont pas (ou qu'on a déjà nommées nous-mêmes).
    for (const el of elts) {
      if (el.hasAttribute('aria-label') && !el.hasAttribute('data-bpy-lbl')) continue;
      const r = el.getRootNode();
      let t = texteOption(el);
      if (!t) { const ids = el.getAttribute('aria-labelledby');
                if (ids && r.getElementById) t = net(ids.split(/\s+/).map(i => (r.getElementById(i) || {}).innerText || '').join(' ')).slice(0, 100); }
      if (!t) continue;
      el.setAttribute('aria-label', t);
      el.setAttribute('data-bpy-lbl', '1');
      n++;
    }
    // 2) Options qui PARTAGENT le même nom (LinkedIn met la question sur chaque option) :
    //    Tappi les prendrait pour des doublons et n'en garderait qu'une. On ajoute à chacune
    //    son propre texte, ou à défaut son rang (« option 2/2 »).
    const groupes = {};
    for (const el of elts) {
      const a = el.getAttribute('aria-label');
      if (a) (groupes[a] = groupes[a] || []).push(el);
    }
    for (const [nom, membres] of Object.entries(groupes)) {
      if (membres.length < 2) continue;
      membres.forEach((el, i) => {
        let opt = texteOption(el);
        if (!opt || opt === nom || nom.includes(opt) && opt.length > 40) opt = `option ${i + 1}/${membres.length}`;
        el.setAttribute('aria-label', (opt + ' — ' + nom).slice(0, 140));
        el.setAttribute('data-bpy-lbl', '2');
        n++;
      });
    }
  }
  return n;
})()
"""

# Options de chaque liste « maison » numérotée, lues dans la page SANS l'ouvrir (Indeed les garde
# dans le DOM, liste fermée). Le modèle peut ainsi remplir toutes les listes en une réponse.
JS_OPTIONS_MENUS = r"""
(() => {
  const out = {};
  for (const el of document.querySelectorAll('[data-bpy-idx]')) {
    if (el.getAttribute('role') !== 'combobox' && el.getAttribute('aria-haspopup') !== 'listbox') continue;
    if (el.tagName === 'INPUT' || el.tagName === 'SELECT') continue;
    const id = el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || '';
    const pop = id && document.getElementById(id);
    if (!pop) continue;
    const opts = [...pop.querySelectorAll('[role=option], [role=menuitemcheckbox], [role=menuitemradio]')];
    if (!opts.length) continue;
    out[el.getAttribute('data-bpy-idx')] = {
      multiple: opts.some(o => o.getAttribute('role') === 'menuitemcheckbox') || pop.querySelector('[aria-multiselectable=true]') !== null,
      options: opts.slice(0, 60).map(o => (o.textContent || '').replace(/\s+/g, ' ').trim()
                                     + ((o.getAttribute('aria-selected') === 'true' || o.getAttribute('aria-checked') === 'true') ? ' [choisi]' : ''))
    };
  }
  return JSON.stringify(out);
})()
"""

# IDENTITÉ de chaque élément numéroté, indépendante de son numéro : type, rôle, nom, valeur et
# question associée. Les sites React (Indeed…) redessinent des morceaux de page : les éléments
# redessinés perdent leur numéro, Tappi renumérote tout dans un autre ordre, et un numéro vu par
# le modèle peut désigner un AUTRE élément au moment d'agir. L'identité permet de le retrouver.
JS_IDENTITES = r"""
(() => {
  const txt = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const question = el => {
    const ids = el.getAttribute('aria-labelledby');
    if (ids) return ids.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return l.innerText; }
    const l = el.closest('label'); if (l) return l.innerText;
    return el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
  };
  const ident = el => [el.tagName, el.getAttribute('role') || el.type || '', el.getAttribute('name') || '',
                       el.type === 'radio' || el.type === 'checkbox' ? (el.value || '') : '',
                       txt(question(el)),
                       // Texte affiché : seulement pour un bouton ou un lien. Une liste (« Select an
                       // option » -> « 2 selected ») ou un champ change de texte quand on le remplit.
                       (['BUTTON', 'A'].includes(el.tagName) ||
                        ['button', 'link', 'tab', 'menuitem'].includes(el.getAttribute('role') || ''))
                         ? txt(el.innerText) : ''].join('|');
  const out = {};
  const parcourir = racine => {
    racine.querySelectorAll('[data-bpy-idx]').forEach(el => { out[el.getAttribute('data-bpy-idx')] = ident(el); });
    racine.querySelectorAll('*').forEach(el => { if (el.shadowRoot) parcourir(el.shadowRoot); });
  };
  parcourir(document);
  return JSON.stringify(out);
})()
"""

# CAPTCHA encore à RÉSOUDRE. Une page peut porter plusieurs widgets : Indeed a un reCAPTCHA
# INVISIBLE (déjà validé, jeton présent) ET un reCAPTCHA visible avec défi à images. Regarder « un
# jeton existe quelque part » faisait croire à tort que tout était validé. On juge chaque widget
# VISIBLE sur SA propre réponse, et un défi à images affiché compte toujours comme à résoudre.
JS_CAPTCHA_A_RESOUDRE = r"""
(() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 20 || r.bottom < 0 || r.right < 0 || r.top > innerHeight + 2000) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity || '1') > 0.05;
  };
  const cadres = [...document.querySelectorAll('iframe')].filter(vis);
  // 1) Défi affiché (grille d'images, puzzle) : forcément pas encore résolu.
  if (cadres.some(f => /recaptcha\/(api2|enterprise)\/bframe|hcaptcha\.com\/.*(challenge|hcaptcha-challenge)|arkoselabs|funcaptcha|geetest|awswaf/i.test(f.src || '')))
    return true;
  // 2) Widget visible (case, Turnstile…) : on lit la réponse de CE widget, pas d'un autre.
  const champ = '[name="g-recaptcha-response"], [name="h-captcha-response"], [name="cf-turnstile-response"], [name*="captcha-response" i]';
  const widgets = cadres.filter(f => /recaptcha\/(api2|enterprise)\/anchor|hcaptcha|turnstile|challenges\.cloudflare/i.test(f.src || '')
                                     && !/size=invisible/i.test(f.src || ''));
  for (const f of widgets) {
    let n = f.parentElement, rep = null;
    for (let k = 0; k < 8 && n && !rep; k++, n = n.parentElement) rep = n.querySelector(champ);
    if (!rep || (rep.value || '').trim().length < 20) return true;
  }
  return false;
})()
"""

# Empreinte de la page, iframes de même origine comprises (la revue Indeed vit dans une iframe) :
# change dès que le contenu change — y compris un bouton qui passe à « Loading… ».
JS_EMPREINTE_PAGE = r"""
(() => {
  const docs = [document];
  for (let i = 0; i < docs.length; i++)
    for (const f of docs[i].querySelectorAll('iframe'))
      try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {}
  return location.href + '|' + document.readyState + '|' +
         docs.map(d => d.body ? d.body.innerHTML.length : 0).join(',');
})()
"""

# Bouton « modifier le CV » / « ajouter une lettre » d'une page de REVUE. Cherché aussi dans les
# iframes de même origine : Indeed SmartApply affiche sa revue dans une iframe (« Application
# preview ») que la liste d'éléments de Tappi ne voit pas — le modèle ne peut donc pas le cliquer.
JS_BOUTON_DOCUMENTS = r"""
((genre, cliquer) => {
  const motifs = genre === 'cv'
    ? /(edit|change|replace|update|modifier|changer|remplacer).{0,20}(resume|r[ée]sum[ée]|cv)/i
    : /(add|upload|ajouter|joindre).{0,25}(supporting|cover letter|lettre|documents? (compl|suppl)|additional doc)/i;
  const titres = genre === 'cv' ? /^(resume|r[ée]sum[ée]|cv)\b/i
                                : /(supporting documents|cover letter|lettre de motivation|documents compl)/i;
  const racines = [document];
  for (let i = 0; i < racines.length; i++)
    for (const f of racines[i].querySelectorAll('iframe'))
      try { if (f.contentDocument) racines.push(f.contentDocument); } catch (e) {}
  for (const doc of racines) {
    const boutons = [...doc.querySelectorAll('button, a, [role="button"]')];
    // 1) libellé explicite (aria-label ou texte), ex. « Edit resume », « Add supporting documents »
    let b = boutons.find(e => motifs.test((e.getAttribute('aria-label') || '') + ' ' + (e.innerText || '')));
    // 2) simple « Edit » / « Add » placé à côté du titre de la bonne section
    if (!b) b = boutons.find(e => {
      if (!/^\s*(edit|add|modifier|ajouter)\s*$/i.test(e.innerText || '')) return false;
      let n = e;
      for (let k = 0; k < 4 && n; k++, n = n.parentElement) {
        const h = n.querySelector && n.querySelector('h1, h2, h3, h4, [role="heading"]');
        if (h) return titres.test((h.innerText || '').trim());
      }
      return false;
    });
    if (b) {
      if (cliquer) { b.scrollIntoView({block: 'center'}); b.click(); }
      return (b.getAttribute('aria-label') || b.innerText || '').trim();
    }
  }
  return '';
})
"""

# Position de la case à cocher anti-robot (au centre-gauche du widget, là où est la case).
JS_CASE_ANTI_ROBOT = r"""
(() => {
  const vis = el => { const r = el.getBoundingClientRect();
    return r.width > 20 && r.height > 20 && r.bottom > 0 && r.top < innerHeight; };
  const cadres = [...document.querySelectorAll('iframe')].filter(f => vis(f) &&
    /challenges\.cloudflare|turnstile|recaptcha\/api2\/anchor|hcaptcha/i.test(f.src || ''));
  const widgets = [...document.querySelectorAll(
    '.cf-turnstile, [id^="cf-chl-widget"], #challenge-stage, .g-recaptcha, .h-captcha')].filter(vis);
  const cible = cadres[0] || widgets[0];
  if (!cible) return null;
  const r = cible.getBoundingClientRect();
  return JSON.stringify({x: r.left + Math.min(30, r.width / 2), y: r.top + r.height / 2});
})()
"""


# Vision : dessine sur la page le numéro de chaque élément interactif (cadre rouge + étiquette)
# pour que le modèle relie ce qu'il VOIT sur la capture aux index des actions.
JS_MARQUER_ELEMENTS = r"""
(() => {
  const ancien = document.getElementById('__ja_marques');
  if (ancien) ancien.remove();
  const boite = document.createElement('div');
  boite.id = '__ja_marques';
  boite.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none;';
  const elts = [];
  const parcourir = (racine) => {
    racine.querySelectorAll('[data-bpy-idx]').forEach(e => elts.push(e));
    racine.querySelectorAll('*').forEach(el => { if (el.shadowRoot) parcourir(el.shadowRoot); });
  };
  parcourir(document);
  let n = 0;
  for (const el of elts) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) continue;
    const cadre = document.createElement('div');
    cadre.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;` +
                          'border:2px solid #e11;box-sizing:border-box;';
    const etiquette = document.createElement('div');
    etiquette.textContent = el.getAttribute('data-bpy-idx');
    etiquette.style.cssText = `position:fixed;left:${Math.max(0, r.left)}px;top:${Math.max(0, r.top - 14)}px;` +
                              'background:#e11;color:#fff;font:bold 11px/13px Arial;padding:0 3px;';
    boite.appendChild(cadre);
    boite.appendChild(etiquette);
    n++;
  }
  document.documentElement.appendChild(boite);
  // Une fenêtre MODALE (<dialog> ouvert par showModal, popover…) est affichée par Chrome dans
  // la « couche supérieure », AU-DESSUS de tout z-index : nos cases étaient dessinées dessous,
  // donc invisibles sur la fenêtre de candidature. On les met à leur tour dans la couche
  // supérieure (popover) ; la dernière entrée s'affiche au-dessus de la fenêtre.
  if (boite.showPopover) {
    boite.setAttribute('popover', 'manual');
    boite.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;margin:0;padding:0;' +
                          'border:0;background:transparent;overflow:visible;pointer-events:none;';
    try { boite.showPopover(); } catch (e) {}
  }
  return n;
})()
"""

# Vrai libellé lisible de chaque élément indexé (Tappi ne donne parfois que « on » pour une case
# à cocher) : aria-label, <label for>, <label> englobant, texte voisin, placeholder…
JS_LIBELLES = r"""
(() => {
  const res = {};
  const elts = [];
  const parcourir = (racine) => {
    racine.querySelectorAll('[data-bpy-idx]').forEach(e => elts.push(e));
    racine.querySelectorAll('*').forEach(el => { if (el.shadowRoot) parcourir(el.shadowRoot); });
  };
  parcourir(document);
  for (const el of elts) {
    let t = el.getAttribute('aria-label') || '';
    const ids = el.getAttribute('aria-labelledby');
    if (!t && ids) t = ids.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    if (!t && el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) t = l.innerText;
    }
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    const coche = el.type === 'checkbox' || el.type === 'radio' || el.getAttribute('role') === 'switch'
                  || el.getAttribute('role') === 'checkbox';
    if (!t && coche && el.parentElement) t = el.parentElement.innerText;
    if (!t && coche && el.parentElement && el.parentElement.parentElement) t = el.parentElement.parentElement.innerText;
    if (!t) t = el.getAttribute('placeholder') || el.getAttribute('title') || '';
    t = (t || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    if (coche) t = (el.checked || el.getAttribute('aria-checked') === 'true' ? '[coché] ' : '[non coché] ') + t;
    // Hors écran : en dehors de la fenêtre, ou caché dans une zone qui défile (ex. fenêtre superposée)
    const r = el.getBoundingClientRect();
    let hors = r.bottom <= 0 || r.top >= innerHeight;
    for (let p = el.parentElement; p && !hors; p = p.parentElement) {
      if (/(auto|scroll)/.test(getComputedStyle(p).overflowY) && p.scrollHeight > p.clientHeight + 5) {
        const pr = p.getBoundingClientRect();
        if (r.top >= pr.bottom - 2 || r.bottom <= pr.top + 2) hors = true;
      }
    }
    if (hors) t = '[hors écran : défiler pour le voir] ' + t;
    if (t) res[el.getAttribute('data-bpy-idx')] = t;
  }
  return JSON.stringify(res);
})()
"""

# Texte AUTOUR de chaque case à cocher / radio / interrupteur indexé : le libellé seul
# (« Confirmé », « Oui »…) ne dit pas de quoi il s'agit ; le paragraphe qui précède
# contient presque toujours la vraie question (consentement RGPD, alerte emploi…).
# Sert aux verrous de sécurité (consentement obligatoire vs case marketing).
JS_CONTEXTES = r"""
(() => {
  const res = {};
  const parcourir = (racine) => {
    racine.querySelectorAll('[data-bpy-idx]').forEach(el => {
      const coche = el.type === 'checkbox' || el.type === 'radio' || el.getAttribute('role') === 'switch'
                    || el.getAttribute('role') === 'checkbox';
      if (!coche) return;
      const morceaux = [];
      // remonte jusqu'à 3 niveaux en accumulant le texte des conteneurs (sans répétition)
      let p = el;
      for (let n = 0; n < 3 && p; n++) {
        p = p.parentElement;
        if (!p) break;
        const t = (p.innerText || '').replace(/\s+/g, ' ').trim();
        if (t && !morceaux.includes(t) && t.length <= 800) morceaux.push(t);
        if (t.length > 30) break;   // on a trouvé le vrai paragraphe explicatif
      }
      // frère précédent immédiat (souvent le texte légal au-dessus de la case)
      let frere = el.previousElementSibling || (el.parentElement && el.parentElement.previousElementSibling);
      if (frere) {
        const t = (frere.innerText || '').replace(/\s+/g, ' ').trim();
        if (t && !morceaux.includes(t)) morceaux.unshift(t);
      }
      const contexte = morceaux.join(' | ').slice(0, 400);
      if (contexte) res[el.getAttribute('data-bpy-idx')] = contexte;
    });
    racine.querySelectorAll('*').forEach(el => { if (el.shadowRoot) parcourir(el.shadowRoot); });
  };
  parcourir(document);
  return JSON.stringify(res);
})()
"""

# Champs TEXTE vides et visibles (input text/email/tel/url + textarea), avec leur meilleure
# étiquette (label, aria-label, placeholder, name…). Sert au pré-remplissage local sans LLM.
JS_CHAMPS_VIDES = r"""
(() => {
  const res = [];
  const parcourir = (racine) => {
    racine.querySelectorAll('input, textarea').forEach(el => {
      const type = (el.type || 'text').toLowerCase();
      if (!['text', 'email', 'tel', 'url', ''].includes(type) && el.tagName !== 'TEXTAREA') return;
      if (el.disabled || el.readOnly || (el.value || '').trim()) return;
      // Barre de RECHERCHE du site (Glassdoor « City, state, zipcode, or remote »,
      // Indeed « Où ? »…) : ce n'est pas un champ de candidature. Y écrire la ville lançait
      // une recherche d'offres et faisait quitter l'offre visée.
      if (el.closest('header, nav, [role="search"], [role="banner"], form[action*="search" i], ' +
                     '[class*="search" i], [id*="search" i]')) return;
      const indices = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'),
                       el.getAttribute('autocomplete')].map(v => (v || '').trim());
      if (indices.some(v => /search|recherch|keyword|mots?[ -]cl[ée]s|zip ?code,? or "?remote|job title|intitul[ée] de poste/i.test(v)
                            || /^(q|l|where|what|quoi|o[uù])$/i.test(v))) return;
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 8) return;   // champ caché
      let etiquette = el.getAttribute('aria-label') || '';
      if (!etiquette && el.id) {
        const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (l) etiquette = l.innerText;
      }
      if (!etiquette) { const l = el.closest('label'); if (l) etiquette = l.innerText; }
      if (!etiquette) etiquette = el.getAttribute('placeholder') || '';
      if (!etiquette) etiquette = el.getAttribute('name') || el.id || '';
      etiquette = etiquette.replace(/\s+/g, ' ').trim().slice(0, 80);
      if (!etiquette) return;
      const idx = el.getAttribute('data-bpy-idx');
      if (idx === null) return;
      // champ à autocomplétion (typeahead) ? il faudra choisir dans la liste de suggestions
      const auto = /combobox|list/.test(el.getAttribute('role') || '')
                   || !!el.getAttribute('aria-autocomplete')
                   || /typeahead|autocomplete|select/i.test(el.className || '');
      res.push({index: parseInt(idx, 10), etiquette, autocomplete: auto});
    });
    racine.querySelectorAll('*').forEach(el => { if (el.shadowRoot) parcourir(el.shadowRoot); });
  };
  parcourir(document);
  return JSON.stringify(res.slice(0, 30));
})()
"""


# Bouton « Se connecter / Continuer avec Google » (SSO). Cliquer dessus utilise la session
# Google DÉJÀ ouverte dans le profil Chrome du robot : AUCUN identifiant ni mot de passe saisi.
JS_BOUTON_GOOGLE = r"""
(() => {
  const res = [];
  const action = /(continue|continuer|sign\s?in|sign\s?up|log\s?in|with google|avec google|via google|using google|se connecter|connexion|s'inscrire|inscription|s'identifier)/i;
  const parcourir = (racine) => {
    racine.querySelectorAll('button, a, [role=button], div[role=button], iframe').forEach(el => {
      const t = ((el.innerText || el.value || '') + ' ' + (el.getAttribute('aria-label') || ''))
                .replace(/\s+/g, ' ').trim();
      if (!t || t.length > 70 || !/google/i.test(t) || !action.test(t)) return;
      if (/(analytics|tag manager|search|maps)/i.test(t)) return;
      const r = el.getBoundingClientRect();
      if (r.width < 20 || r.height < 10) return;
      if (res.includes(t)) return;
      res.push(t);
    });
    racine.querySelectorAll('*').forEach(el => { if (el.shadowRoot) parcourir(el.shadowRoot); });
  };
  parcourir(document);
  return JSON.stringify(res.slice(0, 5));
})()
"""

# Clique le bouton Google détecté (le plus petit élément portant le libellé = le vrai bouton)
JS_CLIQUER_GOOGLE = r"""
(() => {
  const action = /(continue|continuer|sign\s?in|sign\s?up|log\s?in|with google|avec google|via google|using google|se connecter|connexion|s'inscrire|inscription|s'identifier)/i;
  let cible = null, taille = Infinity;
  const parcourir = (racine) => {
    racine.querySelectorAll('button, a, [role=button], div[role=button]').forEach(el => {
      const t = ((el.innerText || el.value || '') + ' ' + (el.getAttribute('aria-label') || ''))
                .replace(/\s+/g, ' ').trim();
      if (!t || t.length > 70 || !/google/i.test(t) || !action.test(t)) return;
      if (/(analytics|tag manager|maps)/i.test(t)) return;
      const r = el.getBoundingClientRect();
      if (r.width < 20 || r.height < 10) return;
      if (r.width * r.height < taille) { taille = r.width * r.height; cible = el; }
    });
    racine.querySelectorAll('*').forEach(el => { if (el.shadowRoot) parcourir(el.shadowRoot); });
  };
  parcourir(document);
  if (!cible) return 'introuvable';
  try { cible.scrollIntoView({block: 'center'}); } catch (e) {}
  cible.click();
  return 'cliqué';
})()
"""

# Adresses proposées sur l'écran « Choisir un compte » de Google.
JS_COMPTES_GOOGLE = r"""
(() => {
  const vis = el => { const r = el.getBoundingClientRect();
    return r.width > 10 && r.height > 10 && r.bottom > 0 && r.top < innerHeight; };
  const res = [];
  const motif = /[\w.+-]+@[\w-]+\.[\w.]+/g;
  document.querySelectorAll('[data-authuser], [data-identifier], div[role=link], li[role=link], a')
    .forEach(el => {
      if (!vis(el)) return;
      const ids = (el.getAttribute('data-identifier') || '') + ' ' + (el.innerText || '');
      (ids.match(motif) || []).forEach(m => { if (!res.includes(m)) res.push(m); });
    });
  return JSON.stringify(res.slice(0, 10));
})()
"""


def js_choisir_compte_google(email: str) -> str:
    """
    Clique la tuile du compte dont l'adresse correspond, sur l'écran « Choisir un compte ».

    On cible une adresse PRÉCISE — contrairement à JS_VALIDER_GOOGLE qui prend le premier
    compte venu. C'est ce qui permet de changer de compte au lieu de reprendre le même.
    """
    return r"""
((email) => {
  const vis = el => { const r = el.getBoundingClientRect();
    return r.width > 10 && r.height > 10 && r.bottom > 0 && r.top < innerHeight; };
  const cherche = String(email || '').trim().toLowerCase();
  if (!cherche) return 'introuvable';
  // Sur l'écran de CONSENTEMENT, Google affiche aussi l'adresse (pastille du compte) : la
  // cliquer RAMÈNE au choix du compte -> boucle choix / consentement sans fin. On ne clique
  // donc un compte que sur le vrai écran « Choisir un compte ».
  if (!(/accountchooser|chooseaccount/i.test(location.href) ||
        /utiliser un autre compte|use another account/i.test(document.body.innerText || '')))
    return 'pas un choix de compte';
  const tuile = [...document.querySelectorAll('[data-identifier]')].filter(vis)
    .find(el => (el.getAttribute('data-identifier') || '').trim().toLowerCase() === cherche);
  if (tuile) { try { tuile.scrollIntoView({block: 'center'}); } catch (e) {} tuile.click(); return 'cliqué'; }
  const candidats = [...document.querySelectorAll(
      '[data-authuser], [data-identifier], div[role=link], li[role=link], a, div[role=button]')]
    .filter(vis);
  const cible = candidats.find(el =>
      ((el.getAttribute('data-identifier') || '') + ' ' + (el.innerText || ''))
      .toLowerCase().includes(cherche));
  if (!cible) return 'introuvable';
  try { cible.scrollIntoView({block: 'center'}); } catch (e) {}
  cible.click();
  return 'cliqué';
})(""" + json.dumps(email) + ")"


# Sur les pages accounts.google.com : choisit le compte déjà connecté, puis valide les écrans
# « Continuer / Autoriser » du flux OAuth. Ne clique jamais « Annuler / Refuser ».
JS_VALIDER_GOOGLE = r"""
(() => {
  const vis = el => { const r = el.getBoundingClientRect();
    return r.width > 10 && r.height > 10 && r.bottom > 0 && r.top < innerHeight; };
  const annuler = /annuler|cancel|refuser|deny|deny|fermer|close|pas maintenant|not now/i;
  // 1) écran « Choisir un compte » : tuile du compte (data-authuser, lien contenant @).
  //    Jamais ailleurs : sur le consentement, la pastille de l'adresse ramène au choix du compte.
  const choix = /accountchooser|chooseaccount/i.test(location.href) ||
                /utiliser un autre compte|use another account/i.test(document.body.innerText || '');
  if (choix) {
    const tuiles = [...document.querySelectorAll('[data-authuser], [data-identifier]')].filter(vis);
    if (tuiles.length) { tuiles[0].click(); return 'compte sélectionné'; }
    const liens = [...document.querySelectorAll('div[role=link], li[role=link], a')].filter(vis);
    const avecEmail = liens.find(e => /@/.test(e.innerText || ''));
    if (avecEmail) { avecEmail.click(); return 'compte sélectionné'; }
  }
  // 2) écran de consentement / confirmation : Continuer, Autoriser, Accepter…
  const boutons = [...document.querySelectorAll('button, [role=button], input[type=submit]')].filter(vis);
  const b = boutons.find(x => {
    const t = (x.innerText || x.value || '').replace(/\s+/g, ' ').trim();
    return t && !annuler.test(t) &&
           /continue|continuer|suivant|next|autoriser|allow|accepter|j'accepte|confirmer|agree/i.test(t);
  });
  if (b) { b.click(); return 'écran Google validé'; }
  return 'aucune action possible';
})()
"""


# Outils communs aux scripts de défilement : recherche dans le shadow DOM + zones qui défilent
_JS_OUTILS_DEFILEMENT = r"""
  const trouver = (racine, sel) => {
    let el = racine.querySelector(sel);
    if (el) return el;
    for (const h of racine.querySelectorAll('*')) { if (h.shadowRoot && (el = trouver(h.shadowRoot, sel))) return el; }
    return null;
  };
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 50 && r.height > 50 && r.bottom > 0 && r.top < innerHeight; };
  const dansModale = el => !!el.closest('[role=dialog],[aria-modal=true],dialog,.artdeco-modal,[class*=modal],[class*=Modal]');
  const zones = [];
  const parcourir = (racine) => racine.querySelectorAll('*').forEach(el => {
    const s = getComputedStyle(el);
    if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 20 && visible(el)) zones.push(el);
    if (el.shadowRoot) parcourir(el.shadowRoot);
  });
  parcourir(document);
  // Priorité à la zone d'une fenêtre superposée, puis à la plus grande
  zones.sort((a, b) => (dansModale(b) - dansModale(a)) || (b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth));
"""

# Défilement intelligent : un élément précis, sinon la zone défilante principale (fenêtre superposée
# en priorité), sinon la page. Paramètres : index (ou null) et sens ('bas' / 'haut').
JS_DEFILER = r"""
((idx, sens) => {
""" + _JS_OUTILS_DEFILEMENT + r"""
  if (idx !== null) {
    const el = trouver(document, '[data-bpy-idx="' + idx + '"]');
    if (el) { el.scrollIntoView({block: 'center'}); return 'élément ' + idx + ' amené au centre de l\'écran'; }
  }
  const pas = sens === 'haut' ? -1 : 1;
  if (zones.length) {
    const z = zones[0], avant = z.scrollTop;
    z.scrollBy(0, pas * z.clientHeight * 0.8);
    const reste = Math.max(0, z.scrollHeight - z.clientHeight - z.scrollTop);
    return (dansModale(z) ? 'fenêtre superposée' : 'zone') + ' défilée de ' + Math.round(z.scrollTop - avant) +
           ' px ; reste en dessous : ' + Math.round(reste) + ' px' + (reste < 5 ? ' (tout en bas)' : '');
  }
  const avant = scrollY;
  window.scrollBy(0, pas * innerHeight * 0.8);
  const reste = Math.max(0, document.documentElement.scrollHeight - innerHeight - scrollY);
  return 'page défilée de ' + Math.round(scrollY - avant) + ' px ; reste en dessous : ' + Math.round(reste) + ' px';
})
"""

# État du défilement, joint à chaque étape : le modèle sait s'il reste du contenu caché
JS_ETAT_DEFILEMENT = r"""
(() => {
""" + _JS_OUTILS_DEFILEMENT + r"""
  const lignes = zones.slice(0, 3).map(z => {
    const reste = Math.max(0, z.scrollHeight - z.clientHeight - z.scrollTop);
    const vu = Math.round(100 * (z.scrollTop + z.clientHeight) / z.scrollHeight);
    return (dansModale(z) ? 'Fenêtre superposée' : 'Zone') + ' défilante : ' + vu + ' % vu, ' +
           (reste < 5 ? 'tout en bas' : Math.round(reste) + ' px encore cachés EN DESSOUS -> action « defiler »');
  });
  const resteP = Math.max(0, document.documentElement.scrollHeight - innerHeight - scrollY);
  lignes.push('Page : ' + (resteP < 5 ? 'tout en bas' : Math.round(resteP) + ' px encore cachés en dessous'));
  return lignes.join('\n');
})()
"""

# Case à cocher / interrupteur : état actuel + point VISIBLE où cliquer avec la vraie souris
# (sur LinkedIn & co., la vraie case est souvent invisible et c'est son <label> qui est affiché)
JS_INFO_CASE = r"""
((idx) => {
""" + _JS_OUTILS_DEFILEMENT + r"""
  const el = trouver(document, '[data-bpy-idx="' + idx + '"]');
  if (!el) return null;
  const etat = e => (e.type === 'checkbox' || e.type === 'radio') ? e.checked : e.getAttribute('aria-checked') === 'true';
  let r = el.getBoundingClientRect();
  const invisible = r.width < 4 || r.height < 4 || getComputedStyle(el).opacity === '0' || getComputedStyle(el).visibility === 'hidden';
  if (invisible) {
    const racine = el.getRootNode();
    const l = (el.id && racine.querySelector('label[for="' + CSS.escape(el.id) + '"]')) || el.closest('label');
    if (l) r = l.getBoundingClientRect();
  }
  return JSON.stringify({etat: etat(el), x: r.left + Math.min(r.width / 2, 12), y: r.top + r.height / 2,
                         visible: r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight});
})
"""

JS_EFFACER_MARQUES = "(() => { const b = document.getElementById('__ja_marques'); if (b) b.remove(); return true; })()"


# Déplie la description d'une offre : descend jusqu'à elle, puis clique les boutons
# « voir plus / … plus / afficher plus / see more » (sauf ceux liés à l'entreprise ou aux offres
# similaires). Renvoie le nombre de boutons cliqués. Fonctionne aussi dans le shadow DOM.
JS_DEPLIER_DESCRIPTION = r"""
(() => {
  let clics = 0;
  const motif = /^(…\s*)?(voir\s+|afficher\s+|lire\s+)?(plus|la suite)$|^see more$|^show more$|^more$/i;
  const exclure = /entreprise|company|offres|infos|jobs|similar|abonn|follow|compétences|skills/i;
  // Un « voir plus » qui déplie du texte ne quitte JAMAIS la page. Depuis l'abonnement Premium,
  // LinkedIn ajoute à la page d'offre des cartes de publication dont le « … plus » est un LIEN
  // vers /feed/update/… : le cliquer emmenait l'extraction sur le fil d'actualité. On saute donc
  // ce qui EST un lien (ou est DANS un lien) menant ailleurs que la page courante. Les liens sans
  // href, en « # » ou en « javascript: » ne naviguent pas : ils restent cliqués comme avant.
  const quitteLaPage = (el) => {
    const a = el.closest ? el.closest('a[href]') : null;
    if (!a) return false;
    const h = a.getAttribute('href') || '';
    if (!h || h.startsWith('#') || /^javascript:/i.test(h)) return false;
    try {
      const u = new URL(a.href, location.href);
      return u.origin !== location.origin || u.pathname !== location.pathname;
    } catch (e) { return false; }
  };
  const parcourir = (r) => {
    r.querySelectorAll('button, [role=button], a').forEach(el => {
      const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
      const aria = el.getAttribute('aria-label') || '';
      if (t.length <= 20 && motif.test(t) && !exclure.test(aria) && !exclure.test(t) && !quitteLaPage(el)) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          try { el.scrollIntoView({block: 'center'}); el.click(); clics++; } catch (e) {}
        }
      }
    });
    r.querySelectorAll('*').forEach(el => { if (el.shadowRoot) parcourir(el.shadowRoot); });
  };
  parcourir(document);
  return clics;
})()
"""


def js_selectionner(index: int, option: str, libelle: str = "") -> str:
    """
    JS qui choisit une option dans un <select>.

    L'index Tappi est souvent PÉRIMÉ au moment d'agir : entre la lecture de la page et
    l'action, il s'écoule l'appel au LLM (1 à 4 s) pendant lequel le site (LinkedIn/React)
    re-rend le champ affichant une erreur, ce qui lui fait perdre son attribut data-bpy-idx.
    On retrouve donc la liste par son libellé, puis par l'option demandée.
    """
    return r"""
((idx, opt, lib) => {
  const selects = [];
  const parcourir = (racine) => {
    racine.querySelectorAll('select').forEach(s => selects.push(s));
    racine.querySelectorAll('*').forEach(e => { if (e.shadowRoot) parcourir(e.shadowRoot); });
  };
  parcourir(document);
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const o = norm(opt);
  const libelleDe = (el) => {
    let t = el.getAttribute('aria-label') || '';
    const ids = el.getAttribute('aria-labelledby');
    if (!t && ids) t = ids.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    if (!t && el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    if (!t) { const p = el.closest('div, fieldset, section'); if (p) t = p.innerText || ''; }
    return norm(t);
  };
  const optionDe = (el) => [...el.options].find(x => norm(x.text) === o || norm(x.value) === o)
                        || [...el.options].find(x => norm(x.text).includes(o));
  const vide = (el) => !el.value
                    || /^(select|choisir|choose|--|none)\b/i.test(((el.selectedOptions || [])[0] || {}).text || '');

  // 1) par index Tappi (shadow DOM compris)
  let el = (window.__bpyDeepQuery && window.__bpyDeepQuery(idx)) || document.querySelector('[data-bpy-idx="' + idx + '"]');
  let via = 'index ' + idx;
  // 2) index périmé -> par le libellé mémorisé à la lecture de la page
  if ((!el || el.tagName !== 'SELECT') && lib) {
    const cle = norm(lib).slice(0, 40);
    el = cle ? selects.find(s => libelleDe(s).includes(cle)) : null;
    via = 'libellé';
  }
  // 3) sinon : l'unique liste encore vide qui propose cette option
  if (!el || el.tagName !== 'SELECT') {
    const cands = selects.filter(s => optionDe(s) && vide(s));
    if (cands.length === 1) { el = cands[0]; via = 'option'; }
  }
  if (!el) return 'élément introuvable (index ' + idx + ' périmé ; libellé et option sans correspondance ; '
                  + selects.length + ' liste(s) sur la page)';
  if (el.tagName !== 'SELECT') return 'ce n\'est pas une liste <select> : utilise cliquer';
  const choix = optionDe(el);
  if (!choix) return 'option absente. Options : ' + [...el.options].map(x => x.text.trim()).slice(0, 30).join(' | ');
  // Amener l'élément à l'écran et le focuser (nécessaire pour React)
  try { el.scrollIntoView({block: 'nearest'}); } catch(e) {}
  try { el.focus(); } catch(e) {}
  // Sélectionner via le setter natif (contourne le wrapper React)
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
  setter.call(el, choix.value);
  choix.selected = true;
  // Séquence complète d'événements pour déclencher les événements synthétiques React
  el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
  el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.dispatchEvent(new FocusEvent('blur', {bubbles: true}));
  const ok = el.value === choix.value;
  return (ok ? 'ok' : 'ATTENTION valeur non confirmée') + ' (trouvé par ' + via + ') : ' + choix.text.trim();
})(""" + json.dumps(index) + ", " + json.dumps(option) + ", " + json.dumps(libelle) + ")"


# Menus déroulants « maison » : Workday, et plus généralement les sites React modernes, n'ont
# PLUS de balise <select>. Ils affichent un bouton qui ouvre une liste role="listbox" dont les
# options sont des role="option". js_selectionner ne peut rien pour eux — il cherche des
# <select> et en trouve zéro — et les options remontent souvent sans libellé exploitable pour
# le modèle. On ouvre donc le menu, on lit le TEXTE des options, et on clique la bonne.
#
# En deux temps, car l'ouverture est asynchrone (React n'affiche la liste qu'au tick suivant) :
# js_menu_aria(... 'ouvrir') puis, après une pause, js_menu_aria_choisir(option).

def js_menu_aria(index: int, libelle: str = "", action: str = "ouvrir") -> str:
    """
    Repère le menu (par index Tappi, sinon par son libellé — l'index est souvent périmé) et,
    selon `action` : 'ouvrir' = l'ouvre s'il est fermé, 'forcer' = clique quoi qu'il arrive,
    'lire' = ne touche à rien. Renvoie {trouve, ouvert, texte}.
    """
    return r"""
((idx, lib, action) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const vis = el => { const r = el.getBoundingClientRect();
                      return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight; };
  const libelleDe = (el) => {
    let t = el.getAttribute('aria-label') || '';
    const ids = el.getAttribute('aria-labelledby');
    if (!t && ids) t = ids.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    if (!t && el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    if (!t) t = el.innerText || '';
    return norm(t);
  };
  // 1) par index Tappi (shadow DOM compris)
  let el = (window.__bpyDeepQuery && window.__bpyDeepQuery(idx)) || document.querySelector('[data-bpy-idx="' + idx + '"]');
  // 2) index périmé -> par le libellé mémorisé à la lecture de la page
  const rendu = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  if (!el && lib) {
    const cle = norm(lib).slice(0, 40);
    // Hors écran compris : la question visée est souvent plus bas dans le formulaire.
    const cands = [...document.querySelectorAll(
      'button, [role=combobox], [role=button], [aria-haspopup=listbox], [aria-expanded]')].filter(rendu);
    el = cle ? cands.find(c => libelleDe(c).includes(cle)) : null;
  }
  if (!el) return JSON.stringify({trouve: false, ouvert: false, texte: ''});
  const texte = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  // Le modèle a pu viser la liste elle-même plutôt que son bouton : elle est alors déjà ouverte.
  if (el.getAttribute('role') === 'listbox' || el.closest('[role=listbox],[role=menu]'))
    return JSON.stringify({trouve: true, ouvert: true, texte: texte});
  // « Ouvert » sûr : aria-expanded, ou la liste que ce bouton déclare piloter (aria-controls).
  const idListe = el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || '';
  const liee = idListe ? document.getElementById(idListe) : null;
  const ouvert = el.getAttribute('aria-expanded') === 'true' || !!(liee && vis(liee));
  if (action === 'ouvrir' || action === 'forcer') {
    // Un seul menu ouvert à la fois : sinon la saisie ou le choix tombe dans le MAUVAIS menu.
    for (const autre of document.querySelectorAll('[aria-expanded="true"][role=combobox], [aria-expanded="true"][aria-haspopup]'))
      if (autre !== el && !autre.contains(el) && !el.contains(autre)) { try { autre.click(); } catch (e) {} }
  }
  if (action === 'forcer' || (action === 'ouvrir' && !ouvert) || (action === 'fermer' && ouvert)) {
    try { el.scrollIntoView({block: 'center'}); el.click(); } catch (e) {}
  }
  const r = el.getBoundingClientRect();
  return JSON.stringify({trouve: true, ouvert: ouvert, texte: texte, popup: idListe,
                         x: r.left + r.width / 2, y: r.top + r.height / 2});
})(""" + json.dumps(index) + ", " + json.dumps(libelle) + ", " + json.dumps(action) + ")"


def js_menu_aria_choisir(option: str, popup: str = "") -> str:
    """
    Clique, dans le menu ouvert, l'option dont le TEXTE correspond (exact, puis partiel).
    Renvoie {ok, choix, options} — les libellés rencontrés servent de compte rendu au modèle
    quand la valeur demandée n'existe pas.

    Les apostrophes sont uniformisées : le site écrit « Ingénieur D’Etat » avec l'apostrophe
    typographique, le modèle tape « D'Etat » avec la droite — sans ça, aucune ne correspond.
    """
    return r"""
((opt, popup) => {
  const norm = s => (s || '').replace(/[’‘`´]/g, "'").replace(/\s+/g, ' ').trim().toLowerCase();
  const o = norm(opt);
  const rendu = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const vis = el => { const r = el.getBoundingClientRect();
                      return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight; };
  const parcourir = (racine, acc) => {
    racine.querySelectorAll('[role=option], [role=menuitem], [role=menuitemcheckbox], [role=menuitemradio], ' +
                            '[role=listbox] li, [role=menu] li')
          .forEach(e => acc.push(e));
    racine.querySelectorAll('*').forEach(e => { if (e.shadowRoot) parcourir(e.shadowRoot, acc); });
    return acc;
  };
  const txt = e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
  // Uniquement dans la liste de CE menu (aria-controls) quand on la connaît : plusieurs menus
  // peuvent être dans la page, et une option homonyme d'un autre menu serait cochée à tort.
  const racine = (popup && document.getElementById(popup)) || document;
  const toutes = parcourir(racine, []).filter(rendu);
  const options = toutes.filter(vis);
  const libelles = toutes.map(txt).filter(t => t);
  // Exact, puis « contient », puis « est contenu dans » (« Portable » pour « Téléphone portable »)
  const chercher = (liste) => liste.find(e => norm(txt(e)) === o)
                           || liste.find(e => norm(txt(e)).includes(o))
                           || liste.find(e => txt(e).length > 1 && o.includes(norm(txt(e))));
  // D'abord ce qui est à l'écran ; à défaut, une option rendue mais hors du cadre visible
  // (on la ramènera dans le champ de vision avant de cliquer).
  const cible = chercher(options) || chercher(toutes);
  if (!cible) return JSON.stringify({ok: false, choix: '', options: libelles.slice(0, 40)});
  const choix = txt(cible);
  try { cible.scrollIntoView({block: 'nearest'}); } catch (e) {}
  // Séquence complète : certains widgets réagissent au mousedown, pas au click seul
  // Liste à CHOIX MULTIPLES : une option déjà cochée le reste (un clic la décocherait).
  const etat = () => cible.getAttribute('aria-checked') || cible.getAttribute('aria-selected');
  if (etat() === 'true')
    return JSON.stringify({ok: true, choix: choix, deja: true, options: libelles.slice(0, 40)});
  cible.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
  cible.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
  try { cible.click(); } catch (e) {}
  if (!cible.id) cible.id = '__opt_' + Date.now();
  return JSON.stringify({ok: true, choix: choix, id: cible.id, options: libelles.slice(0, 40)});
})(""" + json.dumps(option) + ", " + json.dumps(popup) + ")"


def js_menu_aria_defiler(sens: str = "bas") -> str:
    """
    Fait défiler la liste d'options OUVERTE d'un cran ('bas'), ou la remet au début ('haut').

    Indispensable : un menu long (Workday « Diplôme » : une trentaine d'options) n'a qu'une
    FENÊTRE de ses options dans la page. L'option cherchée n'existe tout simplement pas dans
    le DOM tant qu'on n'a pas défilé jusqu'à elle — on la cherchait donc en vain.

    Renvoie {bouge, reste} : `bouge` faux = on est au bout, inutile d'insister.
    """
    return r"""
((sens) => {
  const vis = el => { const r = el.getBoundingClientRect();
                      return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight; };
  const defilante = (el) => {
    const s = getComputedStyle(el);
    return /(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 4;
  };
  // Listes et options cherchées AUSSI dans les shadow DOM (SAP SuccessFactors / UI5,
  // 27/09 — la liste n'était pas trouvée, rien ne défilait et seules 20 options étaient vues).
  const racines = [];
  const ajouter = r => { racines.push(r); for (const e of r.querySelectorAll('*')) if (e.shadowRoot) ajouter(e.shadowRoot); };
  ajouter(document);
  const tous = sel => racines.flatMap(r => [...r.querySelectorAll(sel)]);
  const parent = e => e.parentElement || (e.getRootNode && e.getRootNode().host) || null;
  const zoneDe = (el) => { for (let e = el; e && e !== document.body && e !== document.documentElement; e = parent(e))
                             if (e.nodeType === 1 && defilante(e)) return e;
                           return null; };
  // La zone qui défile est la liste elle-même, ou le premier parent qui défile.
  let zone = null;
  for (const l of tous('[role=listbox], [role=menu]').filter(vis)) {
    zone = zoneDe(l);
    if (zone) break;
  }
  const options = tous('[role=option], [role=menuitem], [role=listbox] li, [role=menu] li').filter(vis);
  if (!zone && options.length) zone = zoneDe(options[options.length - 1]);
  if (!zone) {
    // Aucune zone défilante reconnue : on amène la DERNIÈRE option visible en haut, ce qui fait
    // défiler son conteneur, quel qu'il soit, et charge les suivantes (listes « virtuelles »).
    if (sens === 'haut' || !options.length) return JSON.stringify({bouge: false, reste: 0});
    const derniere = options[options.length - 1];
    const avant = derniere.getBoundingClientRect().top;
    try { derniere.scrollIntoView({block: 'start'}); } catch (e) {}
    return JSON.stringify({bouge: Math.abs(derniere.getBoundingClientRect().top - avant) > 2, reste: -1});
  }
  const avant = zone.scrollTop;
  if (sens === 'haut') zone.scrollTop = 0;
  else zone.scrollBy(0, Math.max(40, zone.clientHeight * 0.8));
  return JSON.stringify({bouge: zone.scrollTop !== avant,
                         reste: Math.round(Math.max(0, zone.scrollHeight - zone.clientHeight - zone.scrollTop))});
})(""" + json.dumps(sens) + ")"


class Navigateur:
    """Enveloppe autour de tappi.Browser : lancement, navigation, lecture de page."""

    def __init__(self, config: dict, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("job_automation")
        cfg = config.get("navigateur", {})
        self.cdp_url = cfg.get("cdp_url", "http://127.0.0.1:9222")
        self.lancer_auto = bool(cfg.get("lancer_automatiquement", True))
        self.profil = str(chemin_projet(cfg.get("profil_chrome", "~/.tappi/job-automation")))
        # Navigateurs de SECOURS : des Chrome complets et indépendants, chacun déjà connecté
        # au compte de candidature ET à un compte de chat différent. Quand le chat du
        # navigateur courant est saturé, on bascule sur le suivant — aucune déconnexion,
        # aucune reconnexion : tout y est déjà prêt. Liste vide = comportement d'avant.
        self.navigateurs = [{"profil": self.profil, "cdp_url": self.cdp_url}]
        for secours in (cfg.get("navigateurs_secours") or []):
            port = int(secours.get("port") or 0)
            if not secours.get("profil") or not port:
                continue
            self.navigateurs.append({
                "profil": str(chemin_projet(secours["profil"])),
                "cdp_url": f"http://127.0.0.1:{port}",
            })
        self.index_navigateur = 0
        # Adresse AVEC LAQUELLE POSTULER. Sert à viser le bon compte quand un site propose
        # « Se connecter avec Google » et que le profil en a plusieurs (voir
        # connecter_avec_google). Renseignée par l'orchestrateur depuis profile.yaml.
        self.email_candidature = ""
        self.chemin_chrome = cfg.get("chemin_chrome") or None
        self.headless = bool(cfg.get("headless", False))
        self.delai = float(cfg.get("delai_chargement_secondes", 5))
        # Délai court après une action DANS un formulaire (clic, saisie) : bien plus petit que
        # le délai de chargement d'une page, pour un remplissage fluide et rapide.
        self.delai_action = float(cfg.get("delai_action_secondes", 0.6))
        # NopeCHA : extension de résolution de CAPTCHA chargée dans le Chrome du robot. Elle
        # repère et résout seule reCAPTCHA / hCaptcha / Turnstile ; le robot n'a qu'à attendre.
        cfg_np = config.get("nopecha", {}) or {}
        self.nopecha_actif = bool(cfg_np.get("actif", False))
        self.nopecha_dossier = str(chemin_projet(cfg_np.get("dossier_extension", "extensions/nopecha")))
        self.nopecha_attente = float(cfg_np.get("attente_resolution_secondes", 90))
        self.b = Browser(self.cdp_url)
        self._onglet_epingle: str | None = None
        # Onglets MIS DE CÔTÉ : une page qu'un geste humain seul peut débloquer (CAPTCHA,
        # connexion) est laissée telle quelle pour que l'utilisateur la règle. Ils ne sont ni
        # refermés ni réutilisés. Cette liste ne se remplit qu'en mode « postuler à tout » ;
        # vide, tout le reste du code se comporte exactement comme avant.
        self._onglets_preserves: list[str] = []
        # Onglets « Action manuelle » de CHAQUE navigateur (index -> ids) : retrouvés quand la
        # rotation revient sur ce navigateur, au lieu d'être refermés par onglet_unique().
        self._preserves_par_nav: dict[int, list[str]] = {}
        self.MAX_ONGLETS_PRESERVES = 8
        self._installer_epinglage()
        # Surveillant des fenêtres JavaScript des sites (voir GardeAlertes) : lancé au démarrage.
        self._garde: GardeAlertes | None = None

    # -- onglet actif ---------------------------------------------------------

    def _installer_epinglage(self) -> None:
        """
        Permet de FIXER l'onglet sur lequel travaillent toutes les opérations.

        Par défaut, Tappi considère comme « onglet courant » le premier de /json/list, qui est
        l'onglet le plus RÉCEMMENT créé — et ni Page.bringToFront ni /json/activate ne changent
        cet ordre (vérifié). Dès qu'un chat (Gemini, DeepSeek) était ouvert à côté, il devenait
        donc l'onglet courant : le robot lisait la page du chat au lieu de celle de l'offre.
        On surcharge la résolution de l'onglet courant pour viser explicitement celui qu'on veut.
        """
        resolution_origine = self.b._current_target

        def onglet_courant():
            if self._onglet_epingle:
                for page in self.b._get_pages():
                    if page.get("id") == self._onglet_epingle:
                        return page
                self._onglet_epingle = None    # l'onglet épinglé a été fermé
            return resolution_origine()

        self.b._current_target = onglet_courant

    def epingler_onglet_courant(self) -> str | None:
        """Fixe l'onglet actuellement visé comme cible de toutes les opérations."""
        try:
            self._onglet_epingle = None
            self._onglet_epingle = str(self.b._current_target().get("id") or "") or None
        except Exception:  # noqa: BLE001
            self._onglet_epingle = None
        return self._onglet_epingle

    def ouvrir_onglet_assistant(self, url: str) -> str:
        """
        Ouvre `url` dans un NOUVEL ONGLET de la même fenêtre et renvoie son id.

        L'id est indispensable : c'est lui qui permet d'épingler l'onglet (voir `epingler`),
        donc de garantir que chaque commande vise le bon — le chat pour le chat, le site
        d'emploi pour les clics et les captures.
        """
        cdp = self.b._connect_browser()
        try:
            resultat = cdp.send("Target.createTarget", url=url)
        finally:
            cdp.close()
        return str(resultat.get("targetId") or "")

    def epingler(self, id_onglet: str | None, afficher: bool = True) -> str | None:
        """
        Vise explicitement cet onglet (None = retour au comportement par défaut) et, par
        défaut, l'amène au PREMIER PLAN.

        L'épinglage seul suffirait au robot, mais l'écran resterait figé sur le chat : on ne
        verrait jamais ce qui se passe sur le site d'emploi. Afficher l'onglet visé rend le
        déroulement observable — et permet de reprendre la main à tout moment.
        """
        precedent = self._onglet_epingle
        self._onglet_epingle = id_onglet or None
        if afficher and self._onglet_epingle:
            try:
                cdp = self.b._connect_page(self._onglet_epingle)
                try:
                    cdp.send("Page.bringToFront")
                finally:
                    cdp.close()
            except Exception:  # noqa: BLE001 — l'affichage est un confort, jamais bloquant
                pass
        return precedent

    # -- cycle de vie -------------------------------------------------------

    def demarrer(self, premier_plan: bool | None = None) -> None:
        """
        Se connecte à Chrome ; le lance avec le profil dédié si nécessaire.
        premier_plan : True = fenêtre agrandie et mise devant ; False = jamais ;
        None (défaut) = seulement si Chrome vient d'être lancé.
        """
        lance = False
        try:
            self.b.tabs()
            self.logger.info(f"Connecté à Chrome ({self.cdp_url}).")
        except BrowserNotRunning:
            if not self.lancer_auto:
                raise
            port = int(self.cdp_url.rsplit(":", 1)[-1].split("/")[0])
            self.logger.info(f"Lancement de Chrome (port {port}, profil {self.profil})…")
            if self.nopecha_actif:
                self._lancer_chrome_avec_nopecha(port)
            else:
                Browser.launch(port=port, user_data_dir=self.profil, headless=self.headless,
                               chrome_path=self.chemin_chrome)
            time.sleep(2)
            lance = True
        if not self.b.tabs():
            self.b.newtab()
            time.sleep(1)
        if premier_plan or (premier_plan is None and lance):
            self._fenetre_au_premier_plan(self.b)
        if self._garde is None:
            self._garde = GardeAlertes(self)
            self._garde.start()
        if self.nopecha_actif:
            if self.nopecha_chargee():
                self.logger.info("NopeCHA : extension active dans ce Chrome.")
            else:
                self.logger.warning(
                    "NopeCHA : extension NON chargée dans ce Chrome. Chrome officiel ignore "
                    "--load-extension : charge-la une fois à la main dans ce profil — "
                    "chrome://extensions > Mode développeur > « Charger l'extension non empaquetée » "
                    f"> {self.nopecha_dossier} (elle y reste ensuite).")

    def alertes_fermees(self) -> list[str]:
        """Messages des fenêtres JavaScript fermées automatiquement depuis le dernier appel."""
        return self._garde.alertes_fermees() if self._garde else []

    # -- NopeCHA ----------------------------------------------------------------

    # ID fixe de NopeCHA (dérivé de la clé « key » de son manifest.json).
    NOPECHA_ID = "dknlfmjaanfblgfdfebhijalfmhmjjjo"

    def _lancer_chrome_avec_nopecha(self, port: int) -> None:
        """
        Comme Browser.launch de Tappi, avec l'extension NopeCHA en plus. --load-extension est
        pris en compte par Chromium / Chrome for Testing ; Chrome officiel (v137+) l'ignore,
        d'où le chargement manuel unique dans le profil (voir demarrer).
        """
        chrome = self.chemin_chrome or _find_chrome()
        if not chrome:
            raise FileNotFoundError("Chrome introuvable : renseigne navigateur.chemin_chrome dans config.yaml.")
        os.makedirs(self.profil, exist_ok=True)
        cmd = [chrome, f"--remote-debugging-port={port}", f"--user-data-dir={self.profil}",
               f"--load-extension={self.nopecha_dossier}",
               "--disable-features=DisableLoadExtensionCommandLineSwitch", "--start-maximized"]
        if self.headless:
            cmd.append("--headless=new")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fin = time.monotonic() + 10
        while time.monotonic() < fin:
            try:
                urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        raise BrowserNotRunning(f"Chrome ne répond pas sur le port {port}.")

    def nopecha_chargee(self) -> bool:
        """
        NopeCHA est-elle active dans ce Chrome ? Son service worker s'endort après ~30 s
        d'inactivité (et se réveille seul au premier CAPTCHA) : s'il n'est pas visible, on
        regarde si l'extension est installée et activée dans le profil.
        """
        try:
            cdp = self.b._connect_browser()
            try:
                cibles = cdp.send("Target.getTargets").get("targetInfos", [])
            finally:
                cdp.close()
            if any(f"chrome-extension://{self.NOPECHA_ID}/" in (c.get("url") or "") for c in cibles):
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            prefs = json.loads((Path(self.profil) / "Default" / "Secure Preferences")
                               .read_text(encoding="utf-8"))
            ext = prefs.get("extensions", {}).get("settings", {}).get(self.NOPECHA_ID)
            return bool(ext) and not ext.get("disable_reasons")
        except (OSError, ValueError):
            return False

    def attendre_nopecha(self, delai_max: float | None = None) -> str:
        """
        Laisse NopeCHA résoudre le CAPTCHA de la page : attend que le widget ait reçu sa
        réponse ou que le CAPTCHA / la page anti-robot disparaisse.
        Renvoie « validée », « disparue » ou « pas résolu ».
        """
        fin = time.monotonic() + (self.nopecha_attente if delai_max is None else delai_max)
        while time.monotonic() < fin:
            time.sleep(0.5)
            try:
                if not self.captcha_visible():
                    if not self.page_anti_robot():
                        return "disparue"
                elif self.b.eval(JS_CAPTCHA_A_RESOUDRE) is False:
                    return "validée"
            except CDPError:
                continue   # la page change (souvent : la vérification est passée)
        return "pas résolu"

    def basculer_navigateur(self, fermer_ancien: bool = False) -> bool:
        """
        Passe au navigateur SUIVANT : un autre Chrome, avec son propre dossier de profil et
        son propre port de pilotage.

        Chacun est un poste de travail complet et déjà prêt — compte de candidature connecté,
        et un compte de chat différent. On ne se déconnecte de rien, on ne saisit aucun mot de
        passe : on change de fenêtre, c'est tout. C'est ce qui rend la bascule fiable, là où
        dérouler « Déconnexion » dans l'interface d'un site ne l'est jamais.

        La rotation est CIRCULAIRE : après le dernier, on revient au premier. Le robot ne
        quitte un navigateur que quand son compte est saturé ; quand il y revient, il a
        travaillé tout le temps des autres comptes entre-temps — assez, en général, pour que
        la limite se soit levée. Le cas où TOUS sont saturés en même temps (limite par IP,
        serveur DeepSeek surchargé) est traité par l'orchestrateur, qui met alors le robot
        en pause au lieu de tourner à vide.

        Renvoie False s'il n'y a aucun autre navigateur vers lequel basculer.
        """
        if len(self.navigateurs) <= 1:
            self.logger.error("Aucun navigateur de secours configuré : bascule impossible.")
            return False
        # L'ancien navigateur, avant de le quitter : ses onglets « Action manuelle » sont gardés.
        ancien_b, ancien_index = self.b, self.index_navigateur
        ancien_preserves = list(self._onglets_preserves)
        self._preserves_par_nav[ancien_index] = ancien_preserves
        self.index_navigateur = (self.index_navigateur + 1) % len(self.navigateurs)
        suivant = self.navigateurs[self.index_navigateur]
        retour = " (retour au premier, tour complet)" if self.index_navigateur == 0 else ""
        self.logger.warning(f"Bascule sur le navigateur {self.index_navigateur + 1}/"
                            f"{len(self.navigateurs)}{retour} : profil {suivant['profil']} "
                            f"({suivant['cdp_url']}).")
        self.cdp_url = suivant["cdp_url"]
        self.profil = suivant["profil"]
        # Nouveau navigateur = nouvel espace d'onglets : tout ce qui identifiait les anciens
        # (onglet épinglé, onglets mis de côté) ne veut plus rien dire ici.
        self.b = Browser(self.cdp_url)
        self._onglet_epingle = None
        self._onglets_preserves = list(self._preserves_par_nav.get(self.index_navigateur, []))
        self._installer_epinglage()
        try:
            self.demarrer(premier_plan=False)   # mis devant plus bas, après avoir rangé l'ancien
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Navigateur {self.index_navigateur + 1} injoignable : {e}")
            return False
        self._ranger_ancien_navigateur(ancien_b, ancien_index, ancien_preserves, fermer_ancien)
        self._fenetre_au_premier_plan(self.b)
        return True

    def _fenetres(self, b) -> dict[int, str]:
        """{windowId: targetId} des fenêtres d'un navigateur (une entrée par fenêtre)."""
        fenetres: dict[int, str] = {}
        cdp = b._connect_browser()
        try:
            for t in b.tabs():
                try:
                    w = cdp.send("Browser.getWindowForTarget", targetId=t.id)
                    fenetres.setdefault(int(w["windowId"]), t.id)
                except Exception:  # noqa: BLE001
                    continue
        finally:
            cdp.close()
        return fenetres

    def _ranger_ancien_navigateur(self, b, index: int, preserves: list[str], fermer: bool) -> None:
        """
        Après une bascule : FERME l'ancien navigateur si on rejoue la même offre ailleurs et
        qu'il ne garde aucun onglet « Action manuelle » ; sinon le RÉDUIT (ses onglets restent
        intacts pour toi, et la rotation le retrouvera tel quel). Ne bloque jamais la bascule.
        """
        try:
            if fermer and not preserves:
                cdp = b._connect_browser()
                try:
                    cdp.send("Browser.close")
                except Exception:  # noqa: BLE001 — la connexion se coupe quand Chrome se ferme
                    pass
                finally:
                    cdp.close()
                self._preserves_par_nav.pop(index, None)
                self.logger.info(f"Navigateur {index + 1} fermé (l'offre est rejouée sur le suivant ; "
                                 f"il sera relancé quand la rotation reviendra dessus).")
                return
            cdp = b._connect_browser()
            try:
                # Seuls les onglets « Action manuelle » méritent de rester : la page de l'offre
                # abandonnée et les conversations DeepSeek de l'ancien compte ne servent plus
                # (l'offre est rejouée ailleurs). Ils s'accumulaient à chaque bascule.
                fermes = 0
                if preserves:
                    for t in b.tabs():
                        if getattr(t, "id", "") and t.id not in preserves:
                            try:
                                cdp.send("Target.closeTarget", targetId=t.id)
                                fermes += 1
                            except Exception:  # noqa: BLE001 — déjà fermé
                                pass
                for id_fenetre in self._fenetres(b):
                    cdp.send("Browser.setWindowBounds", windowId=id_fenetre,
                             bounds={"windowState": "minimized"})
            finally:
                cdp.close()
            garde = f", {len(preserves)} onglet(s) « Action manuelle » gardé(s)" if preserves else ""
            if fermes:
                garde += f", {fermes} onglet(s) de travail refermé(s)"
            self.logger.info(f"Navigateur {index + 1} réduit dans la barre des tâches{garde}.")
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Ancien navigateur non rangé : {e}")

    def _fenetre_au_premier_plan(self, b) -> None:
        """
        Met la fenêtre du navigateur actif DEVANT les autres, AGRANDIE. Page.bringToFront ne
        fait que choisir l'onglet ; Windows empêche un programme de passer au premier plan de
        lui-même. Réduire puis réafficher la fenêtre contourne le plus souvent cette protection.
        """
        try:
            cdp = b._connect_browser()
            try:
                for id_fenetre, id_onglet in self._fenetres(b).items():
                    cdp.send("Browser.setWindowBounds", windowId=id_fenetre, bounds={"windowState": "minimized"})
                    time.sleep(0.3)
                    # Chrome refuse « réduit » → « agrandi » d'un coup : passage obligé par « normal ».
                    cdp.send("Browser.setWindowBounds", windowId=id_fenetre, bounds={"windowState": "normal"})
                    cdp.send("Browser.setWindowBounds", windowId=id_fenetre, bounds={"windowState": "maximized"})
                    page = b._connect_page(id_onglet)
                    try:
                        page.send("Page.bringToFront")
                    finally:
                        page.close()
            finally:
                cdp.close()
        except Exception as e:  # noqa: BLE001 — l'affichage est un confort, jamais bloquant
            self.logger.debug(f"Fenêtre non mise au premier plan : {e}")

    def preserver_onglet_courant(self) -> str | None:
        """
        Laisse l'onglet courant EN L'ÉTAT : il ne sera plus refermé ni réutilisé pour l'offre
        suivante. Sert au mode « postuler à tout » quand seul un geste humain peut débloquer
        la page (CAPTCHA, connexion) : l'utilisateur la retrouve intacte, plus tard.

        Au-delà de MAX_ONGLETS_PRESERVES, le plus ancien est refermé : une nuit de
        candidatures laisserait sinon des dizaines d'onglets ouverts.
        """
        try:
            id_onglet = str((self.b._current_target() or {}).get("id") or "") or None
        except Exception:  # noqa: BLE001 — ne doit jamais interrompre une candidature
            id_onglet = None
        if not id_onglet or id_onglet in self._onglets_preserves:
            return id_onglet
        self._onglets_preserves.append(id_onglet)
        while len(self._onglets_preserves) > self.MAX_ONGLETS_PRESERVES:
            ancien = self._onglets_preserves.pop(0)
            self.logger.info(f"Onglets mis de côté : plafond de {self.MAX_ONGLETS_PRESERVES} "
                             f"atteint -> fermeture du plus ancien.")
            self._fermer_onglet(ancien)
        self.logger.info(f"Onglet laissé ouvert pour toi ({len(self._onglets_preserves)}/"
                         f"{self.MAX_ONGLETS_PRESERVES}) : {self.url()[:90]}")
        return id_onglet

    def _fermer_onglet(self, id_onglet: str) -> None:
        """Ferme l'onglet d'id donné (par son index courant : les index se décalent)."""
        try:
            for t in self.b.tabs():
                if getattr(t, "id", "") == id_onglet:
                    self.b.close_tab(t.index)
                    return
        except Exception:  # noqa: BLE001 — un onglet déjà fermé n'est pas une erreur
            pass

    def onglet_unique(self) -> None:
        """
        Ferme les onglets en trop pour repartir d'un seul onglet propre.

        Les onglets mis de côté (voir `preserver_onglet_courant`) sont épargnés : ils attendent
        l'utilisateur. Tant qu'il n'y en a aucun — le cas normal, et tout le mode 4 — le
        déroulé ci-dessous est exactement celui d'avant.
        """
        onglets = self.b.tabs()
        if not onglets:
            self.b.newtab()
            time.sleep(1)
            return
        # Un onglet préservé que l'utilisateur a déjà refermé lui-même n'a plus à être suivi.
        ouverts = {getattr(t, "id", "") for t in onglets}
        self._onglets_preserves = [i for i in self._onglets_preserves if i in ouverts]

        if not self._onglets_preserves:
            # On garde un onglet qui n'est PAS un chat (DeepSeek / Gemini). Chrome liste les
            # onglets du plus récemment affiché au plus ancien : « garder le n° 0 » gardait
            # souvent l'onglet du chat, qui devenait alors celui de l'offre — puis le chat le
            # ramenait sur sa conversation, effaçant la page de candidature.
            assistants = ("deepseek.com", "gemini.google.com")
            garde = next((t for t in onglets
                          if not any(h in str(getattr(t, "url", "")) for h in assistants)), None)
            for t in sorted(onglets, key=lambda x: x.index, reverse=True):
                if garde is not None and t.id == garde.id:
                    continue
                try:
                    self.b.close_tab(t.index)
                except CDPError:
                    pass
            if garde is None:        # il n'y avait que des chats : on repart d'un onglet neuf
                id_garde = self.ouvrir_onglet_assistant("about:blank")
                time.sleep(0.8)
            else:
                id_garde = garde.id
            # Épinglé : toutes les commandes visent CET onglet, quel que soit l'ordre de Chrome.
            self.epingler(id_garde or None)
            return

        # Des onglets attendent un geste humain : on ferme tout le reste et on repart d'un
        # onglet NEUF, au lieu de réutiliser le premier — qui est peut-être l'un d'eux.
        for t in sorted(onglets, key=lambda x: x.index, reverse=True):
            if getattr(t, "id", "") in self._onglets_preserves:
                continue
            try:
                self.b.close_tab(t.index)
            except CDPError:
                pass
        nouveau = self.ouvrir_onglet_assistant("about:blank")
        time.sleep(0.8)
        self.epingler(nouveau or None)

    # -- navigation / lecture -------------------------------------------------

    def aller(self, url: str) -> None:
        self.b.open(url)          # attend l'événement « load » (10 s max)
        # Cet onglet est celui de l'offre : on l'épingle pour que l'ouverture d'un chat à côté
        # (Gemini, DeepSeek) ne détourne pas les lectures et les clics vers le chat.
        self.epingler_onglet_courant()
        self.attendre_page()      # attend que le contenu soit réellement là (pas un délai fixe)
        self.neutraliser_onglets()

    def attendre_page(self, delai_max: float | None = None) -> None:
        """Attente INTELLIGENTE après une navigation : dès que la page a du contenu, on continue
        (au lieu d'attendre bêtement N secondes). Si rien n'apparaît, on attend quand même
        `delai_max` (sites lents). Bien plus rapide qu'un time.sleep fixe sur les sites rapides."""
        delai_max = self.delai if delai_max is None else delai_max
        fin = time.monotonic() + delai_max
        while time.monotonic() < fin:
            try:
                pret = self.b.eval(
                    "document.readyState === 'complete' && document.body "
                    "&& (document.body.innerText || '').trim().length > 80")
            except CDPError:
                pret = False
            if pret:
                time.sleep(0.3)   # stabilisation du JavaScript de la page
                return
            time.sleep(0.25)

    def attendre_stabilite(self, max_secondes: float = 3.0) -> None:
        """
        Attend que la page cesse de bouger après une action (modale qui s'ouvre, contenu qui
        se charge), au lieu d'un délai fixe.

        Un simple `sleep(0.4)` après un clic ne suffisait pas : la capture partait AVANT que
        la fenêtre « Candidature simplifiée » soit affichée, et le modèle concluait que le
        clic n'avait rien fait — il recliquait ou naviguait à la main. Ici on rend la main dès
        que le DOM est figé, donc sans ralentir les pages rapides.
        """
        fin = time.monotonic() + max_secondes
        taille_precedente, fige_depuis = -1, time.monotonic()
        while time.monotonic() < fin:
            try:
                taille = int(self.b.eval(
                    "document.body ? document.body.innerHTML.length : 0") or 0)
            except CDPError:
                return
            if taille != taille_precedente:
                taille_precedente, fige_depuis = taille, time.monotonic()
            elif time.monotonic() - fige_depuis >= 0.4:
                return
            time.sleep(0.2)

    def _signature_page(self) -> dict:
        """État léger de la page : adresse, nombre d'éléments cliquables visibles, texte."""
        etat = self._eval_json(JS_SIGNATURE_PAGE, {})
        return etat if isinstance(etat, dict) and "n" in etat else {}

    def attendre_page_prete(self, delai_max: float = 30.0, avant: dict | None = None) -> str:
        """
        Attend que la page soit UTILISABLE avant de la montrer au modèle : chargée, avec des
        boutons ou des champs, et stable depuis ~2 s. Rend la main dès que c'est le cas —
        3 s si le site est rapide, jusqu'à `delai_max` s'il est lent.

        `avant` (signature prise avant un clic) : on attend d'abord que la page CHANGE, sinon
        l'ancienne page, encore affichée, passerait pour « prête ».

        Renvoie « prête », « inchangée » (le clic n'a rien fait) ou « bloquée » (toujours
        aucun élément utilisable au bout du délai : redirection qui ne se termine pas).
        """
        def cle(e: dict) -> tuple:
            return e.get("url"), e.get("n"), int(e.get("t", 0)) // 200

        fin = time.monotonic() + delai_max
        changee = avant is None
        precedente, stable = None, 0
        etat = {}
        while time.monotonic() < fin:
            time.sleep(1.0)
            try:
                etat = self._signature_page()
            except Exception:  # noqa: BLE001 — navigation en cours
                etat = {}
            if not etat:
                precedente, stable = None, 0
                continue
            if not changee:
                changee = cle(etat) != cle(avant or {})
                if not changee:
                    continue
            stable = stable + 1 if cle(etat) == precedente else 0
            precedente = cle(etat)
            if etat.get("pret") and etat.get("n", 0) > 0 and stable >= 2:
                return "prête"
        if not changee:
            return "inchangée"
        return "bloquée" if not etat or etat.get("n", 0) == 0 else "prête"

    def attendre_case_validee(self, delai_max: float = 20.0) -> str:
        """
        Après le clic sur la case anti-robot : attend qu'elle soit VALIDÉE (le widget a reçu
        sa réponse) ou que la page anti-robot disparaisse, jusqu'à `delai_max`.
        Renvoie « validée », « disparue » ou « pas validée ».
        """
        fin = time.monotonic() + delai_max
        while time.monotonic() < fin:
            time.sleep(1.0)
            try:
                if self.b.eval(JS_CASE_VALIDEE) is True:
                    return "validée"
            except CDPError:
                continue   # la page change (souvent : la vérification est passée)
            if not self.page_anti_robot() and not self.captcha_visible():
                return "disparue"
        return "pas validée"

    def neutraliser_onglets(self) -> None:
        try:
            self.b.eval(JS_NEUTRALISER_ONGLETS)
        except CDPError:
            pass

    def url(self) -> str:
        return self.b.url()

    def titre(self) -> str:
        try:
            return str(self.b.eval("document.title") or "")
        except CDPError:
            return ""

    def deplier_description(self, motifs_attendus: list[str] | None = None) -> int:
        """
        Descend progressivement dans la page pour forcer le chargement paresseux de la
        description (LinkedIn, WTTJ…), clique les boutons « voir plus / … plus » à chaque
        palier, et s'arrête quand le contenu attendu est visible. Renvoie le nb de clics.

        Filet de sécurité : si malgré tout un clic fait quitter l'offre (bouton qui navigue en
        JavaScript, donc sans href repérable côté page), on revient sur l'offre et on arrête de
        déplier — jamais d'extraction sur une autre page que celle demandée.
        """
        total = 0
        # Comparaison sans « ? », « # » ni « / » final : les sites réécrivent souvent l'adresse
        # (paramètres de suivi) sans changer de page ; seul un vrai changement de page compte.
        def cle(u: str) -> str:
            return (u or "").split("#")[0].split("?")[0].rstrip("/")

        try:
            url_depart = self.url()
        except CDPError:
            url_depart = ""
        try:
            for palier in range(5):
                self.b.eval(f"window.scrollTo(0, {int((palier + 1) * 900)})")
                time.sleep(0.4)
                clics = int(self.b.eval(JS_DEPLIER_DESCRIPTION) or 0)
                if clics:
                    total += clics
                    time.sleep(0.8)   # laisse le texte complet s'afficher / se charger
                if url_depart and cle(self.url()) != cle(url_depart):
                    self.logger.warning(f"Dépliage : un clic a quitté l'offre ({self.url()}) ; "
                                        f"retour sur {url_depart} et arrêt du dépliage.")
                    self.b.open(url_depart)
                    self.attendre_page()
                    break
                # Arrêt anticipé si la description attendue est déjà chargée
                if motifs_attendus:
                    txt = str(self.b.eval("document.body ? document.body.innerText : ''") or "").lower()
                    if any(m.lower() in txt for m in motifs_attendus):
                        break
            self.b.eval("window.scrollTo(0, 0)")
            time.sleep(0.3)
        except CDPError:
            pass
        return total

    def texte_complet(self, max_caracteres: int = 20000) -> str:
        """Tout le texte visible de la page (pour extraire l'offre)."""
        try:
            texte = self.b.eval("document.body ? document.body.innerText : ''") or ""
        except CDPError:
            texte = self.b.text()
        return str(texte)[:max_caracteres]

    def texte_court(self, max_caracteres: int = 5000) -> str:
        return str(self.b.text() or "")[:max_caracteres]

    def elements(self, max_elements: int = 300) -> list[str]:
        """Éléments interactifs numérotés : « [12] (input:text) Nom ». Réindexe la page."""
        for js in (JS_NOMMER_LISTES,   # sinon Tappi ne numérote qu'une liste « Select an option »
                   JS_NOMMER_CASES):   # sinon « Oui » et « Non » d'une question sont vus comme doublons
            try:
                self.b.eval(js)
            except CDPError:
                pass
        return [str(e) for e in self.b.elements()[:max_elements]]

    def options_menus(self) -> dict[str, dict]:
        """{numéro: {options: [...], multiple: bool}} des listes « maison » (voir JS_OPTIONS_MENUS)."""
        res = self._eval_json(JS_OPTIONS_MENUS, {})
        return res if isinstance(res, dict) else {}

    def _eval_json(self, js: str, defaut):
        try:
            brut = self.b.eval(js)
            return json.loads(brut) if isinstance(brut, str) else (brut if brut is not None else defaut)
        except (CDPError, ValueError):
            return defaut

    def champs_fichier(self) -> list[dict]:
        return self._eval_json(JS_CHAMPS_FICHIER, [])

    def libelles(self) -> dict:
        """{index (str): libellé lisible} pour les éléments indexés par le dernier elements()."""
        return self._eval_json(JS_LIBELLES, {})

    def contextes_elements(self) -> dict:
        """{index (str): texte autour} pour les cases/radios indexées (consentement, alerte…)."""
        return self._eval_json(JS_CONTEXTES, {})

    # Un sélecteur d'indicatif pays est-il affiché ? (liste « Morocco (+212) », « Country code »…)
    JS_SELECTEUR_INDICATIF = r"""
    (() => {
      const etiquette = (el) => {
        let t = el.getAttribute('aria-label') || '';
        if (!t && el.id) {
          const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (l) t = l.innerText || '';
        }
        if (!t) { const l = el.closest('label'); if (l) t = l.innerText || ''; }
        return t;
      };
      const liste = [...document.querySelectorAll('select')].some(s => {
        if (s.getBoundingClientRect().height < 5) return false;
        const t = etiquette(s);
        if (/country code|indicatif|code pays|dialing/i.test(t)) return true;
        return [...s.options].slice(0, 40).some(o => /\(\+\d{1,4}\)|^\+\d{1,4}\b/.test(o.text || ''));
      });
      if (liste) return true;
      // Menu « maison » (bouton « France (+33) », pastille « +212 »…) : vu le 27/09.
      // Texte STRICT : un pays éventuel + un indicatif, rien d'autre (jamais un vrai numéro).
      const code = /^\s*(?:[A-Za-zÀ-ÿ .,'()-]{2,40}?\s*)?\(?\+\d{1,4}\)?\s*$/;
      return [...document.querySelectorAll('button, [role=combobox], [role=button], [aria-haspopup], ' +
          '[class*=country i], [class*=dial i], [class*=flag i], [class*=iti]')].some(el => {
        if (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return false;
        const r = el.getBoundingClientRect();
        if (r.height < 5 || r.width < 5) return false;
        const t = ((el.innerText || '') || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
        return t.length <= 45 && code.test(t);
      });
    })()
    """

    # Indicatif affiché À CÔTÉ du champ téléphone n° idx (menu « France (+33) », liste native,
    # pastille « +212 »…), ou "" s'il n'y en a pas. Jamais la valeur du champ lui-même.
    JS_INDICATIF_DU_CHAMP = r"""
    ((idx) => {
      const racines = [];
      const ajouter = r => { racines.push(r); for (const e of r.querySelectorAll('*')) {
        if (e.shadowRoot) ajouter(e.shadowRoot);
        if (e.tagName === 'IFRAME') { try { if (e.contentDocument) ajouter(e.contentDocument); } catch (x) {} } } };
      ajouter(document);
      let champ = null;
      for (const r of racines) { champ = r.querySelector('[data-bpy-idx="' + idx + '"]'); if (champ) break; }
      if (!champ) return '';
      const code = /^\s*(?:[A-Za-zÀ-ÿ .,'()-]{2,40}?\s*)?\(?(\+\d{1,4})\)?\s*$/;
      let p = champ.parentElement;
      // Jusqu'au formulaire compris, jamais la page entière (un autre numéro y traînerait).
      for (let i = 0; i < 4 && p && !/^(BODY|HTML)$/.test(p.tagName); i++, p = p.parentElement) {
        for (const s of p.querySelectorAll('select')) {
          const o = s.options[s.selectedIndex];
          const m = o && (o.text || '').match(/\(?(\+\d{1,4})\)?/);
          if (m) return m[1];
        }
        for (const el of p.querySelectorAll('button, [role=combobox], [role=button], [aria-haspopup], div, span')) {
          if (el === champ || el.contains(champ) || champ.contains(el)) continue;
          const t = ((el.innerText || '') || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
          const m = t.length <= 45 ? t.match(code) : null;
          if (m) return m[1];
        }
      }
      return '';
    })"""

    def indicatif_du_telephone(self, index: int) -> str:
        """Indicatif (« +33 ») affiché par le sélecteur à côté du champ téléphone, ou ""."""
        try:
            return str(self.b.eval(f"({self.JS_INDICATIF_DU_CHAMP})({json.dumps(str(int(index)))})") or "")
        except CDPError:
            return ""

    def selecteur_indicatif_present(self) -> bool:
        """
        Vrai si la page propose une liste d'indicatifs pays à côté du téléphone. Dans ce cas
        le champ numéro attend le numéro SANS indicatif, sinon « +212 » serait écrit deux fois.
        """
        try:
            return bool(self.b.eval(self.JS_SELECTEUR_INDICATIF))
        except CDPError:
            return False

    def champs_texte_vides(self) -> list[dict]:
        """Champs texte vides et visibles [{index, etiquette, autocomplete}] (pré-remplissage)."""
        return self._eval_json(JS_CHAMPS_VIDES, [])

    # -- connexion Google (SSO) ------------------------------------------------

    def bouton_google(self) -> list[str]:
        """Libellés des boutons « Se connecter / Continuer avec Google » visibles ([] si aucun)."""
        return self._eval_json(JS_BOUTON_GOOGLE, [])

    def cliquer_bouton_google(self) -> str:
        """
        Clique « Se connecter / Continuer avec Google » SANS choisir de compte ensuite.

        `connecter_avec_google` enchaîne et prend le PREMIER compte proposé ; pour changer de
        compte il faut s'arrêter avant, puis viser une adresse avec `choisir_compte_google`.
        """
        return self._clic_google()

    def comptes_google_proposes(self) -> list[str]:
        """Adresses proposées sur l'écran « Choisir un compte » ([] si on n'y est pas)."""
        return self._eval_json(JS_COMPTES_GOOGLE, [])

    def choisir_compte_google(self, email: str) -> str:
        """
        Clique LE compte demandé sur l'écran « Choisir un compte ».

        Différent de `_valider_google`, qui prend toujours le PREMIER compte : ici on vise une
        adresse précise, c'est ce qui permet de changer de compte plutôt que de reprendre le
        même. Renvoie 'cliqué', 'introuvable' ou 'erreur'.
        """
        try:
            return str(self.b.eval(js_choisir_compte_google(email)))
        except CDPError:
            return "erreur"

    def _clic_google(self) -> str:
        try:
            return str(self.b.eval(JS_CLIQUER_GOOGLE))
        except CDPError:
            return "introuvable"

    def _valider_google(self) -> str:
        try:
            return str(self.b.eval(JS_VALIDER_GOOGLE))
        except CDPError:
            return "rien"

    def _suivre_onglet_google(self) -> str:
        """Si Google s'est ouvert dans un autre onglet, l'active. Renvoie l'URL de l'onglet actif."""
        try:
            onglets = self.b.tabs()
        except CDPError:
            return self.url()
        for t in onglets:
            if "accounts.google.com" in (t.url or "") and t.index != self._onglet_actif(onglets):
                try:
                    self.b.tab(t.index)
                    time.sleep(0.6)
                except CDPError:
                    pass
                break
        return self.url()

    @staticmethod
    def _onglet_actif(onglets) -> int:
        for t in onglets:
            if getattr(t, "active", False):
                return t.index
        return -1

    def connecter_avec_google(self, attente: float = 45.0) -> tuple[bool, str]:
        """
        Clique « Continuer avec Google » et suit le flux Google (choix du compte + « Continuer »).
        Utilise la session Google DÉJÀ connectée dans le profil Chrome du robot : AUCUN identifiant
        ni mot de passe n'est saisi par le robot. Renvoie (réussi, compte_rendu lisible).
        """
        url_actuelle = self.url()
        deja_sur_google = "accounts.google.com" in url_actuelle
        if deja_sur_google:
            avant = url_actuelle   # le clic a déjà eu lieu (par ex. fait par le LLM) : on enchaîne le flux
        else:
            if not self.bouton_google():
                return False, "aucun bouton Google visible"
            self.neutraliser_onglets()   # window.open doit s'ouvrir dans l'onglet courant (Tappi pilote un onglet)
            avant = self.url()
            if self._clic_google() == "introuvable":
                return False, "bouton Google introuvable au moment du clic"
        fin = time.monotonic() + attente
        vu_google = False
        while time.monotonic() < fin:
            time.sleep(1.0)
            url = self._suivre_onglet_google()
            if "accounts.google.com" in url:
                vu_google = True
                # Un profil Chrome peut avoir PLUSIEURS comptes Google connectés (celui des
                # candidatures et celui du chat). `_valider_google` prendrait la première
                # tuile venue : la candidature partirait sous la mauvaise identité, avec la
                # mauvaise adresse pour la réponse du recruteur. On vise donc explicitement
                # l'adresse du profil, et on ne retombe sur le comportement général que si
                # elle n'est pas proposée (ou s'il n'y a qu'un seul compte).
                if self.email_candidature:
                    if self.choisir_compte_google(self.email_candidature) == "cliqué":
                        self.logger.info(f"  Compte Google choisi : {self.email_candidature}")
                        self._attendre_changement_url(url, 10.0)
                        continue
                if self._valider_google() != "aucune action possible":
                    self._attendre_changement_url(url, 10.0)
                continue
            if vu_google or (url and url != avant and "google" not in url):
                return True, "session Google utilisée, retour sur le site de candidature"
        return False, "flux Google non terminé (connexion Google peut-être expirée)"

    def _attendre_changement_url(self, url: str, delai: float) -> None:
        """Après un clic sur une page Google : attend qu'elle change, sans recliquer entre-temps
        (un 2e clic pendant le chargement relance l'étape, et le flux tourne en rond)."""
        fin = time.monotonic() + delai
        while time.monotonic() < fin:
            time.sleep(0.5)
            try:
                if self.url() != url:
                    return
            except Exception:  # noqa: BLE001
                return

    def options_select(self) -> dict:
        return self._eval_json(JS_OPTIONS_SELECT, {})

    def iframes(self) -> list[str]:
        return self._eval_json(JS_IFRAMES, [])

    def captcha_visible(self) -> bool:
        try:
            return bool(self.b.eval(JS_CAPTCHA))
        except CDPError:
            return False

    def section_lettre_presente(self) -> bool:
        """
        La page (iframes comprises) propose-t-elle une place pour la lettre : section « Supporting
        documents », « Cover letter », « Lettre de motivation », « Documents complémentaires »… ?
        """
        js = r"""
(() => {
  const docs = [document];
  for (let i = 0; i < docs.length; i++)
    for (const f of docs[i].querySelectorAll('iframe'))
      try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {}
  const motif = /supporting documents?|cover letter|lettre de motivation|documents? compl[ée]mentaires?|additional documents?|pi[eè]ces? jointes?/i;
  // Fenêtre superposée ouverte (Easy Apply de LinkedIn…) : on ne lit QUE son contenu. La page
  // de l'offre, derrière, contient souvent « cover letter » dans sa description, ce qui faisait
  // chercher un bouton « ajouter une lettre » qui n'existe pas.
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 50 && r.height > 50; };
  let fenetres = [...document.querySelectorAll('[role=dialog], [aria-modal=true]')].filter(vis);
  // Fenêtre superposée SANS role=dialog (nouveau formulaire Easy Apply de LinkedIn…) : un bloc
  // en position fixe qui couvre une bonne part de l'écran et contient un bouton d'envoi.
  if (!fenetres.length) {
    const envoi = /submit application|review your application|envoyer la candidature|vérifier votre candidature|continue to next step|suivant/i;
    fenetres = [...document.querySelectorAll('div, section, aside')].filter(el => {
      const st = getComputedStyle(el);
      if (st.position !== 'fixed') return false;
      const r = el.getBoundingClientRect();
      return r.width > innerWidth * 0.3 && r.height > innerHeight * 0.3
             && [...el.querySelectorAll('button')].some(b => envoi.test(b.innerText || ''));
    });
  }
  if (fenetres.length) return fenetres.some(f => motif.test(f.innerText || ''));
  return docs.some(d => d.body && motif.test(d.body.innerText || ''));
})()
"""
        try:
            return self.b.eval(js) is not False
        except CDPError:
            return True   # dans le doute, on cherche comme avant

    def cliquer_bouton_documents(self, genre: str, cliquer: bool = True, attente: float = 0.0) -> str:
        """
        Sur une page de revue, clique le bouton qui permet de REMPLACER le CV (genre « cv ») ou
        d'AJOUTER la lettre (genre « lm »), y compris dans une iframe. Renvoie son libellé,
        ou "" s'il n'existe pas. `cliquer=False` : le cherche seulement.
        `attente` : le chercher encore pendant ce temps (une page de revue s'affiche section
        par section : le bouton peut arriver une ou deux secondes après le reste).
        """
        fin = time.monotonic() + attente
        while True:
            try:
                res = self.b.eval(f"{JS_BOUTON_DOCUMENTS}({json.dumps(genre)}, {json.dumps(cliquer)})")
            except CDPError:
                res = ""
            if (isinstance(res, str) and res) or time.monotonic() >= fin:
                return res if isinstance(res, str) else ""
            time.sleep(0.3)

    def identites(self) -> dict[str, str]:
        """{numéro: identité} des éléments actuellement numérotés (voir JS_IDENTITES)."""
        res = self._eval_json(JS_IDENTITES, {})
        return res if isinstance(res, dict) else {}

    def empreinte_page(self) -> str:
        try:
            e = self.b.eval(JS_EMPREINTE_PAGE)
        except CDPError:
            return ""   # page en cours de remplacement
        return e if isinstance(e, str) else ""

    def attendre_apres_action(self, avant: str | None = None, delai_changement: float = 1.0,
                              delai_max: float = 15.0) -> float:
        """
        Attend que l'écran ait VRAIMENT fini de réagir à une action, avant la capture suivante :
          1) si `avant` (empreinte prise juste avant l'action) : que la page COMMENCE à changer
             (au plus `delai_changement` s — une action peut ne rien changer, ex. un bouton radio) ;
          2) puis qu'elle soit TERMINÉE : plus d'indicateur de chargement (Loading…, Preparing
             review, spinner…), document chargé et contenu immobile depuis 0,6 s.
        Un indicateur qui ne s'en va jamais (décor permanent) ne bloque pas plus de 10 s de
        contenu immobile ; sans indicateur, une page toujours animée pas plus de 2,5 s.
        Vérifie toutes les 0,15 s : on repart dès que c'est fini.
        Renvoie la durée attendue.
        """
        debut = time.monotonic()
        precedente, immobile_depuis = None, time.monotonic()
        if avant is not None:
            change = False
            while time.monotonic() - debut < delai_changement:
                if self.empreinte_page() != avant:
                    change = True
                    break
                time.sleep(0.15)
            if not change:
                # Rien n'a bougé depuis l'action : la page est déjà immobile depuis `debut`.
                precedente, immobile_depuis = avant, debut
        debut_fin = time.monotonic()
        while time.monotonic() - debut < delai_max:
            empreinte = self.empreinte_page()
            if empreinte != precedente:
                precedente, immobile_depuis = empreinte, time.monotonic()
            immobile = time.monotonic() - immobile_depuis
            if empreinte and "|complete|" in empreinte:
                try:
                    niveau = self.b.eval(JS_CHARGEMENT_EN_COURS)
                    niveau = niveau if isinstance(niveau, int) else 2
                except CDPError:
                    niveau = 2
                # Sans indicateur de chargement, une page qui s'anime sans arrêt (carrousel,
                # compteur, chat) ne retient pas plus de 2,5 s. Un simple « loader » repéré par
                # son nom de classe (indice faible) pas plus de 1 s de contenu immobile — LinkedIn
                # en garde un en permanence, ce qui coûtait 3 s après CHAQUE action ; seul un
                # vrai message « Loading… » peut retenir jusqu'à 10 s.
                if (niveau == 0 and (immobile >= 0.6 or time.monotonic() - debut_fin >= 2.5)) \
                        or (niveau == 1 and immobile >= 1.0) or immobile >= 10.0:
                    break
            time.sleep(0.15)
        return time.monotonic() - debut

    def captcha_a_resoudre(self) -> bool:
        """CAPTCHA affiché ET pas encore validé (un widget déjà validé ne bloque plus rien)."""
        if not self.captcha_visible():
            return False
        try:
            return self.b.eval(JS_CAPTCHA_A_RESOUDRE) is not False
        except CDPError:
            return True

    def attendre_fin_chargement(self, delai_max: float = 15.0) -> float:
        """
        La page affiche un état transitoire (« Loading… », « Saving and continuing… »,
        « Submitting… », spinner) : on attend qu'il disparaisse AVANT de consulter le modèle,
        au lieu de lui faire répondre « attendre » (un aller-retour de plusieurs secondes).
        Renvoie la durée attendue.
        """
        debut = time.monotonic()
        while time.monotonic() - debut < delai_max:
            try:
                if not self.b.eval(JS_CHARGEMENT_EN_COURS):
                    break
            except CDPError:
                pass   # page en cours de remplacement : c'est encore un chargement
            time.sleep(0.3)
        return time.monotonic() - debut

    def page_anti_robot(self) -> bool:
        """La page affichée est-elle une vérification anti-robot (Cloudflare…) à la place de l'offre ?"""
        try:
            return bool(self.b.eval(JS_PAGE_ANTI_ROBOT))
        except CDPError:
            return False

    def cliquer_case_anti_robot(self) -> str:
        """
        UN clic sur la case « Je ne suis pas un robot » de la page, s'il y en a une. Rien de
        plus : pas de résolution de défi. La case est souvent dans une iframe d'un autre
        domaine, donc hors de portée du DOM : on clique à ses coordonnées, comme une souris.
        """
        try:
            # La page renvoie une chaîne JSON ; le dict est toléré par prudence.
            brut = self.b.eval(JS_CASE_ANTI_ROBOT)
            zone = json.loads(brut) if isinstance(brut, str) else brut
        except (CDPError, ValueError):
            zone = None
        # Quand la page ne renvoie rien, Tappi ne renvoie pas None mais la description CDP du
        # vide ({"type": "object", "subtype": "null"}) : seul un couple x / y est une position.
        if not (isinstance(zone, dict) and "x" in zone and "y" in zone):
            return "aucune case trouvée"
        x, y = float(zone["x"]), float(zone["y"])
        cdp = self.b._connect_page()
        try:
            cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            for t in ("mousePressed", "mouseReleased"):
                cdp.send("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
        except Exception as e:  # noqa: BLE001
            return f"clic impossible : {e}"
        finally:
            cdp.close()
        return "case cliquée"

    # -- actions --------------------------------------------------------------

    def saisir(self, index: int, valeur: str) -> str:
        return self.b.type(index, valeur)

    def _touche(self, nom: str) -> None:
        """Envoie une frappe clavier (Entrée, flèche bas…) à l'élément focalisé, via CDP."""
        codes = {"Enter": (13, "Enter"), "ArrowDown": (40, "ArrowDown"),
                 "ArrowUp": (38, "ArrowUp"), "Escape": (27, "Escape"), "Tab": (9, "Tab")}
        vk, code = codes.get(nom, (0, nom))
        cdp = self.b._connect_page()
        try:
            for t in ("keyDown", "keyUp"):
                cdp.send("Input.dispatchKeyEvent", type=t, key=nom, code=code,
                         windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)
        except Exception:  # noqa: BLE001
            pass
        finally:
            cdp.close()

    def saisir_suggestion(self, index: int, valeur: str) -> str:
        """
        Remplit un champ à AUTOCOMPLÉTION (ville, localisation, école…) PUIS sélectionne la
        première suggestion proposée (flèche bas + Entrée). Indispensable quand le site refuse
        une saisie libre et exige un choix dans sa liste (ex. « Location (city) » de LinkedIn).

        IMPORTANT : ces champs ne proposent des suggestions que sur le NOM SIMPLE. « Rabat,
        Maroc » ne renvoie AUCUNE suggestion (et reste en erreur), alors que « Rabat » oui.
        On ne tape donc que la partie avant la 1re virgule.
        """
        requete = valeur.split(",")[0].strip() or valeur.strip()
        self._vider(index)                  # repart d'un champ vide (évite « Rabat, MarocRabat »)
        base = self.b.type(index, requete)
        time.sleep(1.1)                     # laisse la liste de suggestions apparaître
        # Si rien n'apparaît, on retente avec un seul mot (ex. « Rabat Agdal » -> « Rabat »)
        if not self._suggestions_visibles() and " " in requete:
            requete = requete.split()[0]
            self._vider(index)
            self.b.type(index, requete)
            time.sleep(1.1)
        self._touche("ArrowDown")
        time.sleep(0.35)
        self._touche("Enter")
        time.sleep(0.5)
        reste = "encore une liste ouverte" if self._suggestions_visibles() else "suggestion validée"
        return f"{base} (saisi « {requete} ») -> 1re suggestion sélectionnée (Bas + Entrée) — {reste}"

    def _vider(self, index: int) -> None:
        """Vide un champ texte (compatible React) avant d'y retaper une valeur propre."""
        js = r"""
        ((idx) => {
          const el = document.querySelector('[data-bpy-idx="' + idx + '"]');
          if (!el || !('value' in el)) return false;
          const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          setter.call(el, '');
          el.dispatchEvent(new Event('input', {bubbles: true}));
          return true;
        })(%s)""" % json.dumps(int(index))
        try:
            self.b.eval(js)
        except CDPError:
            pass

    def _suggestions_visibles(self) -> bool:
        """Vrai si une liste de suggestions d'autocomplétion est actuellement affichée."""
        js = r"""
        (() => {
          const vis = el => { const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight; };
          const opts = [...document.querySelectorAll(
            '[role=option],[role=listbox] li,.basic-typeahead__selectable,[class*=typeahead] li')];
          return opts.some(vis);
        })()"""
        try:
            return bool(self.b.eval(js))
        except CDPError:
            return False

    def cliquer(self, index: int, libelle: str = "") -> str:
        """
        Clique l'élément n° `index`. Si ce numéro est PÉRIMÉ (la page s'est redessinée, ex. après
        un téléversement : « Element [3] not found »), relit la page et clique l'élément qui
        porte le même libellé, au lieu de faire échouer l'action.
        """
        try:
            return self.b.click(index)
        except Exception as e:  # noqa: BLE001
            if "not found" not in str(e).lower() or not libelle:
                raise
            cle = self._cle_element(libelle)
            for ligne in self.elements():
                m = re.match(r"\[(\d+)\]", ligne)
                if m and cle and self._cle_element(ligne) == cle:
                    return self.b.click(int(m.group(1))) + f" (numéro périmé {index} -> {m.group(1)})"
            raise

    @staticmethod
    def _cle_element(ligne: str) -> str:
        """« [3] (button) Continue  « …annotations… » » -> « (button) Continue »."""
        ligne = re.sub(r"^\[\d+\]\s*", "", str(ligne)).split("  «")[0]
        return re.sub(r"\s+", " ", ligne.split(" → ")[0]).strip()

    # La même valeur, écrite dans l'autre langue du site (les listes de pays mélangent souvent
    # français et anglais : un site proposait « Morocco », le modèle cherchait « Maroc »).
    EQUIVALENCES = {"maroc": ["Morocco", "Marruecos"], "morocco": ["Maroc"],
                    "oui": ["Yes"], "yes": ["Oui"], "non": ["No"], "no": ["Non"],
                    "homme": ["Male", "Masculin"], "masculin": ["Male", "Homme"], "male": ["Masculin", "Homme"],
                    "femme": ["Female", "Féminin"], "féminin": ["Female", "Femme"], "female": ["Féminin", "Femme"],
                    "france": ["Francia"], "marocaine": ["Moroccan"], "moroccan": ["Marocaine"]}

    @staticmethod
    def _selection_ratee(resultat: str) -> bool:
        r = str(resultat)
        return (" absente" in r or r.startswith("élément introuvable")
                or r.startswith("ce n'est pas une liste"))

    def selectionner(self, index: int, option: str, libelle: str = "") -> str:
        """
        Choisit `option` dans la liste n° `index` (liste native ou menu « maison »). Si la voie
        habituelle échoue, trois secours dans l'ordre : même valeur dans l'autre langue, filtre
        de la liste (s'il existe), puis le clavier (premières lettres + Entrée) — une liste sans
        filtre se choisit donc quand même. Rien ne change quand la voie habituelle réussit.
        """
        resultat = self._selectionner_une_fois(index, option, libelle)
        if not self._selection_ratee(resultat):
            return resultat
        equivalents = self.EQUIVALENCES.get(option.strip().lower(), [])
        for autre in equivalents:
            r = self._selectionner_une_fois(index, autre, libelle)
            if not self._selection_ratee(r):
                return f"{r} (« {option} » absente de la liste : même valeur « {autre} » choisie)"
        if not self._liste_native(index):
            for texte in [option] + equivalents:
                r = self._selectionner_par_filtre(index, texte, libelle)
                if r:
                    return r
            for texte in [option] + equivalents:
                r = self._selectionner_menu_clavier(index, texte, libelle)
                if r:
                    return r
        return resultat

    def _liste_native(self, index: int) -> bool:
        try:
            return self.b.eval(f"(() => {{ const e = document.querySelector('[data-bpy-idx=\"{int(index)}\"]'); "
                               "return !!e && e.tagName === 'SELECT'; })()") is True
        except CDPError:
            return False

    def _taper(self, texte: str) -> None:
        """Tape `texte` dans l'élément qui a le focus (vraie saisie clavier CDP)."""
        cdp = self.b._connect_page()
        try:
            cdp.send("Input.insertText", text=texte)
        finally:
            cdp.close()

    def _touches(self, noms: list[str]) -> None:
        cdp = self.b._connect_page()
        try:
            for nom in noms:
                code = {"Enter": 13, "ArrowDown": 40}.get(nom, ord(nom.upper()) if len(nom) == 1 and nom.isascii() else 0)
                for evt in ("keyDown", "keyUp"):
                    params = {"type": evt, "key": nom, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
                    if len(nom) == 1 and evt == "keyDown":
                        params["text"] = nom
                    cdp.send("Input.dispatchKeyEvent", **params)
                time.sleep(0.06)
        finally:
            cdp.close()

    # Champ de FILTRE d'une liste : le champ de la liste lui-même (combobox où l'on tape), ou une
    # zone de recherche dans la liste ouverte. Mis en focus et vidé ; renvoie true s'il existe.
    JS_FILTRE_LISTE = r"""
    ((idx, popup) => {
      const racines = [];
      const ajouter = r => { racines.push(r); for (const e of r.querySelectorAll('*')) if (e.shadowRoot) ajouter(e.shadowRoot); };
      ajouter(document);
      const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const saisissable = e => e && ((e.tagName === 'INPUT' && /^(text|search|)$/.test(e.type || '')) || e.isContentEditable)
                               && !e.disabled && !e.readOnly && vis(e);
      let el = null;
      for (const r of racines) { el = r.querySelector('[data-bpy-idx="' + idx + '"]'); if (el) break; }
      let champ = saisissable(el) ? el : (el && [...el.querySelectorAll('input')].find(saisissable));
      if (!champ) {
        const listes = (popup && document.getElementById(popup)) ? [document.getElementById(popup)]
          : racines.flatMap(r => [...r.querySelectorAll('[role=listbox], [role=menu]')]).filter(vis);
        for (const l of listes) {
          for (let p = l, i = 0; p && i < 4 && !champ; p = p.parentElement || (p.getRootNode && p.getRootNode().host), i++)
            champ = [...(p.querySelectorAll ? p.querySelectorAll('input, [role=searchbox], [contenteditable=true]') : [])]
                      .find(saisissable) || null;
          if (champ) break;
        }
      }
      if (!champ) return false;
      champ.focus();
      if (champ.select) champ.select();
      return document.activeElement === champ || (champ.getRootNode && champ.getRootNode().activeElement === champ);
    })"""

    def _selectionner_par_filtre(self, index: int, option: str, libelle: str) -> str | None:
        """Secours n° 2 : taper l'option dans le filtre de la liste, puis cliquer l'option filtrée."""
        try:
            etat = self._eval_json(js_menu_aria(index, libelle, "ouvrir"), {})
            if not etat.get("trouve"):
                return None
            time.sleep(0.4)
            popup = str(etat.get("popup") or "")
            if self.b.eval(f"({self.JS_FILTRE_LISTE})({json.dumps(str(int(index)))}, {json.dumps(popup)})") is not True:
                return None
            self._taper(option)
            time.sleep(0.8)                                   # la liste se filtre
            res = self._eval_json(js_menu_aria_choisir(option, popup), {})
            if not res.get("ok"):
                return None
            time.sleep(0.4)
            apres = self._eval_json(js_menu_aria(index, libelle, "lire"), {})
            if str(res.get("choix", "")).lower() not in str(apres.get("texte", "")).lower() \
                    and not res.get("deja"):
                valeur = self.b.eval(f"(() => {{ const e = document.querySelector('[data-bpy-idx=\"{int(index)}\"]'); "
                                     "return e && 'value' in e ? e.value : ''; })()") or ""
                if str(res.get("choix", "")).lower() not in str(valeur).lower():
                    return None
            return f"ok (liste filtrée en tapant « {option} ») : {res.get('choix', '')}"
        except Exception as e:  # noqa: BLE001 — un secours ne doit jamais faire échouer l'action
            self.logger.debug(f"Sélection par filtre impossible : {e}")
            return None

    def _selectionner_menu_clavier(self, index: int, option: str, libelle: str) -> str | None:
        """
        Secours n° 3 (liste SANS filtre) : sur la liste ouverte, les premières lettres de
        l'option y sautent directement (comme dans Windows), puis Entrée la choisit.
        """
        try:
            etat = self._eval_json(js_menu_aria(index, libelle, "ouvrir"), {})
            if not etat.get("trouve"):
                return None
            time.sleep(0.4)
            # Un clic JavaScript n'a pas donné le focus à la liste : sans lui, les lettres tapées
            # n'arrivent nulle part.
            self.b.eval(f"(() => {{ const e = document.querySelector('[data-bpy-idx=\"{int(index)}\"]'); "
                        "if (e) e.focus(); return !!e; })()")
            self._touches(list(option.strip()[:5]))
            time.sleep(0.4)
            self._touches(["Enter"])
            time.sleep(0.5)
            apres = self._eval_json(js_menu_aria(index, libelle, "lire"), {})
            valeur = self.b.eval(f"(() => {{ const e = document.querySelector('[data-bpy-idx=\"{int(index)}\"]'); "
                                 "return e ? (('value' in e ? e.value : '') + ' ' + (e.innerText || '')) : ''; })()") or ""
            vu = f"{apres.get('texte', '')} {valeur}".lower()
            if option.strip().lower() in vu:
                return f"ok (liste sans filtre : premières lettres + Entrée) : {option}"
            return None
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Sélection au clavier impossible : {e}")
            return None

    def _selectionner_une_fois(self, index: int, option: str, libelle: str = "") -> str:
        result = str(self.b.eval(js_selectionner(index, option, libelle)))
        time.sleep(0.4)   # laisse React mettre à jour le DOM
        if "ATTENTION valeur non confirmée" in result:
            # Fallback clavier : focus + premières lettres de l'option + Entrée
            fallback = self._selectionner_par_clavier(index, option)
            if fallback:
                return f"ok (clavier) : {fallback}"
        # Aucun <select> trouvé (« 0 liste(s) sur la page ») ou élément qui n'en est pas un :
        # c'est un menu « maison » (Workday, React…) -> voie ARIA role=listbox / role=option.
        if "liste(s) sur la page" in result or "n'est pas une liste <select>" in result:
            aria = self._selectionner_menu_aria(index, option, libelle)
            if aria:
                return aria
        return result

    def _selectionner_menu_aria(self, index: int, option: str, libelle: str = "") -> str | None:
        """
        Fallback pour les menus déroulants sans <select> (Workday : « Titre (préfixe) »,
        « Type d'appareil téléphonique »…). On ouvre le menu, on lit le TEXTE de ses options
        et on clique la bonne — le modèle n'a pas à deviner un index d'option, qui n'est de
        toute façon ni stable ni lisible sur ces widgets.

        Deux passes : si la première ne voit aucune option, c'est que le menu était déjà
        ouvert et que notre clic l'a refermé -> on le rouvre et on recommence.

        Dans chaque passe, on PARCOURT la liste : un menu long (Workday « Diplôme ») ne garde
        qu'une fenêtre de ses options dans la page, et celle qu'on cherche peut n'y apparaître
        qu'après plusieurs crans de défilement.
        """
        for action in ("ouvrir", "forcer"):
            etat = self._eval_json(js_menu_aria(index, libelle, action), {})
            if not etat.get("trouve"):
                return None
            time.sleep(0.5)   # laisse la liste s'afficher (rendu React au tick suivant)
            # Certaines listes (Indeed) ignorent un clic JavaScript : elles ne s'ouvrent qu'avec un
            # vrai clic de souris — celui qu'un clic du modèle produit, et qui marchait.
            lu = self._eval_json(js_menu_aria(index, libelle, "lire"), {})
            if lu.get("trouve") and not lu.get("ouvert") and "x" in lu:
                try:
                    self.b.click_xy(float(lu["x"]), float(lu["y"]))
                    time.sleep(0.5)
                except Exception:  # noqa: BLE001
                    pass
            self._eval_json(js_menu_aria_defiler("haut"), {})   # repartir du début de la liste
            time.sleep(0.25)
            vues: list[str] = []
            popup = str(etat.get("popup") or "")
            sans_nouveaute = 0     # crans d'affilée sans aucune option nouvelle : fin de liste
            for _ in range(40):    # une liste de pays compte ~250 options, ~16 par cran
                res = self._eval_json(js_menu_aria_choisir(option, popup), {})
                if res.get("ok"):
                    time.sleep(0.4)
                    choix = str(res.get("choix", ""))
                    # Confirmé si l'option est marquée choisie (aria-selected / aria-checked :
                    # listes à choix multiples, qui affichent « 2 selected ») ou si le menu
                    # affiche maintenant le texte choisi.
                    coche = res.get("deja") or self._eval_json(
                        "JSON.stringify((() => { const o = document.getElementById("
                        + json.dumps(str(res.get("id", ""))) + "); return {v: !!o && ("
                        "o.getAttribute('aria-checked') === 'true' || "
                        "o.getAttribute('aria-selected') === 'true')}; })())", {}).get("v")
                    apres = self._eval_json(js_menu_aria(index, libelle, "lire"), {})
                    confirme = bool(coche) or choix.lower() in str(apres.get("texte", "")).lower()
                    # Menu refermé : sinon la prochaine saisie ou le prochain choix peut tomber
                    # dedans (Indeed garde ses listes à choix multiples ouvertes).
                    self._eval_json(js_menu_aria(index, libelle, "fermer"), {})
                    time.sleep(0.3)
                    lu = self._eval_json(js_menu_aria(index, libelle, "lire"), {})
                    if lu.get("ouvert") and "x" in lu:
                        try:
                            self.b.click_xy(float(lu["x"]), float(lu["y"]))   # vrai clic pour fermer
                        except Exception:  # noqa: BLE001
                            pass
                    return (f"ok (menu déroulant sans <select>)"
                            f"{' (déjà coché)' if res.get('deja') else ''}"
                            f"{'' if confirme else ' ATTENTION valeur non confirmée'} : {choix}")
                nb_avant = len(vues)
                for libelle_vu in res.get("options") or []:
                    if str(libelle_vu) not in vues:
                        vues.append(str(libelle_vu))
                sans_nouveaute = sans_nouveaute + 1 if len(vues) == nb_avant else 0
                if sans_nouveaute >= 2:
                    break     # deux crans sans rien de neuf : tout a été vu
                if not self._eval_json(js_menu_aria_defiler("bas"), {}).get("bouge"):
                    break     # bout de la liste : tout a été vu, inutile d'insister
                time.sleep(0.3)
            if vues:
                return self._option_absente(option, vues)
        return None

    @staticmethod
    def _option_absente(option: str, vues: list[str]) -> str:
        """
        Compte rendu UTILE en moins de 200 caractères — c'est la longueur que le pilote
        transmet au modèle (candidature.py). Lister les options dans l'ordre de la page ne
        servait à rien : la bonne était en 20e position, coupée avant d'arriver au modèle,
        qui devinait alors à l'aveugle. On met donc les plus ressemblantes en tête.
        """
        proches = difflib.get_close_matches(option, vues, n=4, cutoff=0.0)
        return (f"option « {option} » absente. Plus proches parmi les {len(vues)} du menu : "
                + " | ".join(proches))

    def _selectionner_par_clavier(self, index: int, option: str) -> str | None:
        """
        Fallback pour les <select> React qui n'acceptent pas les changements programmés :
        ouvre le select via un clic CDP réel, tape les premières lettres de l'option pour
        que le navigateur navigue vers elle, puis valide avec Entrée.
        """
        try:
            self.b.click(index)       # focus + ouverture du select
            time.sleep(0.3)
            prefix = option.strip()[:4]
            cdp = self.b._connect_page()
            try:
                for char in prefix:
                    vk = ord(char.upper()) if char.isascii() else 0
                    for evt in ("keyDown", "char", "keyUp"):
                        cdp.send("Input.dispatchKeyEvent", type=evt, key=char,
                                 text=char if evt == "char" else "",
                                 windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)
                    time.sleep(0.08)
                # Valider avec Entrée
                for evt in ("keyDown", "keyUp"):
                    cdp.send("Input.dispatchKeyEvent", type=evt, key="Enter", code="Enter",
                             windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
            finally:
                cdp.close()
            time.sleep(0.3)
            val = self.b.eval(
                f'document.querySelector(\'[data-bpy-idx="{int(index)}"]\')?.selectedOptions[0]?.text?.trim() || ""')
            return str(val or "").strip() or None
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Fallback clavier select {index} impossible : {e}")
            return None

    def televerser_par_bouton(self, index: int, chemin_fichier: Path) -> str:
        """
        Dépose un fichier via le BOUTON d'upload du site (« Upload resume », « Parcourir »…),
        pour les sites qui ne créent leur <input type=file> qu'au moment du clic.

        Chrome INTERCEPTE la fenêtre de choix de fichier (Page.setInterceptFileChooserDialog) :
        on fait un vrai clic sur le bouton, aucune fenêtre Windows ne s'ouvre, et Chrome
        signale le champ qui l'a demandée (Page.fileChooserOpened) — on y met le fichier
        directement. Fonctionne quelle que soit la façon dont le site cache ou crée son champ.
        """
        chemin = Path(chemin_fichier).expanduser().resolve()
        if not chemin.exists():
            return f"ÉCHEC : fichier introuvable {chemin}"
        # Centre du bouton, iframes de même origine comprises (décalage de chaque cadre).
        js_pos = r"""
((idx) => {
  const chercher = (doc, dx, dy) => {
    const el = doc.querySelector('[data-bpy-idx="' + idx + '"]');
    if (el) {
      el.scrollIntoView({block: 'center', inline: 'center'});
      const r = el.getBoundingClientRect();
      return {x: dx + r.left + r.width / 2, y: dy + r.top + r.height / 2};
    }
    for (const f of doc.querySelectorAll('iframe')) {
      let d = null; try { d = f.contentDocument; } catch (e) {}
      if (!d) continue;
      const r = f.getBoundingClientRect();
      const p = chercher(d, dx + r.left + f.clientLeft, dy + r.top + f.clientTop);
      if (p) return p;
    }
    return null;
  };
  return JSON.stringify(chercher(document, 0, 0));
})"""
        try:
            brut = self.b.eval(f"({js_pos})({json.dumps(str(int(index)))})")
            pos = json.loads(brut) if isinstance(brut, str) else None
        except (CDPError, ValueError):
            pos = None
        if not (isinstance(pos, dict) and "x" in pos):
            return f"ÉCHEC : bouton n° {index} introuvable pour le dépôt"
        x, y = float(pos["x"]), float(pos["y"])
        cdp = self.b._connect_page()
        ws = cdp._ws
        numero = [1000]

        def envoyer(methode: str, **params) -> None:
            numero[0] += 1
            ws.send(json.dumps({"id": numero[0], "method": methode, "params": params}))

        noeud = None
        try:
            cdp.send("Page.enable")
            cdp.send("DOM.enable")
            cdp.send("Page.setInterceptFileChooserDialog", enabled=True)
            # Envoi SANS attendre la réponse : l'événement du sélecteur arrive entre les deux,
            # et `send` de Tappi jette les événements qu'il rencontre en attendant.
            envoyer("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            envoyer("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
            envoyer("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)
            fin = time.monotonic() + 6.0
            while time.monotonic() < fin:
                try:
                    msg = json.loads(ws.recv(timeout=max(0.1, fin - time.monotonic())))
                except TimeoutError:
                    break
                if msg.get("method") == "Page.fileChooserOpened":
                    noeud = msg.get("params", {}).get("backendNodeId")
                    break
            if noeud is None:
                return (f"ÉCHEC : le bouton n° {index} n'a ouvert aucun choix de fichier "
                        f"(ce n'est peut-être pas le bouton d'upload)")
            cdp.send("DOM.setFileInputFiles", files=[str(chemin)], backendNodeId=noeud)
        except Exception as e:  # noqa: BLE001 — CDPError, connexion coupée…
            return f"ÉCHEC du dépôt par le bouton : {type(e).__name__} : {str(e)[:200]}"
        finally:
            try:
                cdp.send("Page.setInterceptFileChooserDialog", enabled=False)
            except Exception:  # noqa: BLE001
                pass
            cdp.close()
        # Le site affiche le nom du fichier une fois pris en compte (iframes comprises).
        cherche_nom = r"""
((nom) => {
  const docs = [document];
  for (let i = 0; i < docs.length; i++)
    for (const f of docs[i].querySelectorAll('iframe'))
      try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {}
  const dans = (r) => { for (const el of r.querySelectorAll('*'))
    if (el.shadowRoot && ((el.shadowRoot.textContent || '').includes(nom) || dans(el.shadowRoot))) return true;
    return false; };
  return docs.some(d => d.body && ((d.body.innerText || '').includes(nom) || dans(d)));
})(%s)""" % json.dumps(chemin.name)
        for _ in range(10):
            time.sleep(0.6)
            try:
                if self.b.eval(cherche_nom):
                    return (f"Uploaded: {chemin.name} -> via le bouton n° {index} "
                            f"(vérifié : affiché par le site)")
            except CDPError:
                pass
        return (f"Uploaded: {chemin.name} -> via le bouton n° {index} (déposé ; nom pas encore "
                f"affiché, vérifie sur la capture)")

    def televerser_cadre_externe(self, chemin_fichier: Path, genre: str) -> str:
        """
        Dépose le fichier dans un champ fichier situé DANS UN CADRE (iframe) D'UN AUTRE SITE,
        posé sur le bouton « Charger » (Salesforce, 26/09). Le JavaScript de la page ne
        voit pas l'intérieur d'un tel cadre, mais Chrome en fait une cible à part : on s'y
        connecte et on y dépose le fichier (le site l'envoie dès que le champ change).
        genre : "cv" ou "lm" — choisit le cadre d'après l'intitulé qui l'entoure.
        """
        chemin = Path(chemin_fichier).expanduser().resolve()
        if not chemin.exists():
            return f"ÉCHEC : fichier introuvable {chemin}"
        js_cadres = r"""
(() => JSON.stringify([...document.querySelectorAll('iframe')].filter(f => {
  const r = f.getBoundingClientRect();
  if (r.width < 20 || r.height < 10 || !/^https?:/.test(f.src || '')) return false;
  try { return !f.contentDocument; } catch (e) { return true; }      // autre site : illisible ici
}).map(f => {
  // Intitulé du champ : texte du plus grand bloc qui ne contient QUE ce cadre (« CV Charger »,
  // « Lettre de motivation Charger ») — jamais celui d'un bloc voisin.
  let p = f.parentElement, contexte = '';
  for (let i = 0; i < 6 && p && !/^(BODY|HTML|FORM)$/.test(p.tagName); i++, p = p.parentElement) {
    if (p.querySelectorAll('iframe').length > 1) break;
    contexte = (p.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160) || contexte;
  }
  return {src: f.src, contexte};
})))()"""
        try:
            cadres = json.loads(self.b.eval(js_cadres) or "[]")
            page_id = str((self.b._current_target() or {}).get("id") or "")
            port = self.cdp_url.rsplit(":", 1)[-1].split("/")[0]
            cibles = json.loads(urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3).read())
        except Exception as e:  # noqa: BLE001
            return f"ÉCHEC : cadres de la page illisibles ({type(e).__name__})"
        lettre = re.compile(r"lettre|cover|motivation", re.IGNORECASE)
        cv = re.compile(r"\bcv\b|curriculum|r[ée]sum[ée]|resume", re.IGNORECASE)
        voulus = [c for c in cadres if (lettre.search(c["contexte"]) if genre == "lm"
                                        else cv.search(c["contexte"]) and not lettre.search(c["contexte"]))]
        if not voulus and genre == "cv":
            voulus = [c for c in cadres if not lettre.search(c["contexte"])]
        deposes = []
        for c in voulus:
            cible = next((t for t in cibles if t.get("type") == "iframe" and t.get("url") == c["src"]
                          and (not page_id or t.get("parentId") in (page_id, None))), None)
            if not cible:
                continue
            try:
                cdp = CDPSession.connect_to_page(cible["id"], int(port))
            except Exception:  # noqa: BLE001
                continue
            try:
                cdp.send("DOM.enable")
                res = cdp.send("Runtime.evaluate", expression="document.querySelector('input[type=file]')",
                               returnByValue=False)
                object_id = res.get("result", {}).get("objectId")
                if not object_id:
                    continue
                cdp.send("DOM.setFileInputFiles", files=[str(chemin)], objectId=object_id)
                verif = cdp.send("Runtime.callFunctionOn", objectId=object_id, returnByValue=True,
                                 functionDeclaration="function() { return this.files && this.files.length "
                                                     "? this.files[0].name : ''; }")
                if verif.get("result", {}).get("value") == chemin.name:
                    deposes.append(c["contexte"][:40] or c["src"][:60])
            except Exception:  # noqa: BLE001 — cadre qui se recharge : on passe au suivant
                continue
            finally:
                cdp.close()
        if not deposes:
            return f"ÉCHEC : aucun champ fichier trouvé dans les cadres de la page pour « {genre} »"
        # Le site traite le fichier puis affiche son nom (dans la page ou dans le cadre).
        for _ in range(10):
            time.sleep(0.6)
            try:
                if self.fichier_present(chemin.name):
                    break
            except Exception:  # noqa: BLE001
                pass
        return (f"Uploaded: {chemin.name} -> champ fichier dans un cadre du site "
                f"({' ; '.join(deposes)}) — vérifie sur la capture que le site l'affiche")

    def televerser(self, chemin_fichier: Path, numero_input: int) -> str:
        """
        Dépose un fichier dans un <input type=file> sans ouvrir la fenêtre de l'OS (CDP), y compris
        quand le champ est caché dans un shadow DOM (ex. fenêtre « Candidature simplifiée » LinkedIn,
        où l'upload standard de Tappi ne le trouve pas). Vérifie ensuite que le fichier est bien pris.
        """
        chemin = Path(chemin_fichier).expanduser().resolve()
        if not chemin.exists():
            return f"ÉCHEC : fichier introuvable {chemin}"
        self.champs_fichier()  # (re)numérote les champs fichier (shadow DOM compris)
        selecteur = f'input[data-ja-file="{int(numero_input)}"]'
        trouver = r"""
        (() => {
          const chercher = (racine) => {
            let el = racine.querySelector(%s);
            if (el) return el;
            for (const h of racine.querySelectorAll('*')) { if (h.shadowRoot && (el = chercher(h.shadowRoot))) return el; }
            return null;
          };
          return chercher(document);
        })()""" % json.dumps(selecteur)
        # (Pas de DOM.getDocument complet : sur LinkedIn la page dépasse 1 Mo et coupe la connexion CDP.
        #  On cible directement l'élément par son objectId JavaScript.)
        cdp = self.b._connect_page()
        try:
            cdp.send("DOM.enable")
            res = cdp.send("Runtime.evaluate", expression=trouver, returnByValue=False)
            object_id = res.get("result", {}).get("objectId")
            if not object_id:
                return f"ÉCHEC : champ fichier n° {numero_input} introuvable (relis la liste des champs fichier)"
            cdp.send("DOM.setFileInputFiles", files=[str(chemin)], objectId=object_id)
            # Vérification : le navigateur a-t-il bien enregistré ce fichier dans le champ ?
            verif = cdp.send("Runtime.callFunctionOn", objectId=object_id, returnByValue=True,
                             functionDeclaration=(
                                 "function() { const r = this.getBoundingClientRect(); "
                                 "const s = getComputedStyle(this); return JSON.stringify({"
                                 "nom: this.files && this.files.length ? this.files[0].name : '', "
                                 "visible: r.width > 0 && r.height > 0 && s.display !== 'none' "
                                 "&& s.visibility !== 'hidden' && s.opacity !== '0'}); }"))
            etat_champ = json.loads(verif.get("result", {}).get("value") or "{}")
            nom = etat_champ.get("nom") or ""
            champ_visible = bool(etat_champ.get("visible"))
        except Exception as e:  # noqa: BLE001 — CDPError, connexion coupée…
            return f"ÉCHEC du dépôt : {type(e).__name__} : {str(e)[:200]}"
        finally:
            cdp.close()
        if nom == chemin.name and champ_visible:
            time.sleep(1)   # le site traite le fichier (aperçu, analyse du CV…)
            return f"Uploaded: {chemin.name} -> champ fichier n° {numero_input} (vérifié)"
        # Champ CACHÉ : qu'il garde le fichier ne prouve rien — c'était peut-être le champ d'une
        # autre rubrique resté dans la page (LinkedIn : celui du CV, à l'étape de la lettre).
        # Seul le site qui AFFICHE le nom du fichier prouve qu'il l'a pris.
        # Certains sites (LinkedIn…) vident le champ dès qu'ils ont pris le fichier et l'affichent
        # dans leur liste : on vérifie alors que son nom apparaît sur la page (shadow DOM compris).
        cherche_nom = r"""
        ((nom) => {
          if ((document.body.innerText || '').includes(nom)) return true;
          const parcourir = (r) => { for (const el of r.querySelectorAll('*')) {
            if (el.shadowRoot && ((el.shadowRoot.textContent || '').includes(nom) || parcourir(el.shadowRoot))) return true; }
            return false; };
          return parcourir(document);
        })(%s)""" % json.dumps(chemin.name)
        for _ in range(6):
            time.sleep(0.6)
            try:
                if self.b.eval(cherche_nom):
                    return f"Uploaded: {chemin.name} -> champ fichier n° {numero_input} (vérifié : affiché par le site)"
            except CDPError:
                pass
        if nom == chemin.name:
            return (f"ÉCHEC : {chemin.name} mis dans un champ fichier CACHÉ que le site n'affiche pas "
                    f"(champ d'une autre rubrique) — dépôt à refaire par le bouton d'upload")
        return f"ÉCHEC : {chemin.name} n'apparaît ni dans le champ ni sur la page après le dépôt"

    def fichier_present(self, nom_fichier: str) -> bool:
        """
        Vrai si le nom de fichier (ex. « CV_Prenom_NOM_Entreprise.pdf ») apparaît quelque part
        sur la page, shadow DOM compris. Sert à savoir si le CV adapté est DÉJÀ déposé — par ex.
        quand LinkedIn reprend une candidature en cours et rouvre à l'écran de revue, le CV ayant
        déjà été téléversé lors d'un essai précédent. Évite de boucler à re-chercher un champ CV.
        """
        if not nom_fichier:
            return False
        js = r"""
        ((nom) => {
          if ((document.body.innerText || '').includes(nom)) return true;
          const parcourir = (r) => { for (const el of r.querySelectorAll('*')) {
            if (el.shadowRoot && ((el.shadowRoot.textContent || '').includes(nom) || parcourir(el.shadowRoot))) return true; }
            return false; };
          return parcourir(document);
        })(%s)""" % json.dumps(nom_fichier)
        try:
            return bool(self.b.eval(js))
        except CDPError:
            return False

    def _info_case(self, index: int) -> dict:
        return self._eval_json(JS_INFO_CASE + f"({int(index)})", {}) or {}

    def texte_autour(self, index: int, longueur: int = 300) -> str:
        """Texte de la rubrique qui contient l'élément n° index (« Lettre de motivation Upload »)."""
        js = r"""
((idx, maxi) => {
  const racines = [];
  const ajouter = r => { racines.push(r);
    for (const el of r.querySelectorAll('*')) { if (el.shadowRoot) ajouter(el.shadowRoot); } };
  ajouter(document);
  let el = null;
  for (const r of racines) { el = r.querySelector('[data-bpy-idx="' + idx + '"]'); if (el) break; }
  if (!el) return '';
  const propre = t => (t || '').replace(/\s+/g, ' ').trim();
  const soi = propre(el.innerText || el.value || '');
  let p = el.parentElement;
  for (let k = 0; k < 6 && p; k++, p = p.parentElement) {
    const t = propre(p.innerText);
    if (t.length > soi.length + 15) return t.slice(0, maxi);
  }
  return soi.slice(0, maxi);
})(""" + str(int(index)) + ", " + str(int(longueur)) + ")"
        try:
            return str(self.b.eval(js) or "")
        except CDPError:
            return ""

    def question_de_la_case(self, index: int) -> str:
        """
        Si la case à cocher n° `index` est une OPTION d'une question du formulaire (groupe
        d'options avec son intitulé : « Préférences de mobilité ? » -> « All France »),
        renvoie le texte de la question ; sinon "". Une case isolée (alerte emploi, « Suivre
        l'entreprise », newsletter) n'appartient à aucune question.
        """
        js = r"""
((idx) => {
  const racines = [];
  const ajouter = r => {
    racines.push(r);
    for (const el of r.querySelectorAll('*')) {
      if (el.shadowRoot) ajouter(el.shadowRoot);
      if (el.tagName === 'IFRAME') { try { if (el.contentDocument) ajouter(el.contentDocument); } catch (e) {} }
    }
  };
  ajouter(document);
  let el = null;
  for (const r of racines) { el = r.querySelector('[data-bpy-idx="' + idx + '"]'); if (el) break; }
  if (!el) return '';
  // Vraie case HTML OU case « maison » (role=checkbox, ex. formulaires Workable).
  const CASE = 'input[type=checkbox], [role=checkbox]';
  const cb = el.matches(CASE) ? el : el.querySelector(CASE);
  if (!cb) return '';
  const net = t => (t || '').replace(/\s+/g, ' ').trim();
  const groupe = cb.closest('fieldset, [role=group], [role=radiogroup]');
  let question = '';
  if (groupe) {
    const t = groupe.querySelector('legend, [role=heading], h2, h3, h4, [id*=title], [class*=title]');
    question = net((t && t.innerText) || groupe.getAttribute('aria-label') || '');
    const ids = groupe.getAttribute('aria-labelledby'); const r = groupe.getRootNode();
    if (!question && ids && r.getElementById) question = net(ids.split(/\s+/).map(i => (r.getElementById(i) || {}).innerText || '').join(' '));
  }
  const choix_multiple = /multipleChoice/i.test((cb.name || '') + ' ' + (cb.id || ''));
  if (!question) {
    // Sans conteneur de groupe : le 1er ancêtre qui contient PLUSIEURS cases est la question,
    // son intitulé est la 1re ligne de texte (au-dessus des options).
    // On s'arrête avant le formulaire / la page entière : deux cases sans rapport (« Se
    // souvenir de moi », « Recevoir des alertes ») ne forment pas une question.
    const trop_large = /^(BODY|HTML|FORM|MAIN|DIALOG)$/;
    let p = cb.parentElement;
    for (let i = 0; i < 4 && p && !trop_large.test(p.tagName) && p.getAttribute('role') !== 'dialog';
         i++, p = p.parentElement) {
      if (p.querySelectorAll(CASE).length >= 2 || (choix_multiple && i >= 1)) {
        const lignes = (p.innerText || '').split('\n').map(net).filter(Boolean);
        if (lignes.length >= 2) question = lignes[0];
        break;
      }
    }
  }
  if (!question) {
    // Options rangées dans une GRILLE (Workday : fieldset sans titre > role=grid > row > cell),
    // l'intitulé étant le <legend> d'un fieldset PLUS HAUT (« What is your preferred way of
    // work? »). On part du groupe d'options (vrai groupe : fieldset / group / grid) et on
    // remonte jusqu'au premier intitulé qui n'est pas une option — sans jamais déborder sur
    // un conteneur qui contient d'autres cases (une autre question).
    const conteneur = cb.closest('fieldset, [role=group], [role=grid], [role=radiogroup], [role=listbox]');
    const cases = conteneur ? conteneur.querySelectorAll(CASE) : [];
    if (cases.length >= 2) {
      const libelle = c => net((c.labels && c.labels[0] && c.labels[0].innerText) || c.getAttribute('aria-label') || '');
      const options = new Set([...cases].map(libelle).filter(Boolean));
      const sans_etoile = t => net(t).replace(/\s*\*+\s*$/, '');
      let q = conteneur;
      for (let i = 0; i < 8 && q && !/^(BODY|HTML|FORM|MAIN)$/.test(q.tagName); i++, q = q.parentElement) {
        if (q.querySelectorAll(CASE).length > cases.length) break;
        const leg = q.querySelector(':scope > legend') || (q.tagName === 'FIELDSET' ? q.querySelector('legend') : null);
        let t = sans_etoile(leg ? leg.innerText : '');
        if (!t) {
          const lignes = (q.innerText || '').split('\n').map(sans_etoile)
            .filter(l => l && !options.has(l) && !/^(error|erreur)\b/i.test(l));
          t = lignes[0] || '';
        }
        if (t && !options.has(t)) { question = t; break; }
      }
    }
  }
  return question.slice(0, 200);
})
"""
        try:
            return str(self.b.eval(f"({js})({json.dumps(str(index))})") or "")
        except CDPError:
            return ""

    _JS_ELEMENT = r"""
((idx) => {
  const racines = [];
  const ajouter = r => { racines.push(r); for (const el of r.querySelectorAll('*')) {
    if (el.shadowRoot) ajouter(el.shadowRoot);
    if (el.tagName === 'IFRAME') { try { if (el.contentDocument) ajouter(el.contentDocument); } catch (e) {} } } };
  ajouter(document);
  for (const r of racines) { const el = r.querySelector('[data-bpy-idx="' + idx + '"]'); if (el) return el; }
  return null;
})"""

    def adresse_du_lien(self, index: int) -> str:
        """Adresse COMPLÈTE (jamais tronquée) du lien n° `index`, ou "" si ce n'est pas un lien."""
        js = (f"(() => {{ const el = ({self._JS_ELEMENT})({json.dumps(str(int(index)))}); "
              "const a = el && (el.closest('a[href]') || el.querySelector('a[href]')); "
              "return a ? a.href : ''; })()")
        try:
            return str(self.b.eval(js) or "")
        except CDPError:
            return ""

    def valeur_champ(self, index: int) -> str:
        """Valeur actuelle du champ n° `index` (texte), ou "" s'il est introuvable."""
        js = (f"(() => {{ const el = ({self._JS_ELEMENT})({json.dumps(str(int(index)))}); "
              "return el && 'value' in el ? String(el.value) : ''; })()")
        try:
            return str(self.b.eval(js) or "")
        except CDPError:
            return ""

    def cliquer_case(self, index: int, seulement_cocher: bool = False) -> str:
        """
        Coche / décoche une case ou un interrupteur et VÉRIFIE que l'état a changé.
        Si le clic JavaScript n'a aucun effet (case invisible stylée), fait un vrai clic de souris
        à l'endroit visible de la case.
        seulement_cocher : une case DÉJÀ cochée n'est pas touchée (un consentement ne doit
        jamais être décoché parce que la liste lue par le modèle datait d'avant le cochage).
        """
        avant = self._info_case(index)
        if seulement_cocher and avant.get("etat"):
            return "case déjà cochée (laissée telle quelle)"
        if "etat" not in avant:
            return self.cliquer(index)
        self.cliquer(index)
        time.sleep(0.6)
        apres = self._info_case(index)
        if apres.get("etat") != avant["etat"]:
            return f"case {'cochée' if apres.get('etat') else 'décochée'} (vérifié)"
        # Le clic JS n'a rien changé : vrai clic de souris sur la partie visible
        if not apres.get("visible"):
            self.defiler(index)
            apres = self._info_case(index)
        try:
            self.b.click_xy(float(apres["x"]), float(apres["y"]))
        except (CDPError, KeyError, TypeError, ValueError) as e:
            return f"ÉCHEC : impossible de cliquer la case ({e})"
        time.sleep(0.6)
        final = self._info_case(index)
        if final.get("etat") != avant["etat"]:
            return f"case {'cochée' if final.get('etat') else 'décochée'} (vérifié, clic souris réel)"
        return (f"ÉCHEC : la case n'a pas changé d'état (toujours {'cochée' if avant['etat'] else 'décochée'}) "
                f"malgré un clic JavaScript et un clic souris réel")

    # Nombre d'écrans parcourus au plus en une seule action « defiler »
    MAX_ECRANS_PAR_DEFILEMENT = 6

    def defiler(self, index: int | None = None, sens: str = "bas") -> str:
        """
        Fait défiler la fenêtre superposée (en priorité) ou la page, ou amène l'élément
        `index` à l'écran.

        Sans index, on va JUSQU'AU BOUT en une seule action (plusieurs écrans d'affilée).
        Auparavant chaque action ne descendait que d'un écran : atteindre le bouton d'envoi
        d'une longue modale demandait trois allers-retours avec le modèle, soit ~36 s pour
        un simple défilement. Le contenu n'est pas perdu pour autant : la liste des éléments
        interactifs et le texte de la page sont lus en entier, quel que soit le défilement.
        """
        def un_ecran() -> str:
            try:
                return str(self.b.eval(JS_DEFILER + f"({json.dumps(index)}, {json.dumps(sens)})"))
            except CDPError:
                return str(self.b.scroll("up" if sens == "haut" else "down", 800))

        resultat = un_ecran()
        time.sleep(0.5)   # laisse le contenu chargé à la demande apparaître
        if index is not None:
            return resultat
        ecrans = 1
        while ecrans < self.MAX_ECRANS_PAR_DEFILEMENT and "tout en bas" not in resultat \
                and "tout en haut" not in resultat:
            suivant = un_ecran()
            time.sleep(0.35)
            # Plus rien ne bouge : inutile d'insister (fin de zone, ou zone non défilante)
            if "de 0 px" in suivant:
                break
            resultat = suivant
            ecrans += 1
        return resultat if ecrans == 1 else f"{resultat} [{ecrans} écrans en une fois]"

    def etat_defilement(self) -> str:
        try:
            return str(self.b.eval(JS_ETAT_DEFILEMENT) or "")
        except CDPError:
            return ""

    def capture_jpeg(self, qualite: int = 60) -> bytes | None:
        """Capture de la zone visible en JPEG (octets), sans fichier. None en cas d'échec."""
        try:
            cdp = self.b._connect_page()
            try:
                resultat = cdp.send("Page.captureScreenshot", format="jpeg", quality=qualite)
                return base64.b64decode(resultat.get("data", ""))
            finally:
                cdp.close()
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Capture JPEG impossible : {e}")
            return None

    def flouter_prives(self) -> int:
        """
        CONFIDENTIALITÉ : floute, sur la page, chaque champ et chaque texte qui contient le
        prénom, le nom, l'email, le téléphone ou l'adresse du candidat (shadow DOM et iframes
        compris). À appeler juste avant une capture envoyée à un modèle ; `deflouter()` après.
        Renvoie le nombre de zones floutées.
        """
        try:
            from confidentialite import courante as confidentialite
            # Téléphone NON flouté : DeepSeek doit pouvoir lire un indicatif faux / en double.
            motifs = confidentialite().motifs_js(telephone=False)
            if not motifs:
                return 0
            return int(self.b.eval(JS_FLOUTER_PRIVES % json.dumps(motifs)) or 0)
        except Exception:  # noqa: BLE001 — une capture ne doit jamais échouer pour ça
            return 0

    def deflouter(self) -> None:
        try:
            self.b.eval(JS_DEFLOUTER)
        except Exception:  # noqa: BLE001
            pass

    def capture_annotee(self, qualite: int = 60) -> bytes | None:
        """
        Capture avec le numéro de chaque élément dessiné dessus (à appeler juste
        après elements(), qui attribue les numéros). Les annotations sont retirées ensuite.
        Les données personnelles du candidat sont FLOUTÉES sur l'image (confidentialite.py).
        """
        self.flouter_prives()
        try:
            self.b.eval(JS_MARQUER_ELEMENTS)
        except CDPError:
            pass
        try:
            return self.capture_jpeg(qualite)
        finally:
            try:
                self.b.eval(JS_EFFACER_MARQUES)
            except CDPError:
                pass
            self.deflouter()

    def capture(self, chemin: Path) -> Path | None:
        """Capture d'écran (utile pour comprendre une erreur) ; ne plante jamais."""
        try:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            self.b.screenshot(str(chemin))
            return chemin
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"Capture d'écran impossible : {e}")
            return None
