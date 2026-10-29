"""
Pilotage du CHAT WEB DeepSeek comme moteur de REMPLISSAGE DE FORMULAIRE.

Ce module ne réécrit AUCUNE logique : `ClientDeepSeekFormulaire` hérite de
`ClientGeminiChat` et ne redéfinit que ce qui est propre au site (URL, repères de la page,
JavaScript des sélecteurs). Tout le reste — un onglet par candidature gardé ouvert,
l'épinglage, le lien de conversation mémorisé et rouvert en cas de raté, l'envoi vérifié
par la zone de saisie qui se vide, la capture d'écran jointe, la détection d'écho, de refus
et d'erreur, l'envoi en delta — est exactement le code déjà éprouvé de gemini_chat.py.

    moteur = ClientDeepSeekFormulaire(config, nav, logger)
    texte  = moteur.generer(prompt_systeme, message, images=[jpeg], tache="formulaire")
    moteur.fermer()          # fin de candidature -> l'onglet est fermé

ATTENTION — il y a DEUX conversations DeepSeek pendant une candidature : celle qui rédige
le CV et la lettre (deepseek_chat.ChatDeepSeek, onglet « A ») et celle-ci, qui remplit le
formulaire (onglet « B »). Chacune suit SON onglet par son identifiant CDP, jamais par
« le premier onglet dont l'URL contient deepseek » : sans cela l'une écrirait dans la
conversation de l'autre, ou la refermerait.
"""

from __future__ import annotations

from gemini_chat import ClientGeminiChat


class ClientDeepSeekFormulaire(ClientGeminiChat):
    """Même moteur que ClientGeminiChat, branché sur le chat web DeepSeek."""

    NOM = "DeepSeek (formulaire)"
    CLE_CONFIG = "deepseek_formulaire"
    HOTE = "deepseek"
    # DeepSeek ouvre .../a/chat/s/<id> dès le premier message : c'est ce lien qui porte la
    # mémoire de la conversation (profil, offre, étapes déjà faites).
    MARQUE_CONVERSATION = "/chat/s/"
    URL_DEFAUT = "https://chat.deepseek.com/"
    DOSSIER_TMP = "_deepseek_formulaire"

    # Réponses déjà affichées. Doit rendre {n, dernier}, comme chez Gemini.
    #
    # ATTENTION : chez DeepSeek, le fil est VIRTUALISÉ — les anciens messages sont DÉMONTÉS
    # du DOM quand la conversation s'allonge. Compter les éléments présents est donc faux :
    # le total en ajoutait un en bas et en retirait un en haut, et restait figé (2, 2, 2…)
    # alors que les réponses continuaient d'arriver. Le robot attendait alors 120 s une
    # réponse pourtant affichée à l'écran, puis basculait sur le moteur de secours.
    #
    # On MARQUE donc chaque réponse au moment où on la voit pour la première fois, et on
    # tient le compteur dans la page : il ne peut plus redescendre. Il repart de zéro à
    # chaque rechargement, ce qui est exactement ce qu'il faut (n_avant est relu après).
    JS_REPONSES = r"""
    (() => {
      let els = [...document.querySelectorAll('[class*="assistant-message-main-content"]')];
      if (!els.length) {
        // Repli si DeepSeek renomme ses classes : les blocs markdown de PREMIER niveau,
        // c'est-à-dire ceux qui ne sont pas imbriqués dans un autre bloc markdown.
        els = [...document.querySelectorAll('.ds-markdown, [class*="ds-markdown"]')]
          .filter(e => !e.parentElement
                       || !e.parentElement.closest('.ds-markdown, [class*="ds-markdown"]'));
      }
      els = els.filter(e => (e.innerText || '').trim());
      if (typeof window.__bpyNbReponses !== 'number') window.__bpyNbReponses = 0;
      const dernier = els.length ? els[els.length - 1] : null;
      let texte = dernier ? (dernier.innerText || '') : '';
      // DeepSeek REDESSINE parfois son dernier message (nouvel élément, même texte) après
      // l'envoi d'une question : compté comme une réponse neuve, l'ANCIENNE réponse était
      // relue et rejouée sur la page suivante. Même texte que la réponse déjà vue = redessin.
      if (dernier && !dernier.hasAttribute('data-bpy-vue')) {
        dernier.setAttribute('data-bpy-vue', '1');
        if (texte.trim() !== (window.__bpyTexteVu || '').trim()) window.__bpyNbReponses += 1;
      }
      if (dernier) window.__bpyTexteVu = texte;
      // Ce chat pilote des formulaires (prose + JSON), mais llm_client lui refile aussi les
      // réécritures LaTeX quand la clé API meurt. Or le markdown RENDU interprète les
      // échappements : « \\ » s'y affiche « \ », et le document revient cassé. Un document
      // complet se lit donc dans son bloc <pre>, seul texte BRUT de la page. Hors de ce cas,
      // rien ne change : la prose et le JSON des actions sont lus exactement comme avant.
      if (dernier) {
        const brut = [...dernier.querySelectorAll('pre')].map(p => p.innerText || '')
          .filter(t => t.includes('\\documentclass') && t.includes('\\end{document}')).pop();
        if (brut) texte = brut;
      }
      return JSON.stringify({n: window.__bpyNbReponses, dernier: texte});
    })()
    """

    # Champ de saisie : chez DeepSeek c'est le DERNIER champ éditable large de la page
    # (la barre du bas) — le premier serait la recherche de la barre latérale.
    JS_COMPOSER = r"""
    (() => {
      const e = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
        .filter(x => { const r = x.getBoundingClientRect();
                       return r.width > 120 && r.height > 8 && r.bottom <= innerHeight + 300; })
        .pop();
      if (!e) return null;
      const r = e.getBoundingClientRect();
      return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2});
    })()
    """

    JS_INDEX_SAISIE = r"""
    (() => {
      const e = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
        .filter(x => { const r = x.getBoundingClientRect();
                       return r.width > 120 && r.height > 8 && r.bottom <= innerHeight + 300; })
        .pop();
      return e ? e.getAttribute('data-bpy-idx') : null;
    })()
    """

    # Bouton d'envoi (icône flèche) près de la zone de saisie.
    JS_ENVOYER = r"""
    (() => {
      const composer = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
        .filter(el => { const r = el.getBoundingClientRect();
                        return r.width > 120 && r.bottom <= innerHeight + 300; }).pop();
      const zone = composer ? (composer.closest('form, div[class*="input"], div[class*="chat"], div') || document) : document;
      let cands = [...zone.querySelectorAll('button, [role="button"], div[role="button"]')];
      if (cands.length < 2) cands = [...document.querySelectorAll('button, [role="button"], div[role="button"]')];
      let b = cands.find(x => /send|envoyer/i.test((x.getAttribute('aria-label') || '') + (x.getAttribute('title') || '')));
      if (!b) b = cands.find(x => x.type === 'submit');
      if (!b) b = cands.reverse().find(x => { const r = x.getBoundingClientRect();
        return r.width > 20 && r.width < 90 && r.height > 20 && r.height < 90 && r.bottom <= innerHeight + 10; });
      if (!b) return 'introuvable';
      if (b.disabled || b.getAttribute('aria-disabled') === 'true') return 'inactif';
      b.click();
      return 'ok';
    })()
    """

    JS_LONGUEUR_SAISIE = r"""
    (() => {
      const e = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
        .filter(x => { const r = x.getBoundingClientRect();
                       return r.width > 120 && r.bottom <= innerHeight + 300; }).pop();
      return e ? (e.value != null ? e.value.trim().length : (e.textContent || '').trim().length) : -1;
    })()
    """

    # Bouton « joindre un fichier ». Contrairement à Gemini, DeepSeek garde déjà son
    # input[type=file] dans la page : si le bouton n'est pas reconnu, on vise la zone de
    # saisie — le clic sert alors juste à donner le focus, et le dépôt CDP fonctionne quand
    # même (vérifié).
    JS_BOUTON_PLUS = r"""
    (() => {
      const cible = [...document.querySelectorAll('button, [role="button"], label, div')].find(x => {
        const t = ((x.getAttribute('aria-label') || '') + ' ' + (x.getAttribute('title') || '')
                   + ' ' + (x.innerText || '')).trim();
        return t && t.length < 40
          && /joindre|attach|upload|t[ée]l[ée]verser|importer|fichier|file/i.test(t)
          && x.getBoundingClientRect().width > 5;
      }) || [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
        .filter(x => { const r = x.getBoundingClientRect();
                       return r.width > 120 && r.bottom <= innerHeight + 300; }).pop();
      if (!cible) return null;
      const r = cible.getBoundingClientRect();
      return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2});
    })()
    """

    # Pièce jointe prise en compte : vignette, ou carte portant le nom du fichier déposé.
    JS_PIECE_JOINTE = r"""
    (() => !!document.querySelector('img[src^="blob:"], img[src^="data:image"]')
        || /page\.jpg/i.test(document.body.innerText || ''))()
    """
