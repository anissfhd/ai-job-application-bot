"""
Génération du CV et de la lettre de motivation personnalisés (LaTeX -> PDF).

Le LLM propose une version adaptée, puis le CODE vérifie et impose les
garde-fous (on ne fait pas confiance au LLM seul) :
  1. le préambule LaTeX de l'original est toujours réutilisé tel quel ;
  2. les blocs entre « % PROTEGE-DEBUT » et « % PROTEGE-FIN » sont restaurés
     à l'identique ;
  3. nom, prénom, email, téléphone et les mentions obligatoires
     (« Stage PFE 6 mois »…) doivent être présents à l'identique ;
  4. la longueur du CV doit rester proche de l'original ;
  5. les termes techniques de l'offre absents de ton CV original sont
     détectés (compétence inventée) ;
  6. la LM doit mentionner « 6 mois » et ne plus contenir de texte du modèle ([…], XYZ…) ;
  7. chaque PDF doit tenir sur le nombre de pages autorisé (1 par défaut).
En cas de problème, le LLM recommence avec la liste des erreurs (3 essais max).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import yaml

from latex_compiler import ErreurLatex, compiler
from llm_client import ClientLLM
from objectif import Objectif, courant as objectif_courant
from utils import chemin_projet, normaliser_espaces, retirer_balises_code, slugifier

def neutraliser_liens(tex: str) -> str:
    """
    Garantit qu'un document compilé ne contiendra AUCUN lien cliquable.

    Le CV original n'en a plus, mais il est RÉÉCRIT par le modèle à chaque offre : rien
    n'empêche celui-ci de remettre un \\href autour du GitHub ou de l'email. Une consigne
    dans le prompt n'est qu'une demande — ici, c'est mécanique.

    On passe l'option « draft » à hyperref : toutes ses commandes continuent d'afficher leur
    texte, mais n'engendrent plus aucune annotation de lien. Un \\href{url}{texte} imprime
    donc « texte », sans rien de cliquable. Sans hyperref dans le document, l'option est
    simplement ignorée.
    """
    if r"\documentclass" not in tex:
        return tex
    if "PassOptionsToPackage{draft}{hyperref}" in tex:
        return tex                                   # déjà neutralisé
    m = re.search(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", tex)
    if not m:
        return tex
    garde = ("\n% Aucun lien cliquable dans les documents envoyés (ajouté automatiquement).\n"
             r"\PassOptionsToPackage{draft}{hyperref}")
    return tex[:m.end()] + garde + tex[m.end():]


def retirer_entete_interface(tex: str) -> str:
    r"""
    Retire ce qui précède le document LaTeX quand la réponse de DeepSeek a été lue « hors bloc
    de code » : les libellés de l'encadré de code de son interface (« latex », « Copier »,
    « Télécharger », « Copy », « Download »…) se retrouvaient avant \documentclass, et LaTeX
    refusait de compiler — à chaque essai, jusqu'à l'abandon de la candidature.
    Seules les lignes de TEXTE avant le préambule sont retirées : un commentaire (%) ou une
    commande (\PassOptionsToPackage…) placés avant \documentclass sont conservés.
    """
    i = tex.find(r"\documentclass")
    if i <= 0:
        return tex
    lignes = tex[:i].splitlines(keepends=True)
    garde = [l for l in lignes if l.strip().startswith(("\\", "%")) or not l.strip()]
    return "".join(garde).lstrip("\n") + tex[i:]


def compter_liens_pdf(pdf) -> int:
    """Nombre d'annotations de lien dans un PDF (0 attendu). -1 si la lecture échoue."""
    try:
        import pymupdf
        with pymupdf.open(pdf) as doc:
            return sum(len(page.get_links()) for page in doc)
    except Exception:  # noqa: BLE001 — un contrôle raté ne doit pas casser la génération
        return -1


MARQUEUR_DEBUT = "% PROTEGE-DEBUT"
MARQUEUR_FIN = "% PROTEGE-FIN"
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
# Mots courants qui ne sont pas des compétences (évite les fausses alertes)
MOTS_COURANTS = {"CV", "PFE", "RH", "HR", "FR", "EN", "OK", "CDI", "CDD", "SAS", "SA", "SARL", "H/F", "F/H", "M/F"}


class ErreurGeneration(Exception):
    """Impossible de produire un document valide après plusieurs essais."""


# ---------------------------------------------------------------------------
# Vérifications (fonctions pures, testables indépendamment)
# ---------------------------------------------------------------------------

def normaliser_pour_comparaison(texte: str) -> str:
    """Normalise pour comparer : espaces LaTeX (~, \\,) -> espace simple, minuscules."""
    texte = texte.replace("~", " ").replace("\\,", " ").replace("\\ ", " ")
    return normaliser_espaces(texte).lower()


def separer_preambule(tex: str) -> tuple[str, str]:
    """Renvoie (préambule, corps) ; le corps commence à \\begin{document}."""
    position = tex.find(r"\begin{document}")
    if position == -1:
        return "", tex
    return tex[:position], tex[position:]


def blocs_proteges(tex: str) -> list[str]:
    motif = re.escape(MARQUEUR_DEBUT) + r".*?" + re.escape(MARQUEUR_FIN)
    return re.findall(motif, tex, re.DOTALL)


def restaurer_blocs_proteges(original: str, genere: str) -> tuple[str, str]:
    """
    Remet a l'identique les blocs « % PROTEGE-DEBUT ... % PROTEGE-FIN » du document d'origine.

    Ces blocs portent les coordonnees : l'email volontairement espace
    (« nom @ domaine . com »), que les lecteurs PDF ne transforment pas en lien, ainsi que les
    adresses GitHub et LinkedIn, l'ecole et la disponibilite. Le modele qui reecrit le CV les
    « corrige » spontanement : il recollait l'email, qui redevenait cliquable a l'affichage.

    Le titre adapte a l'offre est volontairement ECRIT ENTRE les deux blocs : il reste libre.

    Renvoie (tex, etat) avec etat = "" | "restaure" | "introuvable". En cas de decoupage qui
    ne correspond pas, on ne touche a rien : mieux vaut un bloc modifie qu'un document casse.
    """
    blocs_orig = blocs_proteges(original)
    if not blocs_orig:
        return genere, ""
    blocs_gen = blocs_proteges(genere)
    if len(blocs_gen) != len(blocs_orig):
        return genere, "introuvable"
    etat = ""
    for ancien, nouveau in zip(blocs_orig, blocs_gen):
        if ancien != nouveau:
            genere = genere.replace(nouveau, ancien, 1)
            etat = "restaure"
    return genere, etat


# Email écrit de façon ESPACÉE dans le CV d'origine : « nom @ domaine . com ».
_EMAIL_ESPACE = re.compile(r"[A-Za-z0-9._%+-]+\s+@\s+[A-Za-z0-9-]+\s+\.\s+[A-Za-z]{2,}")


def forme_email_origine(original: str) -> tuple[str, str]:
    """
    Si le document d'origine écrit son email de façon espacée, renvoie
    (forme_compacte, forme_espacée) — par exemple ("a@b.com", "a @ b . com"). Sinon ("", "").
    """
    m = _EMAIL_ESPACE.search(original)
    if not m:
        return "", ""
    espace = " ".join(m.group().split())
    compacte = espace.replace(" @ ", "@").replace(" . ", ".")
    return compacte, espace


def imposer_email_non_cliquable(original: str, genere: str) -> tuple[str, bool]:
    """
    Rétablit l'email dans la forme ESPACÉE choisie dans le document d'origine.

    Un lecteur PDF (Chrome, Edge, Acrobat) transforme de lui-même « nom@domaine.com » en lien
    mailto : c'est une détection à l'affichage, rien dans le fichier ne la demande, et aucune
    option LaTeX ne l'empêche. Le seul moyen est que le texte n'ait PAS la forme d'une
    adresse — d'où l'écriture espacée du CV d'origine. Le modèle qui réécrit le CV la
    « corrige » spontanément en recollant l'adresse : on la remet donc ici, mécaniquement.
    """
    compacte, espace = forme_email_origine(original)
    if not compacte or compacte not in genere:
        return genere, False
    # Un \href{mailto:…}{texte} éventuel : neutraliser_liens le rend déjà non cliquable, mais
    # le TEXTE affiché resterait détectable par le lecteur. On retire donc les deux.
    genere = re.sub(r"\\href\s*\{\s*mailto:[^}]*\}\s*\{([^{}]*)\}", r"\1", genere)
    return genere.replace(compacte, espace), True


def imposer_garde_fous_structurels(original: str, genere: str) -> tuple[str, list[str]]:
    """
    Remet le préambule original et restaure les blocs protégés.
    Renvoie (tex_corrigé, liste_des_problèmes_bloquants).
    """
    problemes = []
    genere = retirer_balises_code(genere)

    preambule, _ = separer_preambule(original)
    _, corps = separer_preambule(genere)
    if r"\begin{document}" not in corps:
        problemes.append(r"Le document ne contient pas \begin{document}.")
    if r"\end{document}" not in corps:
        problemes.append(r"Le document ne se termine pas par \end{document} (réponse tronquée ?).")
    else:
        corps = corps[: corps.rfind(r"\end{document}") + len(r"\end{document}")] + "\n"
    tex = preambule + corps

    blocs_orig = blocs_proteges(original)
    if blocs_orig:
        blocs_gen = blocs_proteges(tex)
        if len(blocs_gen) != len(blocs_orig):
            problemes.append(f"Les {len(blocs_orig)} blocs « {MARQUEUR_DEBUT} … {MARQUEUR_FIN} » "
                             f"doivent être recopiés à l'identique (trouvés : {len(blocs_gen)}).")
        else:
            for ancien, nouveau in zip(blocs_orig, blocs_gen):
                tex = tex.replace(nouveau, ancien, 1)
    return tex, problemes


def verifier_chaines(original: str, genere: str, chaines: list[str]) -> list[str]:
    """Chaque chaîne présente dans l'original doit rester présente dans le document généré."""
    orig_n, gen_n = normaliser_pour_comparaison(original), normaliser_pour_comparaison(genere)
    problemes = []
    for chaine in chaines:
        c = normaliser_pour_comparaison(str(chaine or ""))
        if c and c in orig_n and c not in gen_n:
            problemes.append(f"La mention « {chaine} » a disparu ou a été modifiée : recopie-la à l'identique.")
    return problemes


def detecter_competences_inventees(original: str, genere: str, texte_offre: str,
                                   termes_autorises: str = "") -> list[str]:
    """
    Repère les termes techniques (sigles, CamelCase, chiffres, C++, C#, .NET…) qui
    apparaissent dans l'offre ET dans le CV généré mais PAS dans le CV original.
    `termes_autorises` : texte dont les mots ne sont jamais signalés (ex. nom de l'entreprise).
    """
    motif = r"(?<![\w\\])(?:[A-Za-z][A-Za-z0-9]*[+#]+|\.?[A-Z][a-z]*[A-Z][A-Za-z0-9]*|[A-Z]{2,}[A-Za-z0-9]*|[A-Za-z]+[0-9][A-Za-z0-9]*)"
    orig_n = original.lower()
    _, corps_gen = separer_preambule(genere)
    offre_termes = {t.lower() for t in re.findall(motif, texte_offre or "")}
    suspects = set()
    for terme in re.findall(motif, corps_gen):
        if terme in MOTS_COURANTS or len(terme) < 2 or terme.lower() in termes_autorises.lower():
            continue
        if terme.lower() in offre_termes and terme.lower() not in orig_n:
            suspects.add(terme)
    if suspects:
        return [f"Termes absents du CV original ajoutés (compétences inventées interdites) : "
                f"{', '.join(sorted(suspects))}. Retire-les."]
    return []


def verifier_longueur(original: str, genere: str, tolerance: float = 0.30) -> list[str]:
    _, c_orig = separer_preambule(original)
    _, c_gen = separer_preambule(genere)
    if not c_orig:
        return []
    ratio = len(c_gen) / len(c_orig)
    if ratio < 1 - tolerance:
        return [f"Le CV généré est beaucoup plus court que l'original ({ratio:.0%}) : ne supprime pas de contenu."]
    if ratio > 1 + tolerance:
        return [f"Le CV généré est beaucoup plus long que l'original ({ratio:.0%}) : il doit tenir sur la même longueur."]
    return []


def verifier_duree_lm(genere: str) -> list[str]:
    obj = objectif_courant()
    motif = obj.regex_duree(lettre=True)
    if motif is not None and not motif.search(genere):
        return [f"La lettre doit mentionner explicitement la durée du {obj.type_mot} : « {obj.duree_fr} »."]
    return []


# Morceaux de texte du modèle de lettre qui ne doivent plus apparaître une fois la lettre rédigée
RESIDUS_MODELE_LM = ["[NOM DE L'ENTREPRISE]", "[date du jour]", "[Ville", "[intitulé", "précisez ici",
                     "détaillez ici", "reprendre les missions", "société XYZ", "Nom de l'employeur",
                     "mettre ici", "Poste de l'employeur", "Adresse de l'entreprise"]


def verifier_modele_rempli(genere: str) -> list[str]:
    _, corps = separer_preambule(genere)
    restes = [r for r in RESIDUS_MODELE_LM if r.lower() in corps.lower()]
    if restes:
        return [f"Des passages du modèle n'ont pas été remplacés : {', '.join(restes)}. "
                f"Rédige le texte définitif (ou supprime la ligne si l'information est inconnue)."]
    return []


def compter_pages(pdf: Path) -> int:
    """Nombre de pages d'un PDF (0 si illisible)."""
    try:
        import pymupdf as fitz  # PyMuPDF
        with fitz.open(pdf) as doc:
            return doc.page_count
    except Exception:  # noqa: BLE001
        return 0


def mesurer_debordement(pdf: Path, pages_max: int) -> dict:
    """
    Diagnostic CHIFFRÉ d'un PDF trop long, pour dire à GPT *combien* couper et *où*.

    « Réduis-le pour qu'il tienne sur 1 page » ne converge pas : sans quantité, le modèle
    refait la même chose à chaque essai. On mesure donc sur le rendu réel : le nombre de
    lignes qui débordent, la place encore libre sur la dernière page autorisée, et les
    titres de section rejetés en trop.

    Renvoie {pages, lignes_debord, hauteur_libre_pt, lignes_a_couper, sections_debord}.
    """
    diag = {"pages": 0, "lignes_debord": 0, "hauteur_libre_pt": 0.0, "taux_remplissage": 0,
            "lignes_libres": 0, "lignes_a_couper": 0, "sections_debord": []}
    try:
        import pymupdf as fitz  # PyMuPDF
        with fitz.open(pdf) as doc:
            diag["pages"] = doc.page_count
            if not pages_max or not doc.page_count:
                return diag
            # Place restante en bas de la dernière page autorisée (marge basse ≈ marge haute)
            derniere = doc[min(pages_max, doc.page_count) - 1]
            blocs = [b for b in derniere.get_text("blocks") if str(b[4]).strip()]
            haut = min((b[1] for b in blocs), default=0.0)
            bas = max((b[3] for b in blocs), default=0.0)
            diag["hauteur_libre_pt"] = max(0.0, derniere.rect.height - bas - haut)
            diag["taux_remplissage"] = round(100 * bas / derniere.rect.height)
            lignes_page = [l for l in derniere.get_text().split("\n") if l.strip()]
            hauteur_ligne = (bas - haut) / max(1, len(lignes_page))
            if hauteur_ligne > 0:
                diag["lignes_libres"] = int(diag["hauteur_libre_pt"] / hauteur_ligne)
            # Document assez court : pas de débordement à analyser
            if doc.page_count <= pages_max:
                return diag
            # Ce qui a été rejeté au-delà de la limite
            lignes, sections = 0, []
            for i in range(pages_max, doc.page_count):
                textes = [l.strip() for l in doc[i].get_text().split("\n") if l.strip()]
                lignes += len(textes)
                # une ligne courte commençant par une lettre (pas une puce) = titre de section
                sections += [t for t in textes if len(t) < 40 and t[0].isalpha()][:3]
            diag["lignes_debord"] = lignes
            diag["sections_debord"] = sections[:4]
            # À couper = ce qui déborde, moins ce qui tiendrait encore, + une marge de sûreté
            tient = int(diag["hauteur_libre_pt"] / hauteur_ligne) if hauteur_ligne > 0 else 0
            diag["lignes_a_couper"] = max(2, lignes - tient + 2)
    except Exception:  # noqa: BLE001 — un diagnostic raté ne doit jamais bloquer la génération
        pass
    return diag


# ---------------------------------------------------------------------------
# Générateur
# ---------------------------------------------------------------------------

class GenerateurDocuments:
    def __init__(self, config: dict, profil: dict, llm: ClientLLM, logger: logging.Logger | None = None):
        self.config = config
        self.profil = profil
        self.llm = llm
        self.logger = logger or logging.getLogger("job_automation")
        chemins = config["chemins"]
        self.cv_original_path = chemin_projet(chemins["cv_original"])
        self.lm_original_path = chemin_projet(chemins["lm_original"])
        dossier_prompts = chemin_projet(chemins["prompts"])
        objectif = Objectif(profil)            # repères [[…]] : objectif de recherche du candidat
        self.prompt_cv = objectif.remplir((dossier_prompts / "cv_prompt.txt").read_text(encoding="utf-8"))
        self.prompt_lm = objectif.remplir((dossier_prompts / "lm_prompt.txt").read_text(encoding="utf-8"))
        self.prompt_reduction = objectif.remplir((dossier_prompts / "reduction_prompt.txt").read_text(encoding="utf-8"))
        ds = config.get("deepseek_chat", {}) or {}
        self.deepseek_actif = bool(ds.get("actif", False))
        self._chat_documents = None      # conversation DeepSeek qui rédige CV et lettre
        self._origine_protegee = ""      # document d'origine dont les blocs protégés font foi
        self.reduire_1page = bool(ds.get("reduire_1page_avec_gpt", True))
        latex = config.get("latex", {})
        self.moteur = latex.get("moteur", "pdflatex")
        self.passes = int(latex.get("passes", 2))
        self.timeout = int(latex.get("timeout_secondes", 180))
        garde_fous = config.get("garde_fous", {})
        self.chaines_cv = self._chaines_identite() + list(garde_fous.get("chaines_obligatoires_cv") or [])
        self.chaines_lm = self._chaines_identite() + list(garde_fous.get("chaines_obligatoires_lm") or [])
        self.replis: list[str] = []   # replis sur document original (signalés dans l'Excel)
        self._secours_latex = None        # moteur de repli pour les réécritures LaTeX
        self._latex_secours_actif = False  # armé dès le 1er échec de réécriture
        # En dessous de ce taux de remplissage, le document est jugé trop court et enrichi.
        self.remplissage_min = int(garde_fous.get("remplissage_minimum_pourcent", 92))
        self.pages_max_cv = int(garde_fous.get("pages_max_cv", 1))
        self.pages_max_lm = int(garde_fous.get("pages_max_lm", 1))

    def _chaines_identite(self) -> list[str]:
        ident = self.profil.get("identite", {}) or {}
        return [v for v in (ident.get("prenom"), ident.get("nom"), ident.get("email"), ident.get("telephone")) if v]

    @staticmethod
    def _lire(chemin: Path) -> str:
        if not chemin.exists():
            raise FileNotFoundError(f"Fichier introuvable : {chemin} — dépose ton fichier LaTeX original à cet endroit.")
        return chemin.read_text(encoding="utf-8")

    @staticmethod
    def _bloc_offre(offre: dict) -> str:
        return (
            "<offre>\n"
            f"Entreprise : {offre.get('nom_entreprise', '')}\n"
            f"Intitulé du poste : {offre.get('titre_poste', '')}\n"
            f"Lieu : {offre.get('lieu', '')}\n"
            f"Description de l'entreprise :\n{offre.get('description_entreprise', '')}\n\n"
            f"Description de l'offre :\n{offre.get('description_offre', '')}\n"
            "</offre>"
        )

    def nom_fichier(self, prefixe: str, offre: dict | None = None) -> str:
        """
        « CV_Prenom_Nom_Entreprise » : nom propre pour le recruteur, et unique par offre
        (le site ne le confond pas avec un ancien CV déjà déposé).
        """
        ident = self.profil.get("identite", {}) or {}
        morceaux = [prefixe, slugifier(f"{ident.get('prenom', '')}_{ident.get('nom', '')}")]
        if offre and offre.get("nom_entreprise"):
            morceaux.append(slugifier(str(offre["nom_entreprise"]), 30))
        return "_".join(m for m in morceaux if m and m != "sans_nom")

    def _compiler(self, tex: str, dossier: Path, nom: str) -> Path:
        # TOUTES les compilations passent ici : c'est le seul endroit où garantir qu'aucun
        # document envoyé ne contient de lien cliquable, quelle que soit la réécriture.
        # Les coordonnees (email espace, GitHub, LinkedIn) reviennent a l'identique de
        # l'original : le modele les « corrige » sinon a chaque reecriture.
        tex = retirer_entete_interface(tex)
        origine = getattr(self, "_origine_protegee", "")
        tex, etat = restaurer_blocs_proteges(origine, tex)
        if etat == "restaure":
            self.logger.info(f"{nom} : coordonnées remises à l'identique de l'original "
                             f"(blocs protégés).")
        elif etat == "introuvable":
            # Le modèle retire souvent les commentaires en réécrivant : on ne peut plus
            # raccrocher les blocs. Ce n'est pas bloquant, l'email est traité juste après.
            self.logger.debug(f"{nom} : repères « {MARQUEUR_DEBUT} » absents du document "
                              f"réécrit -> restauration par blocs impossible.")
        # L'email, lui, est remis dans sa forme espacée quoi qu'il arrive : c'est la seule
        # chose qui empêche un lecteur PDF d'en faire un lien mailto tout seul.
        tex, remis = imposer_email_non_cliquable(origine, tex)
        if remis:
            self.logger.info(f"{nom} : email réécrit en clair espacé (non détectable "
                             f"comme lien par les lecteurs PDF).")
        tex = neutraliser_liens(tex)
        chemin_tex = dossier / f"{nom}.tex"
        chemin_tex.write_text(tex, encoding="utf-8")
        pdf = compiler(chemin_tex, dossier, self.moteur, self.passes, self.timeout,
                       dossiers_ressources=[self.cv_original_path.parent])
        liens = compter_liens_pdf(pdf)
        if liens > 0:
            self.logger.warning(f"{pdf.name} : {liens} lien(s) cliquable(s) subsistent "
                                f"malgré la neutralisation — à signaler.")
        return pdf

    @staticmethod
    def _bloc_mentions(chaines: list[str]) -> str:
        """Liste des textes à recopier à l'identique, transmise au LLM."""
        lignes = "\n".join(f"- {c}" for c in chaines)
        return f"<mentions_obligatoires>\n{lignes}\n</mentions_obligatoires>"

    def _boucle_generation(self, type_doc: str, prompt_systeme: str, construire_message, original: str,
                           verifications, dossier: Path, nom: str, pages_max: int = 1, essais: int = 3) -> Path:
        erreurs: list[str] = []
        for essai in range(1, essais + 1):
            message = construire_message()
            if erreurs:
                message += "\n\n<erreurs_precedentes>\n- " + "\n- ".join(erreurs) + "\n</erreurs_precedentes>"
            reponse = self.llm.generer(prompt_systeme, message)
            tex, erreurs = imposer_garde_fous_structurels(original, reponse)
            if not erreurs:
                erreurs = verifications(tex)
            if erreurs:
                self.logger.warning(f"{type_doc} essai {essai}/{essais} rejeté : {' | '.join(erreurs)}")
                (dossier / f"{nom}_rejet_{essai}.tex").write_text(tex, encoding="utf-8")
                continue
            try:
                pdf = self._compiler(tex, dossier, nom)
                pages = compter_pages(pdf)
                if pages_max and not 1 <= pages <= pages_max:   # 0 = PDF illisible -> rejet aussi
                    erreurs = [f"Le PDF fait {pages} pages alors qu'il doit tenir sur {pages_max} : "
                               f"raccourcis le texte d'environ 25 % (phrases plus courtes, moins de détails), "
                               f"sans toucher à la mise en page ni aux blocs protégés."]
                    self.logger.warning(f"{type_doc} essai {essai}/{essais} rejeté : {erreurs[0]}")
                    continue
                self.logger.info(f"{type_doc} : PDF prêt ({pages} page(s)) -> {pdf}")
                return pdf
            except ErreurLatex as e:
                erreurs = [f"Erreur de compilation LaTeX, corrige-la : {e}\n{e.extrait_log}"]
                self.logger.warning(f"{type_doc} essai {essai}/{essais} : compilation échouée\n{e.extrait_log}")
        raise ErreurGeneration(f"{type_doc} : échec après {essais} essais — {' | '.join(erreurs)}")

    # -- API publique -------------------------------------------------------

    def generer_cv(self, offre: dict, dossier: Path) -> Path:
        """Produit le CV adapté à l'offre et renvoie le chemin du PDF."""
        dossier.mkdir(parents=True, exist_ok=True)
        original = self._lire(self.cv_original_path)
        texte_offre = f"{offre.get('description_offre', '')}\n{offre.get('description_entreprise', '')}"

        def message() -> str:
            return (f"<cv_original>\n{original}\n</cv_original>\n\n{self._bloc_offre(offre)}\n\n"
                    f"{self._bloc_mentions(self.chaines_cv)}")

        def verifications(tex: str) -> list[str]:
            return (verifier_chaines(original, tex, self.chaines_cv)
                    + verifier_longueur(original, tex)
                    + detecter_competences_inventees(original, tex, texte_offre,
                                                     str(offre.get("nom_entreprise", ""))))

        return self._boucle_generation("CV", self.prompt_cv, message, original, verifications,
                                       dossier, self.nom_fichier("CV", offre), self.pages_max_cv)

    def compiler_cv_original(self, dossier: Path, offre: dict | None = None) -> Path:
        """Solution de secours : compile le CV original sans modification."""
        dossier.mkdir(parents=True, exist_ok=True)
        return self._compiler(self._lire(self.cv_original_path), dossier, self.nom_fichier("CV", offre))

    # ------------------------------------------------------------------
    # Génération via le CHAT DeepSeek (contenu conservé tel quel ; GPT ne
    # sert qu'à corriger une erreur LaTeX et à réduire à 1 page si besoin).
    # ------------------------------------------------------------------

    def moteur_documents(self, nav=None):
        """
        Le chat qui a rédigé le CV adapté (« lien 2 »), sous l'interface generer(...).
        None tant qu'aucun document n'a été rédigé par DeepSeek.

        Sert aux QUESTIONS TECHNIQUES du formulaire : c'est cette conversation qui a lu
        l'offre et choisi les compétences à mettre en avant, elle seule peut y répondre sans
        rien inventer.
        """
        if self._chat_documents is None:
            # CV réutilisé (essai précédent) ou robot relancé : la conversation n'existe pas
            # encore. On la crée ; elle s'ouvrira à la première question (message complet).
            if nav is None or not self.deepseek_actif:
                return None
            from deepseek_chat import ChatDeepSeek
            self._chat_documents = ChatDeepSeek(self.config, nav, self.logger)
        from deepseek_chat import ClientDeepSeekDocuments
        return ClientDeepSeekDocuments(self._chat_documents, self.logger)

    def brancher_secours_latex(self, moteur) -> None:
        """
        Déclare un moteur de repli pour les RÉÉCRITURES LaTeX (réduction, correction).

        Utile quand la clé API est morte et que tout bascule sur le chat Gemini : celui-ci
        pilote très bien les formulaires mais renvoie régulièrement du LaTeX non compilable.
        DeepSeek, qui a rédigé le document au départ, s'en sort bien mieux — on lui passe donc
        la main dès la première réécriture ratée.
        """
        self._secours_latex = moteur

    def _reecrire(self, tex: str, instruction: str) -> str:
        """
        Réécriture LaTeX avec bascule IMMÉDIATE : au PREMIER échec du moteur principal,
        le secours (DeepSeek) prend la main pour tout le reste du document.

        Deux façons d'échouer, toutes deux traitées ici : renvoyer autre chose qu'un document
        LaTeX complet (erreur levée ci-dessous), ou renvoyer un document qui ne compile pas
        (détecté par `_compiler_ajuste`, qui arme le même drapeau). Insister avec le moteur
        qui vient d'échouer donnait trois erreurs d'affilée et la candidature perdue.
        """
        # Clé API morte ? Alors self.llm basculerait sur son moteur de secours — qui est le
        # chat du FORMULAIRE. Or celui-ci ne connaît ni le document ni l'offre : ses
        # réécritures ne compilaient pas. On va directement à la conversation qui a rédigé
        # le document, sans perdre un essai.
        api_ok = getattr(self.llm, "api_disponible", lambda: True)()
        if not api_ok and getattr(self, "_secours_latex", None) is not None:
            self._latex_secours_actif = True
        if not self._latex_secours_actif:
            try:
                reponse = self._retravailler_gpt(tex, instruction)
                # La clé peut mourir PENDANT cet appel : llm_client bascule alors en silence
                # sur le chat du FORMULAIRE, qui n'a ni le document ni l'offre. Le test
                # ci-dessus, fait AVANT l'appel, ne pouvait pas le voir — et c'est ce
                # trou-là qui a renvoyé une lettre incompilable. On refait donc la
                # réécriture dans la conversation des documents.
                if (getattr(self, "_secours_latex", None) is None
                        or getattr(self.llm, "api_disponible", lambda: True)()):
                    return reponse
                self._latex_secours_actif = True
                self.logger.warning("Réécriture LaTeX : la clé API est tombée PENDANT l'appel "
                                    "-> on refait la réécriture dans la conversation des documents.")
            except ErreurGeneration as e:
                if getattr(self, "_secours_latex", None) is None:
                    raise
                self._latex_secours_actif = True
                self.logger.warning(f"Réécriture LaTeX : réponse inexploitable ({e}) "
                                    f"-> DeepSeek prend la main.")
        return self._retravailler_gpt(tex, instruction, secours=True)

    # La consigne de format voyage DANS le message, pas seulement dans le prompt système :
    # le moteur DeepSeek des documents ignore le prompt système (sa conversation le connaît
    # déjà), donc une consigne qui n'est QUE dans reduction_prompt.txt ne l'atteint jamais.
    FORMAT_REPONSE = (
        "FORMAT DE RÉPONSE — OBLIGATOIRE : renvoie le document LaTeX complet, de "
        "\\documentclass à \\end{document}, DANS UN BLOC DE CODE ```latex ... ```. "
        "Rien d'autre, aucune explication avant ou après. Hors bloc de code, les « \\\\ » "
        "sont altérés à l'affichage et le document devient incompilable."
    )

    def _retravailler_gpt(self, tex: str, instruction: str, secours: bool = False) -> str:
        """Demande au modèle de retravailler le LaTeX (réduire à 1 page / corriger une erreur)."""
        message = (f"{instruction}\n\n{self.FORMAT_REPONSE}\n\n"
                   f"<document_latex>\n{tex}\n</document_latex>")
        moteur = getattr(self, "_secours_latex", None) if secours else None
        brut = (moteur or self.llm).generer(self.prompt_reduction, message)
        reponse = retirer_balises_code(brut)
        if r"\end{document}" in reponse:
            reponse = reponse[: reponse.rfind(r"\end{document}") + len(r"\end{document}")] + "\n"
        if r"\documentclass" not in reponse or r"\end{document}" not in reponse:
            raise ErreurGeneration("GPT n'a pas renvoyé un document LaTeX complet.")
        return reponse

    # Consignes de plus en plus fermes : répéter 3 fois le même ordre flou ne converge pas,
    # le modèle refait la même chose. Chaque palier autorise un levier supplémentaire.
    _PALIERS_REDUCTION = (
        "Condense les formulations les plus longues (phrases verbeuses, redites, "
        "énumérations de technologies trop détaillées).",
        "Condense davantage ET resserre les espacements DÉJÀ présents dans le préambule "
        "(\\vspace, itemsep, topsep) : c'est autorisé et sans risque pour la mise en page.",
        "Supprime en plus les puces les MOINS pertinentes pour cette offre, et ramène "
        "chaque puce restante à UNE seule ligne rendue.",
        "DERNIER ESSAI : sois nettement plus agressif — une seule ligne par puce, "
        "phrases nominales sans verbe conjugué, et retire les puces redondantes.",
    )

    def _instruction_reduction(self, diag: dict, pages_max: int, essai: int) -> str:
        """Consigne CHIFFRÉE de réduction, construite à partir du rendu PDF réel."""
        palier = self._PALIERS_REDUCTION[min(essai, len(self._PALIERS_REDUCTION)) - 1]
        ou = ""
        if diag.get("sections_debord"):
            ou = (" Le contenu rejeté hors page appartient à la ou les sections : "
                  + ", ".join(diag["sections_debord"]) + ".")
        return (f"Ce document fait {diag['pages']} page(s) alors qu'il doit tenir sur {pages_max}. "
                f"Il déborde de {diag['lignes_debord']} ligne(s) de texte, et il ne reste que "
                f"{diag['hauteur_libre_pt']:.0f} pt de libre en bas de la page {pages_max}."
                f"{ou}\n"
                f"OBJECTIF CHIFFRÉ : supprime l'équivalent d'environ "
                f"{diag['lignes_a_couper']} lignes de texte rendu. {palier}")

    # En dessous, le blanc restant ne vaut pas un appel au modèle (ni le risque de le casser).
    MIN_LIGNES_ENRICHISSEMENT = 3

    def _instruction_enrichissement(self, diag: dict, pages_max: int) -> str:
        """Consigne chiffrée quand le document tient mais laisse trop de blanc en bas."""
        return (f"Ce document tient bien sur {pages_max} page(s), mais il ne la remplit qu'à "
                f"{diag['taux_remplissage']} % : il reste {diag['hauteur_libre_pt']:.0f} pt de blanc "
                f"en bas, soit environ {diag['lignes_libres']} lignes inutilisées. Un CV qui s'arrête "
                f"aux deux tiers de la page paraît pauvre.\n"
                f"OBJECTIF CHIFFRÉ : ajoute l'équivalent d'environ {max(2, diag['lignes_libres'] - 1)} "
                f"lignes de texte rendu, SANS JAMAIS dépasser {pages_max} page(s).\n"
                f"Tu n'inventes RIEN : tu ne fais que RE-DÉTAILLER ce qui a été trop condensé — "
                f"redonner aux puces les plus importantes la précision qu'elles avaient (technologies "
                f"employées, résultat obtenu, contexte), et rétablir des phrases complètes là où le "
                f"texte est devenu télégraphique. Aucune nouvelle expérience, aucune nouvelle "
                f"compétence, aucun chiffre inventé.")

    def _compiler_ajuste(self, tex: str, dossier: Path, nom: str, pages_max: int, type_doc: str,
                         essais: int = 4, repli=None) -> Path:
        """
        Compile le LaTeX de DeepSeek. Erreur LaTeX -> GPT corrige. PDF trop long -> GPT condense
        avec un OBJECTIF CHIFFRÉ mesuré sur le rendu (voir `mesurer_debordement`).

        Ne renvoie JAMAIS un document trop long : si les essais échouent, on se rabat sur
        `repli()` (document original, non personnalisé) et, à défaut, on lève ErreurGeneration.
        """
        (dossier / f"{nom}_deepseek.tex").write_text(tex, encoding="utf-8")   # trace de la version brute
        enrichi = False          # l'enrichissement n'est tenté qu'une fois
        tex_sur = None           # dernière version qui tenait dans la limite (filet de sécurité)
        self._latex_secours_actif = False   # armé au 1er échec : DeepSeek prend alors la main
        for essai in range(1, essais + 1):
            try:
                pdf = self._compiler(tex, dossier, nom)
            except ErreurLatex as e:
                # L'enrichissement est un CONFORT : s'il casse le LaTeX, on ne sacrifie pas un
                # document déjà valide pour quelques lignes de blanc. Retour immédiat à la
                # version sauvegardée, sans brûler les essais restants.
                if enrichi and tex_sur is not None:
                    self.logger.warning(f"{type_doc} (DeepSeek) : l'enrichissement a produit du LaTeX "
                                        f"invalide -> retour à la version valide précédente.")
                    return self._compiler(tex_sur, dossier, nom)
                # Le LaTeX ne compile pas : la réécriture précédente est en cause. Si un moteur
                # de secours est branché, on lui passe la main dès maintenant — insister avec
                # celui qui vient d'échouer donnait trois erreurs d'affilée et l'offre perdue.
                if not self._latex_secours_actif and getattr(self, "_secours_latex", None) is not None:
                    self._latex_secours_actif = True
                    self.logger.warning(f"{type_doc} essai {essai} : LaTeX non compilable -> DeepSeek "
                                        f"prend la main (aucun nouvel essai du moteur qui a échoué).")
                else:
                    self.logger.warning(f"{type_doc} essai {essai} : erreur LaTeX, correction…")
                tex = self._reecrire(tex, f"Ce document a une ERREUR de compilation à corriger "
                                          f"(sans rien changer d'autre) :\n{e}\n{e.extrait_log}")
                continue
            # pages == 0 = PDF illisible : ce n'est PAS « 0 ≤ 1 donc bon », c'est un échec.
            pages = compter_pages(pdf)
            if not pages_max or not self.reduire_1page or 1 <= pages <= pages_max:
                diag = mesurer_debordement(pdf, pages_max)
                taux = diag.get("taux_remplissage", 0)
                # Le document tient, mais REMPLIT-IL la page ? On se fie au nombre de lignes
                # RÉELLEMENT disponibles, pas au pourcentage : sur une lettre, le texte descend
                # jusqu'en bas (signature) alors que le taux reste bas à cause des grandes marges.
                # Déclencher sur le taux seul demandait « ajoute environ 0 lignes » — un appel
                # inutile, qui en plus exposait un document valide à une réponse cassée du modèle.
                place = diag.get("lignes_libres", 0)
                if (pages_max and taux < self.remplissage_min and place >= self.MIN_LIGNES_ENRICHISSEMENT
                        and not enrichi and essai < essais):
                    enrichi = True
                    tex_sur = tex
                    self.logger.info(
                        f"{type_doc} (DeepSeek) : {pages} page(s) mais remplie à {taux} % seulement "
                        f"({diag['hauteur_libre_pt']:.0f} pt vides, ~{diag['lignes_libres']} lignes) "
                        f"-> enrichissement.")
                    tex = self._reecrire(tex, self._instruction_enrichissement(diag, pages_max))
                    continue
                self.logger.info(f"{type_doc} (DeepSeek) : PDF prêt ({pages} page(s), "
                                 f"remplie à {taux} %) -> {pdf}")
                return pdf
            # L'enrichissement a fait déborder : on revient à la version courte qui tenait.
            if enrichi and tex_sur is not None:
                self.logger.info(f"{type_doc} (DeepSeek) : l'enrichissement déborde "
                                 f"-> retour à la version précédente qui tenait.")
                return self._compiler(tex_sur, dossier, nom)
            diag = mesurer_debordement(pdf, pages_max)
            self.logger.info(
                f"{type_doc} (DeepSeek) essai {essai}/{essais} : {pages} page(s), déborde de "
                f"{diag['lignes_debord']} ligne(s) ({', '.join(diag['sections_debord']) or 'section ?'}) "
                f"-> consigne : couper ~{diag['lignes_a_couper']} ligne(s).")
            if essai == essais:
                break
            tex = self._reecrire(tex, self._instruction_reduction(diag, pages_max, essai))

        # Aucun essai n'a tenu dans la limite : on n'envoie PAS un document trop long.
        if repli is not None:
            self.logger.warning(f"{type_doc} : toujours trop long après {essais} essais "
                                f"-> repli sur le document ORIGINAL (non personnalisé).")
            pdf_repli = repli()
            pages_repli = compter_pages(pdf_repli)
            if not pages_max or pages_repli <= pages_max:
                self.logger.info(f"{type_doc} : repli OK ({pages_repli} page(s)) -> {pdf_repli}")
                self.replis.append(f"{type_doc} ORIGINAL utilisé (réduction à {pages_max} page(s) "
                                   f"impossible après {essais} essais)")
                return pdf_repli
            self.logger.warning(f"{type_doc} : le document original fait lui-même {pages_repli} page(s).")
        raise ErreurGeneration(
            f"{type_doc} : impossible de tenir sur {pages_max} page(s) après {essais} essais"
            + (" (document original trop long lui aussi)" if repli is not None else ""))

    def generer_via_deepseek(self, offre: dict, dossier: Path, nav) -> tuple[Path, Path]:
        """
        Génère CV + lettre via le chat DeepSeek (piloté par `nav`), puis compile chacun en PDF
        (correction + réduction 1 page par GPT si besoin). Renvoie (cv_pdf, lm_pdf).
        """
        # import local : n'impose pas DeepSeek si non utilisé
        from deepseek_chat import ChatDeepSeek, ClientDeepSeekDocuments
        dossier.mkdir(parents=True, exist_ok=True)
        self.replis = []          # documents pour lesquels on a dû revenir à l'original
        if str((self.config.get("deepseek_chat") or {}).get("mode_cv", "latex")).lower() == "gabarit":
            return self._generer_gabarit(offre, dossier, nav)
        cv_orig = self._lire(self.cv_original_path)
        lm_orig = self._lire(self.lm_original_path)
        bloc_inputs = (
            f"=== OFFRE ===\n"
            f"Entreprise : {offre.get('nom_entreprise', '')}\nIntitulé : {offre.get('titre_poste', '')}\n"
            f"Lieu : {offre.get('lieu', '')}\n\n{offre.get('description_entreprise', '')}\n\n"
            f"{offre.get('description_offre', '')}\n\n"
            f"=== CV ORIGINAL (LaTeX) ===\n{cv_orig}\n\n"
            f"=== LETTRE DE MOTIVATION ORIGINALE (LaTeX) ===\n{lm_orig}\n\n"
            # Premier filet, côté modèle. Le second, mécanique, est `neutraliser_liens()` :
            # une consigne n'est qu'une demande, la neutralisation est une garantie.
            f"=== CONSIGNE DE FORME ===\n"
            f"Aucun lien cliquable dans les documents. Les coordonnées (email, GitHub, "
            f"LinkedIn, téléphone) et toute adresse web s'écrivent en TEXTE SIMPLE : "
            f"n'utilise ni \\href, ni \\url, ni mailto:. Le texte affiché reste identique.\n"
        )
        # UNE seule conversation de documents pour toute la session : on garde l'instance,
        # sinon chaque offre en rouvrait une (et renvoyait les 3 messages privés à chaque fois).
        if self._chat_documents is None:
            self._chat_documents = ChatDeepSeek(self.config, nav, self.logger)
        # Les corrections LaTeX doivent se faire DANS cette conversation-là : c'est elle qui a
        # rédigé le document. Sans ça, la clé API morte les envoyait au chat du FORMULAIRE,
        # qui n'a ni le CV ni l'offre — d'où des réécritures incompilables à répétition.
        self._secours_latex = ClientDeepSeekDocuments(self._chat_documents, self.logger)
        cv_tex, lm_tex = self._chat_documents.generer(bloc_inputs)
        # Le CV original est complet et tient sur une page : c'est un repli sûr. La lettre
        # originale, elle, est un MODÈLE à trous ([NOM DE L'ENTREPRISE]…) : jamais de repli.
        # Chaque document a SON original de référence : ses blocs protégés (coordonnées)
        # seront remis à l'identique à chaque compilation, quoi qu'ait écrit le modèle.
        try:
            self._origine_protegee = cv_orig
            cv_pdf = self._compiler_ajuste(cv_tex, dossier, self.nom_fichier("CV", offre),
                                           self.pages_max_cv, "CV",
                                           repli=lambda: self.compiler_cv_original(dossier, offre))
            self._origine_protegee = lm_orig
            lm_pdf = self._compiler_ajuste(lm_tex, dossier, self.nom_fichier("LM", offre),
                                           self.pages_max_lm, "Lettre de motivation")
        finally:
            self._origine_protegee = ""
        return cv_pdf, lm_pdf

    def _generer_gabarit(self, offre: dict, dossier: Path, nav) -> tuple[Path, Path]:
        """
        Mode « gabarit à trous » (config/cv_gabarit) : DeepSeek n'écrit que les textes, la
        mise en page est fixe. Le programme mesure et fait ajuster jusqu'à une page exacte.
        En cas d'échec du CV : repli sur le CV original (input/cv_original.tex).
        """
        from cv_gabarit import ErreurGabarit, GabaritCV
        from deepseek_chat import ChatDeepSeek
        if self._chat_documents is None:
            self._chat_documents = ChatDeepSeek(self.config, nav, self.logger)
        if getattr(self, "_gabarit", None) is None:
            self._gabarit = GabaritCV(self._compiler, self.logger)
        nom_cv, nom_lm = self.nom_fichier("CV", offre), self.nom_fichier("LM", offre)
        try:
            return self._gabarit.generer(offre, dossier, self._chat_documents, nom_cv, nom_lm)
        except ErreurGabarit as e:
            if "lettre" in str(e):
                raise ErreurGeneration(f"Lettre de motivation : {e}") from e
            self.logger.warning(f"Gabarit : {e} -> CV original utilisé.")
            self.replis.append(f"CV ORIGINAL utilisé ({e})")
            return self.compiler_cv_original(dossier, offre), dossier / f"{nom_lm}.pdf"

    def generer_lm(self, offre: dict, dossier: Path) -> Path:
        """Produit la lettre de motivation adaptée à l'offre et renvoie le chemin du PDF."""
        dossier.mkdir(parents=True, exist_ok=True)
        original = self._lire(self.lm_original_path)
        cv = self._lire(self.cv_original_path)
        profil_utile = {
            "identite": {k: (self.profil.get("identite") or {}).get(k) for k in ("prenom", "nom")},
            "formation": self.profil.get("formation"),
            "stage": self.profil.get("stage"),
            "langues": self.profil.get("langues"),
        }
        date = datetime.now()
        date_fr = f"{date.day} {MOIS[date.month - 1]} {date.year}"

        def message() -> str:
            return (
                f"<lm_modele>\n{original}\n</lm_modele>\n\n"
                f"<cv>\n{cv}\n</cv>\n\n"
                f"<profil>\n{yaml.safe_dump(profil_utile, allow_unicode=True, sort_keys=False)}</profil>\n\n"
                f"{self._bloc_offre(offre)}\n\n"
                f"<date_du_jour>{date_fr}</date_du_jour>\n\n"
                f"{self._bloc_mentions(self.chaines_lm)}"
            )

        def verifications(tex: str) -> list[str]:
            return (verifier_chaines(original, tex, self.chaines_lm) + verifier_duree_lm(tex)
                    + verifier_modele_rempli(tex))

        return self._boucle_generation("Lettre de motivation", self.prompt_lm, message, original,
                                       verifications, dossier, self.nom_fichier("LM", offre), self.pages_max_lm)
