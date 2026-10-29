# GUIDE IA — Données personnelles du candidat : où elles vivent, comment les faire remplir

> **À toi, l'IA (Claude Code ou autre) qui lis ce fichier.**
> Ce projet est un robot qui postule seul à des offres d'emploi et de stage, avec le CV, la lettre et les
> informations d'**une** personne. Ton travail : faire entrer les informations personnelles de l'utilisateur
> qui te parle, **question par question**, puis écrire chaque réponse **à sa place exacte** dans les
> fichiers du projet, sans casser leur structure.
>
> Ce guide ne contient **aucune donnée personnelle** : seulement des emplacements, des formats et des
> questions. Les valeurs actuelles (celles d'un candidat précédent) se lisent dans les fichiers eux-mêmes.
>
> **Lis ce guide en entier avant ta première question.** Tous les chemins sont relatifs à la racine du
> projet (le dossier qui contient ce fichier).

---

## 0. Règles non négociables

1. **Une question à la fois.** Donne un exemple du format attendu, attends la réponse, valide-la (§ 6),
   puis passe à la suivante. Annonce la section et la progression
   (« Section 3/14 — Formation, question 2/6 » ; la section 0, documents d'origine, vient avant la 1).
2. **Tu n'inventes rien.** Chaque valeur écrite vient de l'utilisateur. Champ facultatif sans réponse :
   valeur vide `""`, jamais une valeur inventée. Pour les textes du CV (§ 4.7), tu peux **reformuler** ce que
   l'utilisateur te dit, jamais ajouter un fait, un chiffre, une technologie ou une expérience.
3. **Récapitulatif avant écriture.** À la fin de chaque section : montre les réponses de la section,
   demande « Je les écris ? (o/n) », et n'écris qu'après un « o ».
4. **Sauvegarde avant de modifier.** Avant ta première écriture, copie chaque fichier marqué ✎ ou ⚙ au § 2
   dans `config/sauvegardes_formulaire/<AAAA-MM-JJ_HHhMM>/`, en gardant son chemin relatif.
5. **Ne casse pas les fichiers.** Garde l'encodage UTF-8, les fins de ligne d'origine (CRLF ou LF), l'ordre
   des clés et les commentaires. Tu changes une **valeur**, jamais la structure (sauf cas prévus ici).
6. **Secrets** (mots de passe, clés API) : ne les demande **jamais** dans la conversation, ne les affiche
   jamais, et ne fais jamais de recherche (grep) dans un fichier `.env`. L'utilisateur les saisit lui-même
   (§ 4.12).
7. **Ne lance jamais le robot en envoi réel** pendant l'installation : ni `LANCER.bat` → 4 ou 5, ni
   `scripts\orchestrator.py` sans `--verifier`. Ces modes envoient de **vraies** candidatures avec les comptes
   connectés dans les navigateurs. Avant de commencer, vérifie qu'aucun robot ne tourne :
   `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object CommandLine`
   → aucune ligne ne doit contenir `orchestrator.py`, `verify_eligibility.py` ou `scraping.py`.
8. **Rien n'est supprimé définitivement.** Tout ce qui est retiré part à la Corbeille, après accord
   explicite de l'utilisateur (§ 7).
9. **Ne modifie pas le code** (`scripts/*.py`) ni les réglages techniques de `config/config.yaml`, sauf les
   lignes nommées dans ce guide (§ 4.13 et § 5).

---

## 1. Avant la première question

1. Lis ce guide en entier.
2. Vérifie qu'aucun robot ne tourne (règle 7).
3. **Relève les valeurs actuelles** (celles du candidat précédent). Tu en auras besoin à la fin pour vérifier
   qu'il n'en reste aucune trace (§ 8). Lis : `config/profile.yaml`, `config/objectif.yaml`,
   `config/cv_gabarit/fixe_fr.json`, `config/cv_gabarit/lettre_fixe.json`, `config/cv_gabarit/reference_en.json`,
   le bloc `HEADER` et le bloc `EDUCATION` de `config/cv_gabarit/gabarit.tex`, la signature de
   `config/cv_gabarit/lettre.tex`, `input/Scraping/criteres_domaine.json`.
   Garde ces valeurs pour toi : ne les récite pas à l'utilisateur.
4. Fais la sauvegarde (règle 4).
5. Copie le script de l'**annexe A** dans un fichier temporaire **hors du projet**
   (ex. `%TEMP%\outils_ia.py`). Toutes ses commandes se lancent **depuis la racine du projet**, avec le
   Python du projet : `.venv\Scripts\python.exe -B %TEMP%\outils_ia.py <commande>`.
   Sans `--ecrire`, il n'écrit rien (simulation).
6. Explique à l'utilisateur, en 3 lignes, ce qui va se passer : la section 0 (photo, CV et lettre d'origine)
   puis 14 sections, environ 45 minutes, on peut s'arrêter et reprendre. Commence par la section 0 (§ 4.0) :
   s'il donne son CV et sa lettre, tu les lis pour **pré-remplir** les sections 3 à 9, et il n'a plus qu'à
   **confirmer** chaque élément, une question à la fois (« J'ai lu dans ton CV : … C'est correct ? »).

> Remarque : `FORMULAIRE.bat` fait une partie de ce travail en console (sections 1 à 11). Ce guide le
> remplace et couvre en plus le nettoyage et les emplacements écrits en dur.

---

## 2. Carte du projet : où vivent les données personnelles

Légende : ✎ tu écris dedans · ⚙ généré par l'outil de l'annexe A · 🔒 secret, saisi par l'utilisateur
lui-même · 🗑 trace d'un candidat précédent (§ 7) · · aucune donnée personnelle.

```
<racine du projet>/
├── LANCER.bat .................................. ✎ 1 ligne : email de candidature affiché (menu 8) — § 5-C
├── PASSATION_CLAUDE.md ......................... 🗑 documentation écrite pour le candidat d'origine
├── README.md, GUIDE_*.md ....................... · exemples fictifs uniquement
├── job-automation.rar (s'il existe) ............ 🗑 archive complète d'un ancien état du projet
├── .env ........................................ 🔒 mots de passe des sites + clés API — § 4.12
├── config/
│   ├── profile.yaml ............................ ✎ identité, liens, formation, langues, réponses types
│   ├── objectif.yaml ........................... ✎ ce que le candidat cherche
│   ├── config.yaml ............................. ✎ commentaire email, garde_fous, profils Chrome — § 5-D ; 🔒 clés
│   ├── modeles/ ................................ · structure exacte des fichiers (ne pas modifier)
│   ├── cv_gabarit/
│   │   ├── gabarit.tex ......................... ✎ CV à trous : en-tête, établissements, certifications, langues
│   │   ├── fixe_fr.json, fixe_en.json .......... ✎ parties FIXES du CV (français / anglais)
│   │   ├── reference_en.json ................... ✎ textes VARIABLES de référence (anglais) = longueurs cibles
│   │   ├── lettre.tex .......................... ✎ lettre à trous : signature (nom, email, téléphone)
│   │   ├── lettre_fixe.json .................... ✎ parties fixes de la lettre (ville, objet…)
│   │   ├── exemple_fr.json ..................... ✎ exemple en français (non lu par le robot) — § 4.7
│   │   └── apercu_cv_fr/en .pdf/.png ........... ⚙ aperçus du CV (commande `apercus`)
│   └── deepseek/
│       ├── nouveaux/message_1.txt .............. ✎ règles d'écriture pour DeepSeek : seulement les éléments personnels — § 4.10
│       ├── nouveaux/message_2.txt .............. ✎ « MES FAITS RÉELS » : toute la matière du CV — § 4.9
│       ├── nouveaux/message_3.txt .............. · format des réponses : à garder tel quel — § 4.10
│       └── message_1.txt, message_2.txt, message_3.txt (hors nouveaux/) ... 🗑 anciennes versions, non utilisées
├── input/
│   ├── cv_original.tex, lm_original.tex ........ ✎ CV et lettre d'origine : déposés (§ 4.0) ou générés (`originaux`)
│   ├── photo.jpg ............................... ✎ photo du CV, facultative — § 4.0 (commande `photo`), annexe C
│   ├── GitHub.png, LinkedIn.png ................ · icônes
│   ├── entreprises.xlsx ........................ 🗑 suivi des offres (repart vide) — § 7
│   ├── sauvegardes/ ............................ 🗑 copies horaires de l'Excel
│   ├── ancien/ ................................. 🗑 anciennes versions du CV, de la lettre, de l'Excel
│   └── Scraping/
│       ├── .env ................................ 🔒 clés Apify
│       ├── criteres_domaine.json ............... ✎ domaine recherché (tri des offres) — § 4.11
│       ├── liens.txt ........................... ✎ recherches LinkedIn surveillées — § 4.11
│       ├── config_scraping.json ................ · réglages du scraping
│       └── offres_scrapees.json, logs/ ......... 🗑 offres déjà récupérées pour le candidat précédent
├── images/ ..................................... 🗑 ancien CV maître et ancienne photo
├── memoire/
│   ├── playbooks.json .......................... 🗑/✎ valeurs RETAPÉES dans les formulaires — § 7
│   ├── lecons_sites.json ....................... ✎ leçons par site (quelques détails personnels) — § 7
│   ├── regles_sites.json ....................... 🗑 sites où le candidat précédent a un compte — § 7
│   └── etat.json ............................... · date du dernier rapport
├── extensions/nopecha/manifest.json ............ 🔒 uniquement le champ "nopecha" → "key"
├── output/Candidatures/, output/captures/ ...... 🗑 candidatures et captures du candidat précédent
├── logs/, rapports/ ............................ 🗑 journaux et rapports du candidat précédent
├── prompts/ .................................... ✎ seulement si le pays ou l'indicatif change — § 5-E
└── scripts/ .................................... · code (ne pas modifier, sauf § 4.13)

Hors du projet : %USERPROFILE%\.tappi\job-automation, -2, -3, -4, -verif  → profils Chrome (comptes connectés)
```

**Ce qui se met à jour tout seul** (rien à faire) : le masquage des données envoyées à DeepSeek
(`scripts/confidentialite.py`), les phrases d'objectif des consignes (`scripts/objectif.py` remplit les
repères `[[…]]` de `prompts/`), les noms des fichiers CV/LM, la boîte Gmail visée. Tout cela est lu dans
`profile.yaml` et `objectif.yaml`.

---

## 3. Déroulé de l'entretien

| N° | Section | Écrit dans |
|---|---|---|
| 0 | Documents d'origine : photo, CV, lettre | `input/photo.jpg` + `gabarit.tex`, `input/cv_original.tex`, `input/lm_original.tex` (outil : `photo`, `deposer`, `texte`) |
| 1 | Identité | `profile.yaml` + en-tête du CV + signature de la lettre + `LANCER.bat` + `config.yaml` |
| 2 | Liens | `profile.yaml` + en-tête du CV |
| 3 | Formation | `profile.yaml` + `fixe_fr/en.json` + `gabarit.tex` |
| 4 | Objectif | `objectif.yaml` + `profile.yaml` + `fixe_fr/en.json` + `lettre_fixe.json` + `config.yaml` |
| 5 | Langues | `profile.yaml` + `fixe_fr/en.json` + `gabarit.tex` |
| 6 | Réponses types des formulaires | `profile.yaml` |
| 7 | Contenu du CV | `fixe_fr/en.json`, `reference_en.json`, `gabarit.tex`, `exemple_fr.json` |
| 8 | Lettre | `lettre_fixe.json`, `lettre.tex` |
| 9 | Faits réels | `config/deepseek/nouveaux/message_2.txt` |
| 10 | Message 1 (éléments personnels), CV et lettre de secours | `message_1.txt` ; outil : `originaux` |
| 11 | Domaine et recherches | `criteres_domaine.json`, `liens.txt` |
| 12 | Secrets | saisis par l'utilisateur : `.env`, `input/Scraping/.env`, `manifest.json`, `config.yaml` |
| 13 | Navigateurs et comptes | fait par l'utilisateur (profils Chrome) |
| 14 | Nettoyage et vérification | § 7 et § 8 |

Écris la section 0, puis les sections 1 à 6, dès qu'elles sont validées (récapitulatif + « o »). Les documents (7 à 10) se
contrôlent ensemble avec la commande `controle` (§ 8).

---

## 4. Fiches par section

### 4.0 Documents d'origine — photo, CV, lettre de motivation (section 0)

Pose ces trois questions **en premier** : les documents de l'utilisateur te servent ensuite à **pré-remplir**
les sections 3 à 9. Il n'a plus qu'à confirmer chaque élément, une question à la fois
(« J'ai lu dans ton CV : … C'est correct ? »).

**Comment le robot s'en sert** (ne change rien à cette logique) :

| Document | Emplacement exact | Rôle dans le robot |
|---|---|---|
| Photo | `input/photo.jpg`, appelée dans `config/cv_gabarit/gabarit.tex`, bloc `HEADER`, par `\includegraphics[width=2.8cm]{photo.jpg}` | apparaît sur **chaque** CV généré. Le robot compile chaque CV avec `input/` comme dossier de ressources : la photo n'est jamais copiée ailleurs, et son nom doit être **exactement** celui écrit dans le gabarit |
| CV d'origine | `input/cv_original.tex` (`config.yaml` → `chemins.cv_original`) | fichier **obligatoire**. CV de **secours**, envoyé tel quel si la génération échoue. Au démarrage, le robot vérifie que prénom, nom, email et `garde_fous.chaines_obligatoires_cv` y figurent |
| Lettre d'origine | `input/lm_original.tex` (`config.yaml` → `chemins.lm_original`) | fichier **obligatoire** au démarrage. En mode `gabarit` (réglage actuel, `deepseek_chat.mode_cv`), chaque lettre est construite avec `lettre.tex` + `lettre_fixe.json` + 3 paragraphes écrits par DeepSeek |
| Contenu du CV et de la lettre | lu par **toi** | source des réponses des sections 3 à 9 (formation, objectif, langues, contenu du CV, lettre, faits réels) |

Le CV envoyé pour chaque offre n'est **pas** une copie de `cv_original.tex` : il est fabriqué avec `gabarit.tex`
(mise en page + photo) + `fixe_fr/en.json` + les textes que DeepSeek écrit d'après `message_2.txt` (§ 4.7, § 4.9).

**Pour recevoir un fichier** : demande son **chemin complet** (dans l'Explorateur Windows : Maj + clic droit sur
le fichier → « Copier en tant que chemin d'accès », puis coller ici). Une image glissée dans la conversation
n'est pas forcément un fichier sur le disque : redemande alors son chemin. Tu **copies** toujours, tu ne
déplaces jamais : le fichier de l'utilisateur reste où il est, intact.

| N° | Question | Action (outil de l'annexe A, simulation puis `--ecrire` après accord) |
|---|---|---|
| 0.1 | « Veux-tu une photo sur ton CV ? Si oui, donne le chemin de la photo (.jpg, .jpeg, .png ou .webp). » Photo d'iPhone en `.heic` : qu'il l'exporte d'abord en JPG | oui : `photo "<chemin>"`, puis `photo "<chemin>" --ecrire` · non : `photo --aucune` (puis `--ecrire` si le gabarit contient encore une photo) |
| 0.2 | « As-tu ton CV actuel ? Donne son chemin (.tex de préférence, sinon .pdf ou .docx), ou réponds non. » | `.tex` : `deposer --cv "<chemin>"`, puis `--ecrire` · `.pdf` / `.docx` : `texte "<chemin>"` pour le lire, sans le copier dans le projet · non : rien |
| 0.3 | « As-tu une lettre de motivation ? Donne son chemin (.tex de préférence, sinon .pdf ou .docx), ou réponds non. » | `.tex` : `deposer --lm "<chemin>"`, puis `--ecrire` · `.pdf` / `.docx` : `texte "<chemin>"` · non : rien |

**`photo`** : remet à l'endroit une photo de téléphone, met un fond blanc si elle est transparente, la
**recadre** au format pour lequel l'en-tête est calibré (2,8 × 3,16 cm, en gardant le haut de l'image),
l'enregistre en `input/photo.jpg` et place le bloc photo dans le gabarit (annexe C). La simulation compile le CV
et doit annoncer **1 page** et **1 image**. Le cadrage se vérifie sur l'aperçu (section 10, puis
`apercus --ecrire` : `config\cv_gabarit\apercu_cv_en.png`). S'il ne convient pas, demande une autre photo,
déjà recadrée par l'utilisateur. Les anciennes photos restées dans `input/` (la commande les liste) sont à
proposer pour la Corbeille (§ 7). `input/GitHub.png` et `input/LinkedIn.png` sont des icônes : à garder.

**`deposer`** : copie le `.tex` **octet pour octet** en `input/cv_original.tex` (ou `input/lm_original.tex`),
avec les images, `.cls` et `.sty` qu'il appelle et qui sont rangés à côté de lui. Si le document appelle une
photo absente (ex. `moi.jpg`), il y met la photo placée en 0.1, sous ce nom. Il compile comme le robot et
annonce le nombre de pages :
- « ATTENTION : 2 pages » ou « images introuvables » → n'écris pas. Explique-le à l'utilisateur : il corrige
  son fichier (ou fournit l'image manquante), ou bien on ne dépose rien et la section 10 génère le document
  (`originaux`) dans la mise en page des CV du robot ;
- un `.tex` déposé n'est plus jamais remplacé : en section 10, `originaux … --garder cv` (et/ou `lm`) ;
- après la section 1, `controle` vérifie que prénom, nom et email figurent tels quels dans
  `input/cv_original.tex`. Sinon, dis-le : son CV doit porter les mêmes coordonnées que le profil.

**`.pdf` ou `.docx`** : le robot ne compile que du LaTeX, la mise en page d'un PDF ne peut donc pas être
reprise telle quelle. Seul son **contenu** sert (pré-remplissage). `input/cv_original.tex` et
`input/lm_original.tex` sont alors générés en section 10 par `originaux`, dans la mise en page des CV du robot
(avec la photo s'il y en a une).

**La lettre** : ses paragraphes donnent la lettre type (§ 4.8). Sa ville et sa signature pré-remplissent
`lettre_fixe.json` (§ 4.8) et la signature de `lettre.tex` (§ 5-B).

**Garder SA propre mise en page pour tous les CV** (au lieu de celle du gabarit actuel) : c'est le travail de
`FORMULAIRE.bat` → section « CV, lettre de motivation et photo ». DeepSeek y transforme son CV et sa lettre
(LaTeX ou PDF) en gabarits, avec aperçus à valider ; il doit être connecté (§ 4.13). Propose-le seulement si
l'utilisateur le demande, après la section 13, puis relance `controle`.

### 4.1 Identité — `config/profile.yaml` → bloc `identite:`

| Clé | Question à poser | Format | Où écrire (toutes les places) |
|---|---|---|---|
| `civilite` | « Ta civilité ? » | `M.` / `Mme` / vide | `identite.civilite` |
| `genre` | « Ton genre ? (certains formulaires le demandent ; sert aussi aux accords) » | `Homme` / `Femme` | `identite.genre` |
| `prenom` | « Ton prénom ? » | texte | `identite.prenom` ; en-tête du CV (§ 5-A) ; signature de la lettre (§ 5-B) ; puis `message_1.txt` (§ 4.10) et commande `originaux` |
| `nom` | « Ton nom de famille ? » | texte | `identite.nom` ; mêmes endroits que le prénom |
| `email` | « L'email avec lequel tu postules (celui du compte Google connecté dans les navigateurs du robot) ? » | email valide | `identite.email` ; en-tête du CV ; signature de la lettre ; `LANCER.bat` (§ 5-C) ; commentaire de `config.yaml` (§ 5-D) |
| `telephone` | « Ton téléphone, écrit exactement comme il doit apparaître sur ton CV ? » | chiffres, espaces, `+` | `identite.telephone` ; en-tête du CV (2 fois) ; signature de la lettre |
| `adresse` | « Ton adresse (rue ou quartier) ? » | texte | `identite.adresse` |
| `code_postal` | « Ton code postal ? » | texte | `identite.code_postal` |
| `ville` | « Ta ville de résidence ? » | texte | `identite.ville` ; `lettre_fixe.json` (§ 4.8) |
| `pays` | « Ton pays de résidence ? » | texte | `identite.pays` ; `lettre_fixe.json` (§ 4.8) ; si l'indicatif téléphonique change : § 5-E |
| `nationalite` | « Ta nationalité ? » | texte | `identite.nationalite` |
| `date_naissance` | « Ta date de naissance ? (facultatif) » | `JJ/MM/AAAA` | `identite.date_naissance` |

Les deux autres écritures du téléphone (sans indicatif, avec indicatif), utilisées dans les formulaires,
sont demandées en § 4.6.

### 4.2 Liens — `config/profile.yaml` → bloc `liens:`

| Clé | Question | Format | Où écrire |
|---|---|---|---|
| `linkedin` | « Le lien de ton profil LinkedIn ? » | `https://www.linkedin.com/in/…/` | `liens.linkedin` ; en-tête du CV (§ 5-A) |
| `github` | « Ton GitHub ? (facultatif) » | `https://…` ou vide | `liens.github` ; en-tête du CV (§ 5-A) |
| `portfolio` | « Un portfolio ou site personnel ? (facultatif) » | `https://…` ou vide | `liens.portfolio` (absent du CV, sauf demande de l'utilisateur) |

Dans l'en-tête du CV : l'URL complète va dans `\href{…}`, le texte affiché est écrit **sans** `https://`,
et chaque `_` du texte affiché devient `\_` (pas dans l'URL). Sans GitHub : retire son
`\textbf{\href{…}{…}}` et le séparateur ` \quad\textbar\quad ` de la ligne.

### 4.3 Formation

**`config/profile.yaml` → bloc `formation:`**

| Clé | Question | Exemple de format |
|---|---|---|
| `ecole` | « Ton école ou université, avec la ville ? » | « <Nom complet de l'école>, <Ville> » |
| `diplome` | « Le diplôme préparé ou obtenu ? » | « Diplôme d'ingénieur », « Master 2 » |
| `specialite` | « Ta spécialité ? » | « Informatique » |
| `annee_en_cours` | « Ton année d'études en cours (ou « Diplômé ») ? » | « 5ème année » |
| `date_obtention_prevue` | « L'année d'obtention, prévue ou réelle ? » | « 2027 » |

**Section Formation du CV** : 3 lignes dans la structure actuelle. Pour chaque formation *i* (1 à 3),
demande : la période (« 2024 - Présent »), l'intitulé, l'établissement et sa ville.

- `config/cv_gabarit/fixe_fr.json` → `F1`, `F2`, `F3` = « <période> : <intitulé> » ; `fixe_en.json` → même
  chose en anglais (« 2024 - Present: … »). Ce sont des valeurs **LaTeX** (§ 6).
- `F1_AXES` : libellé fixe (« Axes d'étude : » / « Focus areas: »), à garder. `F1_LISTE` : demande les
  matières principales de la formation 1, liste terminée par un point.
- `config/cv_gabarit/gabarit.tex` → bloc `EDUCATION` : le texte en gras **après `\hfill`** sur les lignes
  `\textbf{<<F1>>} …`, `\textbf{<<F2>>} …` et `\textbf{<<F3>>} …` est l'établissement, **écrit en dur**
  (§ 5-A).
- Ailleurs : la 3ᵉ ligne d'en-tête `ECOLE` (§ 4.7), `objectif.yaml` → `presentation_fr` (§ 4.4),
  `criteres_domaine.json` → `diplome` (§ 4.11), et la section FORMATION de `message_2.txt` (§ 4.9).
- Moins ou plus de 3 formations : supprime ou duplique les lignes du bloc `EDUCATION` de `gabarit.tex`
  (avec le `\vspace{1pt}` qui les sépare), et les clés `F…` correspondantes dans `fixe_fr.json` **et**
  `fixe_en.json`. Puis contrôle la page (§ 8).

### 4.4 Objectif — `config/objectif.yaml` (+ `profile.yaml` → `stage:`)

Pose les questions en langage courant, **calcule** toi-même les formes dérivées, et fais-les confirmer.

| Clé de `objectif.yaml` | Question / règle | Format |
|---|---|---|
| `type` | « Tu cherches un stage, une alternance ou un emploi ? » | `stage` / `alternance` / `emploi` |
| `contrats_acceptes` | « Quels contrats acceptes-tu ? » | liste parmi `stage`, `alternance`, `cdi`, `cdd`, `freelance` |
| `nom_fr` | « Ce que tu cherches, en quelques mots ? » | minuscules, sans point (ex. « stage de fin d'études ») |
| `nom_en` | traduction de `nom_fr` (fais-la confirmer) | ex. « end-of-studies internship » |
| `sigle` | « Un sigle ? (facultatif) » | ex. « PFE », « PFA », ou vide |
| `duree_fr` | « La durée ? (vide pour un CDI) » | ex. « 6 mois » |
| `duree_en`, `duree_adjectif_en` | dérivées | ex. « 6 months », « 6-month » |
| `date_debut_fr` | « La date de début ? » (demande `JJ/MM/AAAA`, vide = dès que possible) | « 1er mars 2027 » (`1er` pour le 1ᵉʳ du mois) |
| `date_debut_en` | dérivée | « March 1, 2027 » |
| `mois_debut_fr` | dérivée | « mars 2027 » |
| `conventionne` | « Stage sous convention de ton école ? » (seulement si `stage`) | `true` / `false` |
| `statut_fr` | « Ta situation en 2-3 mots ? » | minuscules (ex. « élève ingénieur ») |
| `statut_long_fr` | « Un peu plus précis ? » | ex. « élève-ingénieur en dernière année » |
| `presentation_fr` | « Qui tu es, en une demi-phrase ? » | majuscule initiale, sans point |

**`config/profile.yaml` → bloc `stage:`** : `type` (ex. « Stage PFE » = type avec majuscule + sigle),
`duree` (= `duree_fr`), `date_debut_souhaitee` (= `date_debut_fr`, ou « Dès que possible »),
`pays_cibles` (« Dans quels pays cherches-tu ? », liste), `mobilite` (« Ta mobilité, en une phrase ? »),
`convention_de_stage` (phrase pour les formulaires, vide si ce n'est pas un stage).

**Dépend aussi de l'objectif** (à mettre en cohérence dans la même section) :
- `fixe_fr.json` / `fixe_en.json` → `STAGE` (4ᵉ ligne d'en-tête du CV, ex. « Recherche d'un stage … de 6 mois
  à partir du 1\\textsuperscript{er} mars 2027 ») et `TITRE` (2ᵉ ligne d'en-tête : la situation) ;
- `lettre_fixe.json` → `fr.L_OBJET` et `en.L_OBJET` (garde `{poste}` à la fin) ;
- `config.yaml` → `garde_fous.chaines_obligatoires_cv` (§ 5-D) ;
- `criteres_domaine.json` → `diplome` (§ 4.11) ;
- `message_1.txt` : la 1re phrase, l'en-tête du CV et la règle « Profil » (§ 4.10).

### 4.5 Langues

- `config/profile.yaml` → `langues:` : une ligne par langue, `- "Langue : niveau"`.
- `config/profile.yaml` → `reponses_standard.niveau_anglais`, `niveau_francais`, `niveau_arabe` : niveau à
  choisir dans les menus des formulaires, parmi `Native or bilingual`, `Full professional`, `Professional`,
  `Limited working`, `Elementary`, ou `""` si la langue n'est pas parlée. **Garde ces trois clés**, même vides
  (le robot les lit). Ne jamais sous-déclarer.
- Section Langues du CV : 3 lignes `\item <<L_ARABE>>`, `\item <<L_FRANCAIS>>`, `\item <<L_ANGLAIS>>` dans
  `gabarit.tex`, dont les textes sont dans `fixe_fr.json` / `fixe_en.json`, au format
  `"\\textbf{Langue :} Niveau"`. Les noms de clés ne sont que des étiquettes : pour d'autres langues, change
  les **valeurs** (ou renomme la clé à l'identique dans `gabarit.tex` et dans les deux `fixe_*.json`).
  Pour 2 ou 4 langues, retire ou ajoute une ligne `\item` (et sa clé), puis contrôle la page (§ 8).

### 4.6 Réponses types des formulaires — `config/profile.yaml` → bloc `reponses_standard:`

| Clé | Question | Format |
|---|---|---|
| `autorisation_travail` | « Es-tu autorisé(e) à travailler dans le pays visé ? Réponse complète. » | phrase |
| `autorisation_travail_oui_non` | même question, courte | `Oui` / `Non` |
| `autorisation_travail_france_ou_ue_oui_non` | « Autorisé(e) à travailler en France / dans l'UE / en Europe ? » | `Oui` / `Non` |
| `autorisation_travail_maroc_oui_non` | **nom de clé historique : signifie « autorisé(e) à travailler dans ton PAYS DE RÉSIDENCE »**. Ne renomme pas la clé (le code la lit). | `Oui` / `Non` |
| `besoin_visa` | « Auras-tu besoin d'un visa ou d'un parrainage ? Réponse complète. » | phrase |
| `besoin_visa_oui_non` | même question, courte | `Oui` / `Non` |
| `telephone_sans_indicatif` | dérivée du téléphone : numéro national **sans indicatif et sans le 0 initial** (fais-la confirmer) | chiffres seuls |
| `telephone_avec_indicatif` | dérivée : « +<indicatif><numéro> », sans espaces (fais-la confirmer) | ex. `+33612345678` |
| `niveau_anglais`, `niveau_francais`, `niveau_arabe` | voir § 4.5 | liste fermée |
| `gratification_souhaitee` | « Ta rémunération souhaitée, en une phrase ? » | ex. « Selon la grille de l'entreprise » |
| `gratification_souhaitee_montant` | « La même en chiffres : montant mensuel brut en euros ? (facultatif) » | nombre ou vide |
| `comment_avez_vous_connu` | « Réponse à « Comment avez-vous connu l'offre ? » » | phrase |
| `consentement_recontact` | « Acceptes-tu d'être recontacté(e) pour d'autres offres ? (phrase) » | phrase |
| `handicap` | « Réponse à la question sur le handicap ? » | phrase |
| `permis_conduire` | « Permis de conduire ? (facultatif) » | ex. « Permis B » ou vide |

Le bloc `ne_jamais_communiquer:` reste tel quel. La structure complète de `profile.yaml` doit rester celle de
`config/modeles/profile.modele.yaml` (mêmes clés, même ordre).

### 4.7 Contenu du CV

Le CV est fabriqué à chaque offre à partir de trois éléments :
`gabarit.tex` (mise en page fixe) + `fixe_<langue>.json` (textes fixes) + les textes que DeepSeek écrit pour
les clés **variables**, dont la référence et la longueur cible sont dans `reference_en.json`.
Structure actuelle : en-tête de 4 lignes · Profil · 3 expériences · 4 projets · 3 formations ·
2 lignes de compétences · certifications · 3 langues · 3 soft skills.

| N° | Question | Où écrire |
|---|---|---|
| 7.1 | « Le titre sous ton nom (ta situation) ? » | `fixe_fr.json` / `fixe_en.json` → `TITRE` |
| 7.2 | « La ligne école (ta formation et ton école) ? » | → `ECOLE` (ex. « <spécialité> -- <École>, <Ville> ») |
| 7.3 | ligne objectif (dérivée du § 4.4, fais-la confirmer) | → `STAGE` |
| 7.4 | Pour **chaque expérience** (3 dans la structure actuelle) : entreprise et lieu, période, poste réel, réalisations réelles, technologies utilisées, résultat chiffré RÉEL (ou aucun) | `fixe_fr.json` → `STAGE1..3` (ex. « Stage chez <Entreprise> ») et `PERIODE1..3` (ex. « Janv. 2025 -- Juin 2025 ») ; `fixe_en.json` → mêmes clés en anglais (« Internship at <Company> », « Jan. 2025 -- Jun. 2025 ») ; `reference_en.json` → `E1_TITRE`, `E1_P`… ; `message_2.txt` (§ 4.9) |
| 7.5 | Pour **chaque projet** (4) : titre, 2 à 4 technologies, ce qui a été fait et le résultat | `reference_en.json` → `P1_TITRE`, `P1_TECH`, `P1_P`… ; `message_2.txt` |
| 7.6 | « Ton profil, en 3 ou 4 phrases ? » (aide-le à partir de ses réponses) | `reference_en.json` → `PROFIL` |
| 7.7 | Compétences : 2 lignes, chacune un libellé et une liste | `reference_en.json` → `SK1_LABEL`, `SK1`, `SK2_LABEL`, `SK2` (libellés terminés par « : ») |
| 7.8 | « Tes certifications ? (liste, ou aucune) » | `gabarit.tex` → bloc `CERTIFICATIONS`, écrit en dur (§ 5-A) |
| 7.9 | « Tes 3 qualités principales (soft skills) ? » | `fixe_fr.json` / `fixe_en.json` → `SS1`, `SS2`, `SS3` |
| 7.10 | Photo : déjà traitée en section 0 (§ 4.0) | rien à faire ici |

**`reference_en.json`** — en **anglais**, en JSON valide, valeurs **LaTeX** (dans le JSON : `\\&`, `\\%`,
`\\_`). Ces textes servent de **référence** : le robot mesure, dans le PDF, sur combien de lignes chacun tient,
et impose ensuite le même nombre de lignes aux textes de DeepSeek, ce qui garantit **1 page exacte**.
Longueurs cibles actuelles (caractères visibles), à respecter à ±15 % :

| Clé | Car. | Clé | Car. | Clé | Car. | Clé | Car. |
|---|---|---|---|---|---|---|---|
| `PROFIL` | 398 | `E1_TITRE` | 31 | `E2_TITRE` | 36 | `E3_TITRE` | 18 |
| `E1_P` | 311 | `E2_P` | 325 | `E3_P` | 314 | `SK1_LABEL` | 18 |
| `P1_TITRE` | 45 | `P2_TITRE` | 42 | `P3_TITRE` | 49 | `P4_TITRE` | 51 |
| `P1_TECH` | 22 | `P2_TECH` | 7 | `P3_TECH` | 19 | `P4_TECH` | 14 |
| `P1_P` | 176 | `P2_P` | 162 | `P3_P` | 180 | `P4_P` | 178 |
| `SK1` | 127 | `SK2_LABEL` | 28 | `SK2` | 223 | | |

Après écriture, la commande `controle` doit montrer : 1 page, et `PROFIL` 4 lignes, `E…_P` 3 lignes,
`P…_P` 2 lignes, `SK1` 2 lignes, `SK2` 3 lignes, titres 1 ligne.

**`exemple_fr.json`** (mêmes clés + `TITRE`, `SS1..3`, en français) n'est pas lu par le robot : remplace-le par
la version française des mêmes textes, ou mets-le à la Corbeille (§ 7).

**Autre nombre d'expériences ou de projets** : dans `gabarit.tex`, duplique ou supprime un bloc complet
(`\textbf{<<STAGEn>>} … \end{itemize}` ou `\textbf{<<Pn_TITRE>>} … \end{itemize}`, avec le `\vspace{1pt}`
qui suit) ; ajoute ou retire les clés `En_…` / `Pn_…` dans `reference_en.json` (et `exemple_fr.json`), et
`STAGEn` / `PERIODEn` dans les deux `fixe_*.json`. Mets aussi à jour le nombre et la liste des entreprises dans
`message_1.txt`, et les clés « E1_P » à « E…_P » / « P1_P » à « P…_P » de `message_3.txt`. Ajuste les longueurs
pour rester sur 1 page.

### 4.8 Lettre de motivation

**`config/cv_gabarit/lettre_fixe.json`** (blocs `fr` et `en`) :

| Clé | Contenu | À faire |
|---|---|---|
| `L_DATE` | `"<Ville>, le {date}"` (fr) / `"<City>, {date}"` (en) | mettre la ville de l'utilisateur, garder `{date}` |
| `L_OBJET` | ligne d'objet avec l'objectif | dériver du § 4.4, garder `{poste}` |
| `L_VILLE_SIGNATURE` | `"<Ville>, <Pays>"` (fr) / version anglaise (en) | ville et pays de l'utilisateur |
| `L_TITRE_DOC`, `L_SERVICE`, `L_SALUTATION`, `L_FORMULE` | textes génériques | garder, sauf demande |
| `MOIS`, `maximums` | noms des mois, longueur max des 3 paragraphes | garder |

Clé facultative `L_VILLE_FORMAT` : forme de la ville du destinataire (défaut : saut de ligne puis la ville).

**`config/cv_gabarit/lettre.tex`** → signature, écrite en dur (§ 5-B) : nom, email, téléphone.

**Lettre type** (pour `input/lm_original.tex`, § 4.10) : 3 paragraphes. Rédige-les avec l'utilisateur à
partir de ses faits réels, en restant sous `maximums` (420, 560 et 420 caractères), puis fais-les valider.
Si sa lettre a été lue en section 0, pars de ses paragraphes (ramenés à 3, sans rien inventer). Si elle a été
**déposée** en `.tex` (§ 4.0), `input/lm_original.tex` est déjà sa lettre : cette lettre type n'est pas
nécessaire (`originaux … --garder lm`).

### 4.9 Faits réels — `config/deepseek/nouveaux/message_2.txt`

C'est la **seule matière** que DeepSeek a le droit d'utiliser pour écrire le CV et la lettre. Garde **mot pour
mot** toutes ses lignes de consigne (titres de rubriques, phrases d'instruction, « Règle sur les chiffres »,
dernière ligne) et remplace **seulement** les faits, à partir des réponses des § 4.3 et 4.7 et du squelette de
l'**annexe B** : formation, chaque stage (réalisations, technologies, chiffre réel), missions freelance,
exemples de projets, compétences (3 lignes), soft skills. Une rubrique sans contenu (ex. aucune mission
freelance) est retirée en entier. Demande ensuite : « Y a-t-il d'autres faits réels à ajouter (chiffres,
projets, missions) ? ». Texte simple, sans LaTeX ni Markdown. Fais-le **relire en entier** par l'utilisateur
avant de l'écrire.

### 4.10 Messages 1 et 3, CV et lettre de secours (outil)

1. **`message_1.txt`** (règles d'écriture envoyées à DeepSeek) : **ne le régénère pas**. Ses règles ont été
   affinées à l'usage et valent pour tout candidat. Remplace **uniquement** ces 12 éléments personnels, un par
   un, et garde tout le reste mot pour mot (même les tournures et la ponctuation) :

   | Où, dans le fichier | Ce qui change |
   |---|---|
   | 1re phrase « Je suis … » | prénom et nom, présentation (`objectif.yaml` → `presentation_fr`), objectif et date |
   | « RÈGLE ABSOLUE », 2e règle | les 3 lignes d'en-tête du CV entre « » : `TITRE`, `ECOLE`, `STAGE` de `fixe_fr.json`, sans LaTeX |
   | « Si un résultat n'a pas de chiffre réel » | l'exemple entre « » : une de ses réalisations, dite sans chiffre |
   | « Cohérence des dates » | « dans le stage de <année> chez <entreprise> » : sa plus ancienne expérience |
   | « N'exagère pas » | « <un de ses faits> » ne devient JAMAIS « <sa version exagérée> » |
   | « Profil » | l'objectif et la date |
   | « Titre de chaque stage » | 3 exemples d'intitulés proches de ses expériences |
   | « Expérience : mes 3 stages réels (…) » | le nombre et les noms de ses entreprises (`STAGE1..3` de `fixe_fr.json`, sans « Stage chez ») |
   | « Le besoin de départ » | l'exemple : le besoin réel auquel une de ses réalisations répondait |
   | « Chiffres d'impact » | l'exemple d'apport décrit avec des mots |
   | « Style professionnel » | l'exemple de profil impersonnel (sa présentation) |
   | ligne « Exemple : « Conception d'un … » » | un paragraphe d'expérience tiré de SES faits réels, avec son vrai chiffre |

   Si son objectif n'est pas un stage (alternance, emploi), remplace aussi « stage » par le bon mot dans ces
   règles. À signaler à l'utilisateur : la règle « Exception : les PROJETS » autorise DeepSeek à écrire des
   **projets de formation** construits sur les technologies de l'offre. Demande-lui s'il veut la garder ; sinon,
   retire ce paragraphe.
   **`message_3.txt`** (format des réponses) : aucune donnée personnelle, **ne le modifie pas**, sauf si le nombre
   d'expériences ou de projets change (§ 4.7).
   La commande `messages` (régénération depuis `config/modeles/`, avec des règles plus courtes) ne sert qu'en
   secours, si ces fichiers sont absents ou cassés.
2. Écris, **hors du projet**, un fichier `textes_fr.json` en **texte simple** (sans LaTeX) et en **français**,
   avec toutes les clés de `reference_en.json` plus `LM_P1`, `LM_P2`, `LM_P3` (la lettre type du § 4.8).
3. `originaux --textes <chemin>\textes_fr.json` (simulation, compile et vérifie 1 page), puis avec `--ecrire` :
   régénère `input/cv_original.tex` (CV de secours, utilisé si la personnalisation échoue) et
   `input/lm_original.tex`. Si l'utilisateur a déposé son CV ou sa lettre en `.tex` (§ 4.0), ajoute
   `--garder cv`, `--garder lm` ou `--garder cv,lm` : ces fichiers restent les siens, et les clés du
   document gardé ne sont plus exigées dans `textes_fr.json`.

### 4.11 Domaine et recherches

**`input/Scraping/criteres_domaine.json`** (JSON valide) :

| Clé | Question |
|---|---|
| `diplome` | « Quel diplôme possèdes-tu ou prépares-tu, exactement ? » (école, année, objectif) |
| `domaine_souhaite` | « Quel domaine ou quels postes veux-tu ? » (**obligatoire**) |
| `inclure` | « Qu'est-ce qu'il faut inclure en plus (cas limites acceptés) ? » |
| `exclure` | « Qu'est-ce qu'il faut exclure ? » |
| `cas_mixtes` | « Que faire d'un poste mixte ? » |
| `precisions` | « Autres précisions ? (facultatif) » |
| `modifie_le` | date du jour, `AAAA-MM-JJ` |

**`input/Scraping/liens.txt`** : garde les lignes de commentaire du début (celles qui commencent par `#`).
Puis, pour chaque recherche (« Quels mots-clés et quel lieu ? », une à la fois, ligne vide pour finir) :
une ligne `# <mots-clés> — <lieu>`, puis
`https://www.linkedin.com/jobs/search/?keywords=<mots-clés encodés>&location=<lieu encodé>`
(espaces → `%20`, accents encodés en UTF-8), puis une ligne vide.

### 4.12 Secrets — saisis par l'utilisateur lui-même

| Fichier | Clés | Rôle |
|---|---|---|
| `.env` | `MOT_DE_PASSE_SITES` | mot de passe des comptes candidat sur les sites d'entreprises (8 caractères ou plus, différent de celui de l'email) |
| `.env` | `MOT_DE_PASSE_SITES_2`, `MOT_DE_PASSE_SITES_3` | versions de secours plus longues (16 et 20 caractères), essayées si un site refuse le précédent |
| `.env` | `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `SMTP_PASSWORD`, `TWOCAPTCHA_API_KEY` | facultatives : laisser **vides** sauf choix de l'utilisateur |
| `input/Scraping/.env` | `APIFY_TOKEN`, `APIFY_TOKEN_2` … `APIFY_TOKEN_10` | clés Apify du scraping (jusqu'à 10) |
| `extensions/nopecha/manifest.json` | **uniquement** `"nopecha": { "key": "…" }` | clé NopeCHA. Ne touche **jamais** au champ `"key"` du premier niveau (identifiant de l'extension Chrome) |
| `config/config.yaml` | `secours_vision.fournisseurs[*].cle_api` | à vider si elles appartiennent à un autre candidat ; `secours_vision.actif` reste `false` |
| `config/config.yaml` | `email.utilisateur`, `email.mot_de_passe` | facultatif (rapport par email), sinon vides |

Procédure :
1. Explique à l'utilisateur ce qu'il doit saisir et où. Rappelle que les secrets d'un candidat précédent
   doivent tous être **remplacés ou vidés**.
2. Ouvre le fichier pour lui (`notepad .env`, `notepad input\Scraping\.env`). Format : `NOM=valeur`, une par
   ligne ; une valeur qui contient `$`, `!` ou `#` s'écrit entre apostrophes `'…'`. Il saisit, enregistre et
   ferme.
   Alternative : `FORMULAIRE.bat` → section 7 (saisie masquée, crée aussi les mots de passe n° 2 et 3).
3. Vérifie **seulement la présence**, sans jamais afficher de valeur :
   `.venv\Scripts\python.exe -B -c "for f in ('.env','input/Scraping/.env'): print(f, [l.split('=',1)[0]+('=(rempli)' if l.split('=',1)[1].strip() else '=(vide)') for l in open(f,encoding='utf-8-sig').read().splitlines() if '=' in l and not l.lstrip().startswith('#')])"`

### 4.13 Navigateurs et comptes (fait par l'utilisateur)

Le robot pilote 5 Chrome, chacun avec son dossier de profil (hors du projet) où l'utilisateur se connecte
**lui-même**. Le robot ne connaît aucun mot de passe de ces comptes.

| N° | Port | Dossier du profil | Connexions à faire |
|---|---|---|---|
| 1 | 9222 | `%USERPROFILE%\.tappi\job-automation` | LinkedIn, Gmail (email de candidature), DeepSeek (compte 1) |
| 2 | 9223 | `…\.tappi\job-automation-2` | LinkedIn, Gmail, DeepSeek (compte 2) |
| 3 | 9224 | `…\.tappi\job-automation-3` | LinkedIn, Gmail, DeepSeek (compte 3) |
| 4 | 9225 | `…\.tappi\job-automation-4` | LinkedIn, Gmail, DeepSeek (compte 4) |
| 5 | 9226 | `…\.tappi\job-automation-verif` | DeepSeek seulement (tri des offres) |

- L'utilisateur ouvre chaque navigateur avec `LANCER.bat` → 8 → numéro, et s'y connecte. Chaque compte DeepSeek
  doit être différent, et créé avec une autre adresse que l'email de candidature. Extension NopeCHA :
  `chrome://extensions` → Mode développeur → « Charger l'extension non empaquetée » → dossier
  `extensions\nopecha`. Pour une vérification automatique des connexions : `FORMULAIRE.bat` → section 8.
- **Si ces dossiers existent déjà avec les comptes d'une autre personne** (même PC), le robot postulerait
  avec SES comptes. Il faut alors choisir de nouveaux noms de dossiers (ex. ajouter un suffixe) et les changer
  aux **4 endroits** suivants, à l'identique :
  1. `config/config.yaml` → `navigateur.profil_chrome` et les 3 lignes `profil:` de `navigateurs_secours` ;
  2. `LANCER.bat` → les 5 lignes `set DOSSIER=…` (menu 8) ;
  3. `scripts/verify_eligibility.py` → la ligne `PROFIL = "~/.tappi/job-automation-verif"` ;
  4. `scripts/formulaire_navigateurs.py` → `"profil": "~/.tappi/job-automation-verif"` (navigateur 5).
  Si les deux robots doivent tourner **en même temps**, change aussi les ports : `config.yaml` → `cdp_url` et
  `navigateurs_secours` (`port:`) ; `LANCER.bat` → `set PORT=…` ; `scripts/verify_eligibility.py` →
  `PORT = 9226` ; `scripts/formulaire_navigateurs.py` → les ports des navigateurs 1 et 5.
  Ce sont les **seules** lignes de code que tu as le droit de modifier.

---

## 5. Emplacements écrits « en dur » (à modifier à la main)

**A. `config/cv_gabarit/gabarit.tex`**
- Bloc `% ================= HEADER =================`, jusqu'au `\end{minipage}` qui le ferme :
  - `{\LARGE \textbf{…}}\\[1pt]` → prénom et nom affichés (casse au choix de l'utilisateur) ;
  - `\textbf{<<TELEPHONE>>} \href{tel:…}{…} \quad|\quad` → dans `tel:` le numéro **sans espaces** ; le texte
    affiché = `identite.telephone` ;
  - `\textbf{Email:} \href{mailto:…}{…} \\[3pt]` → l'email, **2 fois** ;
  - `{\large \textbf{\href{<URL GitHub>}{<texte>}} \quad\textbar\quad \textbf{\href{<URL LinkedIn>}{<texte>}}}`
    → voir § 4.2.
  - Les lignes `<<TITRE>>`, `<<ECOLE>>`, `<<STAGE>>` ne se modifient **pas** ici, mais dans `fixe_*.json`.
- Bloc `% ================= EDUCATION =================` : le texte en gras après `\hfill` sur les 3 lignes
  `\textbf{<<F1>>}`, `\textbf{<<F2>>}`, `\textbf{<<F3>>}` (établissement et ville).
- Bloc `% ================= CERTIFICATIONS =================` : les lignes `\item …` entre
  `\begin{itemize}` et `\end{itemize}`, à l'intérieur de `\begin{multicols}{2}` (2 colonnes : un nombre pair de
  lignes est plus joli). Sans certification : supprime tout le bloc, du commentaire `CERTIFICATIONS` jusqu'au
  `\vspace{1pt}` qui précède le commentaire `LANGUAGES & SOFT SKILLS`, inclus.
- Bloc `% ================= LANGUAGES & SOFT SKILLS =================` : les lignes `\item <<L_…>>` (§ 4.5) ;
  le titre « Soft Skills » est fixe.

**B. `config/cv_gabarit/lettre.tex`** — bloc `% Signature et coordonnées (fixes)` :
1ʳᵉ ligne `\textbf{…}\\[0.1cm]` → prénom et nom ; 3ᵉ ligne `\textcolor{textgray}{…}\\` → email ;
4ᵉ ligne `\textcolor{textgray}{…}` → téléphone. La 2ᵉ ligne (`<<L_VILLE_SIGNATURE>>`) vient de
`lettre_fixe.json`.

**C. `LANCER.bat`** — ligne `echo     - Google : <email>   (c'est lui qui postule)` (menu 8). Fichier en
**CRLF** : garde ces fins de ligne.

**D. `config/config.yaml`**
- Section `navigateur`, ligne de commentaire `#   · ton compte de candidature (<email>) connecté à LinkedIn & co. ;`
- `garde_fous.chaines_obligatoires_cv:` → une ligne `    - "…"` par texte qui doit apparaître **mot pour mot**
  dans `input/cv_original.tex` (ex. un morceau de la ligne `STAGE`), ou `chaines_obligatoires_cv: []`.
  Sinon, `--verifier` affiche une alerte.
- `garde_fous.pages_max_cv: 1` : à garder.
- Les clés 🔒 du § 4.12.

**E. `prompts/` — seulement si le pays ou l'indicatif téléphonique change**
Ces consignes contiennent quelques exemples et une règle écrits pour la ville et l'indicatif du **premier**
candidat (lis-les pour les repérer) :
- `prompts/formulaire_prompt.txt` : la règle « 8 quinquies » (sélecteur d'indicatif pays : elle nomme un pays et
  un indicatif précis) → **à adapter** au pays et à l'indicatif du nouveau candidat ; l'exemple de l'action
  `saisir_suggestion` et la règle « 8 ter » (une ville en exemple) → recommandé ;
- `prompts/bilan_prompt.txt` : un exemple « (candidat à <ville>) » → recommandé ;
- `prompts/lm_prompt.txt` : un pays cité en exemple → facultatif.
Ne touche jamais aux repères `[[…]]` de ces fichiers.

---

## 6. Règles d'écriture par type de fichier

- **YAML** (`profile.yaml`, `objectif.yaml`, `config.yaml`) : valeur entre guillemets doubles `"…"`, un `"`
  intérieur s'écrit `\"`, le commentaire de fin de ligne est conservé ; listes au même format que dans le
  fichier (`["a", "b"]` ou lignes `- "…"`) ; booléens `true` / `false` sans guillemets.
  Contrôle : `.venv\Scripts\python.exe -B -c "import yaml; yaml.safe_load(open('config/profile.yaml', encoding='utf-8')); print('OK')"`
  (même chose pour `objectif.yaml` et `config.yaml`).
- **JSON** : JSON valide, UTF-8, même indentation. Dans `config/cv_gabarit/*.json`, les valeurs sont du
  **LaTeX** : `&` → `\\&`, `%` → `\\%`, `_` → `\\_`, `#` → `\\#`, `$` → `\\$` (barre doublée à cause du
  JSON), `--` = tiret, `1\\textsuperscript{er}` = 1ᵉʳ.
  Contrôle : `.venv\Scripts\python.exe -B -c "import json; json.load(open('<fichier>', encoding='utf-8')); print('OK')"`
- **LaTeX** (`.tex`) : dans le texte, échappe `& % $ # _ { } ~ ^ \` →
  `\& \% \$ \# \_ \{ \} \textasciitilde{} \textasciicircum{} \textbackslash{}`. Dans l'URL d'un `\href{…}`, le
  `_` reste tel quel.
- **Texte simple** (`message_*.txt`, `liens.txt`) : ni LaTeX ni Markdown.
- **Fins de ligne** : avant d'écrire, regarde si le fichier contient `\r\n` (CRLF) et garde la même chose.
  Les `.bat` sont en CRLF. Encodage UTF-8.
- Valide les formats : email `nom@domaine.ext` ; téléphone chiffres, espaces et `+` ; dates `JJ/MM/AAAA` ;
  liens commençant par `https://` ; Oui / Non.

---

## 7. Traces d'un candidat précédent (nettoyage, avec accord)

Propose chaque groupe séparément, et n'agis qu'après « o ». Un élément déjà absent, ou un fichier
déjà vide (`{}`), est simplement sauté. Tout part à la **Corbeille** :

```powershell
Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("<chemin complet du dossier>", 'OnlyErrorDialogs', 'SendToRecycleBin')
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("<chemin complet du fichier>", 'OnlyErrorDialogs', 'SendToRecycleBin')
```

Après un dossier envoyé à la Corbeille, recrée-le vide (et un fichier vide `.gitkeep` dans
`output/Candidatures/` et `logs/`).

| Groupe | Contenu | Action proposée |
|---|---|---|
| 1 | `output/Candidatures/*`, `output/captures/*` | Corbeille (garder `.gitkeep`) |
| 2 | `input/entreprises.xlsx` | Corbeille, puis Excel neuf : `.venv\Scripts\python.exe -B -c "import sys; sys.path.insert(0, 'scripts'); from pathlib import Path; import excel_manager as em; em.creer_modele(Path('input/entreprises.xlsx'), avec_exemple=False)"` (seulement une fois l'ancien retiré) |
| 3 | `input/sauvegardes/*`, `logs/*`, `rapports/*` | Corbeille (garder `logs/.gitkeep`) |
| 4 | `input/ancien/`, `images/`, `config/deepseek/message_1.txt`, `message_2.txt`, `message_3.txt` (**pas** ceux de `nouveaux/`), `PASSATION_CLAUDE.md`, `job-automation.rar`, `exemple_fr.json` s'il n'est pas remplacé, les anciennes photos de `input/` | Corbeille |
| 5 | `input/Scraping/offres_scrapees.json`, `input/Scraping/logs/` | Corbeille |
| 6 | `memoire/regles_sites.json` | contenu remplacé par `{}` : les comptes du candidat précédent n'existent pas pour le nouveau |
| 7 | `memoire/playbooks.json` | contient des valeurs tapées pour le candidat précédent : identité, adresse, date de naissance, école, et même des lettres de motivation complètes. **A (recommandé)** : contenu `{}` (le robot réapprend). **B** : remplacer chaque valeur personnelle par celle du nouveau candidat et retirer les textes de lettres. |
| 8 | `memoire/lecons_sites.json` | les leçons servent à tous, mais certaines citent la nationalité, la ville, la civilité ou l'initiale du nom du candidat précédent : remplace ces mentions par une formulation neutre (« la nationalité du candidat ») |

---

## 8. Vérifications finales

1. `controle` → le gabarit fait 1 page, les nombres de lignes sont ceux du § 4.7, aucune ligne
   « ATTENTION », `input/cv_original.tex` et `input/lm_original.tex` font 1 page, la lettre fait 1 page,
   et `fixe_fr.json` / `fixe_en.json` sont complets. Chaque image appelée (photo, icônes) est
   « présente dans input/ ».
2. `apercus --ecrire`, puis montre `config\cv_gabarit\apercu_cv_fr.png` et `apercu_cv_en.png` à l'utilisateur,
   et fais-les valider (avec la photo et son cadrage, s'il y en a une).
3. `recherche "<ancienne valeur 1>" "<ancienne valeur 2>" …` avec **toutes** les valeurs relevées au § 1
   (prénom, nom, email, téléphone sous ses différentes formes, adresse, code postal, date de naissance, école,
   entreprises, ville…). Résultat attendu : seulement les fichiers que l'utilisateur a choisi de garder.
4. `.venv\Scripts\python.exe scripts\orchestrator.py --verifier` → « Configuration OK », sans alerte sur
   `chaines_obligatoires_cv`.
5. Résumé final pour l'utilisateur : ce qui a été écrit et où, ce qu'il lui reste à faire lui-même (secrets,
   connexions des navigateurs), et le premier essai à faire : `LANCER.bat` → 2 (CV et lettre seulement), puis
   → 3 (formulaires remplis **sans envoi**), avant tout envoi réel (→ 4).
6. Supprime tes fichiers temporaires (`outils_ia.py`, `textes_fr.json`) : ils sont hors du projet.

---

## Annexe A — `outils_ia.py`

Copie ce code dans un fichier **hors du projet**, puis lance-le **depuis la racine du projet** :
`.venv\Scripts\python.exe -B <chemin>\outils_ia.py <commande>`.

```python
r"""
outils_ia.py — aides pour l'IA qui installe un candidat (voir GUIDE_IA_DONNEES_PERSONNELLES.md).
À lancer depuis la RACINE du projet :  .venv\Scripts\python.exe -B <chemin>\outils_ia.py <commande> [options]

  photo     FICHIER [--ecrire]       photo de l'utilisateur -> input/photo.jpg + bloc photo dans gabarit.tex
  photo     --aucune [--ecrire]      retire le bloc photo du gabarit (CV sans photo)
  deposer   --cv F.tex [--ecrire]    CV LaTeX de l'utilisateur -> input/cv_original.tex (+ ses images)
  deposer   --lm F.tex [--ecrire]    lettre LaTeX de l'utilisateur -> input/lm_original.tex (+ ses images)
  texte     FICHIER                  texte d'un CV / d'une lettre (.pdf, .docx, .tex) pour pré-remplir
  messages  [--ecrire]               SECOURS : message_1 et message_3 régénérés depuis config/modeles/
  originaux --textes F.json [--garder cv|lm|cv,lm] [--ecrire]
                                     input/cv_original.tex et input/lm_original.tex (CV / lettre de secours)
  controle                           compile le gabarit, le CV et la lettre (dossier temporaire) : 1 page ?
  apercus   [--ecrire]               config/cv_gabarit/apercu_cv_fr/en .pdf + .png
  recherche VALEUR [VALEUR …]        où une ancienne valeur apparaît encore (jamais dans les .env)

Sans --ecrire, rien n'est écrit : le résultat est seulement affiché.
"""
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
RACINE = Path.cwd()
assert (RACINE / "scripts" / "orchestrator.py").exists(), "Lance ce script depuis la racine du projet."
sys.path.insert(0, str(RACINE / "scripts"))
import yaml  # noqa: E402

G = RACINE / "config" / "cv_gabarit"
ENTREE = RACINE / "input"
NOM_PHOTO = "photo.jpg"
RATIO_PHOTO = 850 / 960          # largeur / hauteur de la photo pour laquelle l'en-tête est calibré
IMAGES = (".jpg", ".jpeg", ".png", ".pdf", ".eps")
RE_IMG = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
ICONES = re.compile(r"github|linkedin|logo|icon", re.I)        # images qui ne sont pas « la photo »
# En-tête du gabarit : sans photo (bloc centré pleine largeur) / avec photo (colonne de 12 %).
SANS_PHOTO = "\\noindent\n\\begin{minipage}[c]{\\textwidth}\n"
AVEC_PHOTO = ("\\noindent\n\\begin{minipage}[c]{0.12\\textwidth}\n    \\includegraphics[width=2.8cm]{NOM}\n"
              "\\end{minipage}%\n\\begin{minipage}[c]{0.88\\textwidth}\n")
RE_BLOC_PHOTO = re.compile(r"\\noindent\n\\begin\{minipage\}\[c\]\{0?\.\d+\\textwidth\}\n[ \t]*\\includegraphics"
                           r"(?:\[[^\]]*\])?\{[^}]+\}\n\\end\{minipage\}%?\n\\begin\{minipage\}\[c\]\{0?\.\d+\\textwidth\}\n")


def lire_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def sans_latex(t: str) -> str:
    t = t.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_").replace("--", "–")
    return re.sub(r"\s+", " ", re.sub(r"\\[a-zA-Z]+|[{}]", "", t)).strip()


def ecrire(p: Path, texte: str, ecrire_vraiment: bool) -> None:
    if not ecrire_vraiment:
        print(f"----- {p.relative_to(RACINE)} (simulation, non écrit) -----\n{texte[:1500]}\n")
        return
    nl = "\r\n" if p.exists() and b"\r\n" in p.read_bytes() else "\n"   # fins de ligne d'origine
    p.write_text(texte.replace("\r\n", "\n").replace("\n", nl), encoding="utf-8", newline="")
    print(f"écrit : {p.relative_to(RACINE)}")


def compiler_tmp(tex: str, nom: str, ressources: list | None = None) -> Path:
    """Compile comme le robot (liens neutralisés, images cherchées dans input/), hors du projet."""
    from documents import neutraliser_liens
    from latex_compiler import compiler
    d = Path(tempfile.mkdtemp(prefix="ia_"))
    tex = tex.replace("\r\n", "\n")                  # comme le robot, qui relit le fichier en texte
    (d / f"{nom}.tex").write_text(neutraliser_liens(tex), encoding="utf-8")
    return compiler(d / f"{nom}.tex", d, "pdflatex", 2, 180, dossiers_ressources=[*(ressources or []), ENTREE])


def pages(pdf: Path) -> int:
    import pymupdf
    with pymupdf.open(pdf) as doc:
        return doc.page_count


def images_pdf(pdf: Path) -> int:
    import pymupdf
    with pymupdf.open(pdf) as doc:
        return sum(len(p.get_images()) for p in doc)


def image_dans_input(nom: str) -> bool:
    return any((ENTREE / (nom + e)).is_file() for e in ("", *IMAGES))


# ------------------------------------------------------------------------------------------ photo
def gabarit_avec_photo(tex: str, nom: str) -> str:
    deja = [m for m in RE_IMG.finditer(tex) if not ICONES.search(m.group(1))]
    if deja:                                         # bloc photo présent : seul le nom du fichier change
        assert len(deja) == 1, "plusieurs images dans le gabarit : applique l'annexe C à la main"
        return tex[:deja[0].start(1)] + nom + tex[deja[0].end(1):]
    assert tex.count(SANS_PHOTO) == 1, "en-tête du gabarit non reconnu : applique l'annexe C à la main"
    return tex.replace(SANS_PHOTO, AVEC_PHOTO.replace("NOM", nom))


def gabarit_sans_photo(tex: str) -> str:
    n = len(RE_BLOC_PHOTO.findall(tex))
    if n == 0:
        assert not [m for m in RE_IMG.finditer(tex) if not ICONES.search(m.group(1))], \
            "photo présente sous une autre forme : retire-la à la main (annexe C)"
        return tex
    assert n == 1, "plusieurs blocs photo : à traiter à la main"
    return RE_BLOC_PHOTO.sub(lambda m: SANS_PHOTO, tex)


def preparer_photo(src: Path):
    from PIL import Image, ImageOps
    assert src.is_file(), f"fichier introuvable : {src}"
    assert src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"), \
        "formats acceptés : .jpg, .jpeg, .png, .webp (photo HEIC d'iPhone : l'exporter d'abord en JPG)"
    img = ImageOps.exif_transpose(Image.open(src))   # photo de téléphone : remise à l'endroit
    if img.mode in ("RGBA", "LA", "P"):               # fond transparent -> blanc
        img = img.convert("RGBA")
        fond = Image.new("RGB", img.size, "white")
        fond.paste(img, mask=img.split()[-1])
        img = fond
    img = img.convert("RGB")
    # Recadrage au format pour lequel le gabarit est calibré (2,8 x 3,16 cm) : l'en-tête garde
    # exactement sa hauteur, donc le CV reste sur une page quelle que soit la photo fournie.
    w, h = img.size
    if w / h > RATIO_PHOTO * 1.01:                    # trop large : on coupe les deux côtés
        nw = round(h * RATIO_PHOTO)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    elif w / h < RATIO_PHOTO * 0.99:                  # trop haute : on coupe surtout le bas (visage en haut)
        nh = round(w / RATIO_PHOTO)
        y = round((h - nh) * 0.25)
        img = img.crop((0, y, w, y + nh))
    if img.size != (w, h):
        print(f"photo recadrée de {w} x {h} à {img.size[0]} x {img.size[1]} px (format du gabarit) : "
              "vérifie le cadrage sur l'aperçu")
    img.thumbnail((800, 800))                         # 2,8 cm à 300 ppp ≈ 330 px : 800 px suffisent
    return img


def cmd_photo(source: str, ecrire_vraiment: bool) -> None:
    from cv_gabarit import GabaritCV
    f_gab = G / "gabarit.tex"
    tex = f_gab.read_text(encoding="utf-8")
    tmp = None
    if source == "--aucune":
        nouveau = gabarit_sans_photo(tex)
    else:
        img = preparer_photo(Path(source.strip('"')))
        w, h = img.size
        print(f"photo préparée : {w} x {h} px, {2.8:.1f} x {2.8 * h / w:.2f} cm sur le CV")
        tmp = Path(tempfile.mkdtemp(prefix="ia_photo_"))
        img.save(tmp / NOM_PHOTO, "JPEG", quality=90)
        nouveau = gabarit_avec_photo(tex, NOM_PHOTO)
    g = GabaritCV(lambda *a: None)
    g.gabarit = nouveau
    pdf = compiler_tmp(g.remplir_cv(g.ref_latex, "en", latex_brut=True), "photo", [tmp] if tmp else [])
    n, ni = pages(pdf), images_pdf(pdf)
    print(f"CV de référence {'avec cette photo' if tmp else 'sans photo'} : {n} page(s), {ni} image(s) dans le PDF")
    assert n == 1, "le CV dépasse une page avec cette photo : demande une photo moins haute (portrait 3:4 ou carrée)"
    assert (ni >= 1) if tmp else True, "la photo n'apparaît pas dans le PDF"
    autres = sorted(p.name for p in ENTREE.iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and not ICONES.search(p.name)
                    and p.name != NOM_PHOTO)
    if autres:
        print(f"Autres photos dans input/ (candidat précédent ? à proposer pour la Corbeille, § 7) : {autres}")
    if ecrire_vraiment:
        if tmp:
            shutil.copy2(tmp / NOM_PHOTO, ENTREE / NOM_PHOTO)
            print(f"écrit : input/{NOM_PHOTO}")
        if nouveau != tex:
            ecrire(f_gab, nouveau, True)
        print("Ensuite : section 10 (`originaux`, puis `apercus --ecrire`) pour le CV de secours et les aperçus.")
    elif nouveau != tex:
        print("(simulation : gabarit.tex et input/ non modifiés)")


# ------------------------------------------------------------------------------------------ dépôt
def cmd_deposer(genre: str, source: str, ecrire_vraiment: bool) -> None:
    src = Path(source.strip('"'))
    assert src.is_file(), f"fichier introuvable : {src}"
    if src.suffix.lower() != ".tex":
        raise SystemExit("Seul un .tex se dépose tel quel. Pour un .pdf / .docx : `texte`, puis § 4.0 "
                         "(le fichier sera généré par `originaux` en section 10).")
    brut = src.read_bytes()
    tex = brut.decode("utf-8")
    assert "\\documentclass" in tex and "\\end{document}" in tex, "ce n'est pas un document LaTeX complet"
    dossier = src.parent
    copies, photo_sous, manquantes = {}, {}, []
    for nom in dict.fromkeys(RE_IMG.findall(tex)):
        trouve = next((c for c in [dossier / nom] + [dossier / (nom + e) for e in IMAGES] if c.is_file()), None)
        if trouve:
            copies[trouve.relative_to(dossier).as_posix()] = trouve
        elif image_dans_input(nom):
            continue
        elif not ICONES.search(nom) and (ENTREE / NOM_PHOTO).is_file():
            photo_sous[nom if Path(nom).suffix else nom + ".jpg"] = ENTREE / NOM_PHOTO
        else:
            manquantes.append(nom)
    for cmd, ext in (("documentclass", ".cls"), ("usepackage", ".sty")):
        for m in re.finditer(r"\\" + cmd + r"(?:\[[^\]]*\])?\{([^}]+)\}", tex):
            for nom in m.group(1).split(","):
                f = dossier / (nom.strip() + ext)
                if f.is_file():
                    copies[f.name] = f
    trop_gros = [n for n, f in copies.items() if f.stat().st_size > 5_000_000]
    assert not trop_gros, f"fichiers de plus de 5 Mo : {trop_gros}"
    tmp = Path(tempfile.mkdtemp(prefix="ia_depot_"))
    for rel, f in {**copies, **photo_sous}.items():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, tmp / rel)
    nom_cible = "cv_original" if genre == "cv" else "lm_original"
    pdf = compiler_tmp(tex, nom_cible, [tmp])
    n = pages(pdf)
    print(f"{src.name} -> input/{nom_cible}.tex : {n} page(s), {images_pdf(pdf)} image(s)")
    print(f"  ressources copiées dans input/ : {sorted(copies) or 'aucune'}")
    if photo_sous:
        print(f"  la photo input/{NOM_PHOTO} est aussi fournie sous le nom attendu par ce document : {sorted(photo_sous)}")
    if manquantes:
        print(f"  ATTENTION : images introuvables (demande-les à l'utilisateur) : {manquantes}")
    pages_max = int(((yaml.safe_load((RACINE / "config" / "config.yaml").read_text(encoding="utf-8")) or {})
                     .get("garde_fous") or {}).get(f"pages_max_{genre}", 1))
    if n > pages_max:
        print(f"  ATTENTION : {n} pages, la limite (garde_fous.pages_max_{genre}) est {pages_max}.")
    if ecrire_vraiment:
        for rel, f in {**copies, **photo_sous}.items():
            cible = ENTREE / rel
            if cible.resolve() == f.resolve():
                continue
            if cible.exists() and cible.read_bytes() != f.read_bytes():
                print(f"  remplacé : input/{rel} (l'ancien est dans la sauvegarde de la règle 4)")
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, cible)
        (ENTREE / f"{nom_cible}.tex").write_bytes(brut)            # tel quel (fins de ligne comprises)
        print(f"écrit : input/{nom_cible}.tex (+ {len(copies) + len(photo_sous)} ressource(s))")
    else:
        print("(simulation : rien écrit)")


def cmd_texte(source: str) -> None:
    p = Path(source.strip('"'))
    assert p.is_file(), f"fichier introuvable : {p}"
    ext = p.suffix.lower()
    if ext == ".pdf":
        import pymupdf
        with pymupdf.open(p) as doc:
            texte = "\n".join(pg.get_text() for pg in doc)
    elif ext == ".docx":
        import html
        import zipfile
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        xml = xml.replace("</w:p>", "\n").replace("<w:tab/>", "\t").replace("<w:br/>", "\n")
        texte = html.unescape(re.sub(r"<[^>]+>", "", xml))
    elif ext in (".tex", ".txt", ".md"):
        texte = p.read_text(encoding="utf-8", errors="replace")
    else:
        raise SystemExit("formats lus : .pdf, .docx, .tex, .txt (un .doc : l'enregistrer en .docx ou .pdf)")
    print(texte)


# ------------------------------------------------------------------------------------------
def cmd_messages(ecrire_vraiment: bool) -> None:
    from objectif import Objectif
    profil = yaml.safe_load((RACINE / "config" / "profile.yaml").read_text(encoding="utf-8"))
    obj = Objectif(profil)
    fixes = lire_json(G / "fixe_fr.json")
    ref = lire_json(G / "reference_en.json")
    exp = sorted({k.split("_")[0] for k in ref if re.match(r"E\d+_", k)}, key=lambda x: int(x[1:]))
    proj = sorted({k.split("_")[0] for k in ref if re.match(r"P\d+_", k)}, key=lambda x: int(x[1:]))
    entreprises = [re.sub(r"^(stage|internship)\s+(chez|at|au sein d[eu]s?|within)\s+", "",
                          sans_latex(fixes.get(f"STAGE{e[1:]}", "")), flags=re.I) for e in exp]
    entete = " ; ".join(f"« {sans_latex(fixes[k])} »" for k in ("TITRE", "ECOLE", "STAGE") if fixes.get(k))
    ident = profil.get("identite") or {}
    pres = obj.presentation_fr
    m1 = ((RACINE / "config" / "modeles" / "message_1.modele.txt").read_text(encoding="utf-8")
          .replace("{{PRESENTATION}}", f"{ident.get('prenom', '')} {ident.get('nom', '')}, {pres[:1].lower() + pres[1:]}")
          .replace("{{OBJECTIF}}", f"{obj.un(obj.nom_fr)} {obj.formule_fr}")
          .replace("{{ENTETE}}", entete or "les lignes d'en-tête")
          .replace("{{NB_EXPERIENCES}}", str(len(exp)))
          .replace("{{EXPERIENCES}}", ", ".join(e for e in entreprises if e) or "voir mes faits réels")
          .replace("{{NB_PROJETS}}", str(len(proj))))

    def cles(liste):
        bornes = liste[:1] + liste[-1:] if len(liste) > 1 else liste
        return " à ".join(f"« {x}_P »" for x in bornes)
    m3 = ((RACINE / "config" / "modeles" / "message_3.modele.txt").read_text(encoding="utf-8")
          .replace("{{CLES_EXPERIENCES}}", cles(exp) or "« E…_P »")
          .replace("{{CLES_PROJETS}}", cles(proj) or "« P…_P »"))
    assert "{{" not in m1 + m3, "un repère {{…}} n'a pas été rempli"
    dossier = RACINE / "config" / "deepseek" / "nouveaux"
    ecrire(dossier / "message_1.txt", m1, ecrire_vraiment)
    ecrire(dossier / "message_3.txt", m3, ecrire_vraiment)


def cmd_originaux(fichier_textes: str, garder: set, ecrire_vraiment: bool) -> None:
    """textes : JSON en TEXTE SIMPLE (pas de LaTeX), langue française, avec toutes les clés de
    reference_en.json (PROFIL, E1_TITRE, E1_P…, P1_TITRE, P1_TECH, P1_P…, SK1_LABEL, SK1…) et
    LM_P1, LM_P2, LM_P3 (3 paragraphes d'une lettre type). --garder cv / lm : ne remplace pas le CV /
    la lettre que l'utilisateur a déposé(e) lui-même (commande deposer)."""
    from cv_gabarit import GabaritCV
    from documents import neutraliser_liens
    textes = lire_json(Path(fichier_textes))
    g = GabaritCV(lambda *a: None)
    cles = (list(g.MAX) if "cv" not in garder else []) + (list(g.LM_MAX) if "lm" not in garder else [])
    manquantes = [k for k in cles if not str(textes.get(k, "")).strip()]
    assert not manquantes, f"clés manquantes dans {fichier_textes} : {manquantes}"
    docs = []
    if "cv" not in garder:
        docs.append(("cv_original", neutraliser_liens(g.remplir_cv(textes, "fr"))))
    if "lm" not in garder:
        docs.append(("lm_original", neutraliser_liens(g.remplir_lettre(
            textes, {"nom_entreprise": "Entreprise", "lieu": "", "titre_poste": "Poste visé"}, "fr"))))
    for nom in sorted(garder):
        print(f"input/{nom}_original.tex : gardé tel quel (déposé par l'utilisateur)")
    for nom, tex in docs:
        n = pages(compiler_tmp(tex, nom))
        print(f"{nom} : {n} page(s)")
        assert n == 1, f"{nom} dépasse une page : raccourcis les textes"
        ecrire(ENTREE / f"{nom}.tex", tex, ecrire_vraiment)


def cmd_controle() -> None:
    from cv_gabarit import GabaritCV, lignes_pdf
    from documents import normaliser_pour_comparaison as normer
    g = GabaritCV(lambda tex, dossier, nom: compiler_tmp(tex, nom))
    ref = g.lignes_reference()
    absentes = [k for k in g.mesures if k in g.MAX and k not in ref]
    pdf_ref = compiler_tmp(g.remplir_cv(g.ref_latex, "en", latex_brut=True), "reference")
    print(f"Gabarit + reference_en.json : {pages(pdf_ref)} page(s), {images_pdf(pdf_ref)} image(s) ; "
          f"lignes par texte : {ref}")
    if absentes:
        print(f"  ATTENTION : textes introuvables dans le PDF (mesure impossible) : {absentes}")
    for nom in ("cv_original", "lm_original"):
        f = ENTREE / f"{nom}.tex"
        if f.exists():
            pdf = compiler_tmp(f.read_text(encoding="utf-8"), nom)
            n, lignes = lignes_pdf(pdf)
            print(f"input/{nom}.tex : {n} page(s), {images_pdf(pdf)} image(s) ; en-tête : {lignes[:3]}")
    lettre = g.remplir_lettre({k: "texte " * (m // 6) for k, m in g.LM_MAX.items()},
                              {"nom_entreprise": "Entreprise", "lieu": "Ville", "titre_poste": "Poste"}, "fr")
    print(f"Lettre (paragraphes à leur longueur maximale) : {pages(compiler_tmp(lettre, 'lettre'))} page(s)")
    for nom in ("fixe_fr", "fixe_en"):
        manque = [k for k in re.findall(r"<<([A-Z0-9_]+)>>", g.gabarit)
                  if k not in g.MAX and k not in lire_json(G / f"{nom}.json")]
        print(f"{nom}.json : {'complet' if not manque else 'CLÉS MANQUANTES ' + str(manque)}")
    # Images appelées (photo, icônes) : le robot les cherche dans input/ à chaque compilation
    for f in (G / "gabarit.tex", G / "lettre.tex", ENTREE / "cv_original.tex", ENTREE / "lm_original.tex"):
        if f.exists():
            for nom in dict.fromkeys(RE_IMG.findall(f.read_text(encoding="utf-8"))):
                etat = "présente dans input/" if image_dans_input(nom) else "ATTENTION : INTROUVABLE dans input/"
                print(f"{f.relative_to(RACINE)} : image « {nom} » {etat}")
    # Comme `orchestrator.py --verifier` : prénom, nom, email doivent figurer dans le CV de secours
    ident = (yaml.safe_load((RACINE / "config" / "profile.yaml").read_text(encoding="utf-8")) or {}).get("identite") or {}
    cv = ENTREE / "cv_original.tex"
    if cv.exists():
        contenu = normer(cv.read_text(encoding="utf-8"))
        for champ in ("prenom", "nom", "email"):
            v = str(ident.get(champ) or "")
            if v and normer(v) not in contenu:
                print(f"  ATTENTION : identite.{champ} n'apparaît pas tel quel dans input/cv_original.tex")


def cmd_apercus(ecrire_vraiment: bool) -> None:
    from PIL import Image
    import pymupdf
    from cv_gabarit import GabaritCV
    g = GabaritCV(lambda *a: None)
    sources = {"fr": (ENTREE / "cv_original.tex").read_text(encoding="utf-8"),
               "en": g.remplir_cv(g.ref_latex, "en", latex_brut=True)}
    for langue, tex in sources.items():
        pdf = compiler_tmp(tex, f"apercu_cv_{langue}")
        with pymupdf.open(pdf) as doc:
            img = Image.open(io.BytesIO(doc[0].get_pixmap(dpi=90).tobytes("png"))).convert("RGB")
            n = doc.page_count
        print(f"apercu_cv_{langue} : {n} page(s)")
        if ecrire_vraiment:
            shutil.copy2(pdf, G / f"apercu_cv_{langue}.pdf")
            img.save(G / f"apercu_cv_{langue}.png", "PNG")
            print(f"écrit : config/cv_gabarit/apercu_cv_{langue}.pdf / .png")


def cmd_recherche(valeurs: list[str]) -> None:
    import zipfile
    motifs = [re.compile(re.escape(v), re.I) for v in valeurs if v.strip()]
    for p in sorted(RACINE.rglob("*")):
        rel = p.relative_to(RACINE).as_posix()
        if not p.is_file() or p.name == ".env" or rel.startswith((".venv/", "extensions/nopecha/assets/")):
            continue                                       # .env : secrets, jamais lus ni affichés
        try:
            if p.suffix.lower() == ".pdf":
                import pymupdf
                with pymupdf.open(p) as doc:
                    texte = "\n".join(pg.get_text() for pg in doc)
            elif p.suffix.lower() == ".xlsx":
                with zipfile.ZipFile(p) as z:
                    texte = "\n".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist() if n.endswith(".xml"))
            else:
                brut = p.read_bytes()
                if b"\x00" in brut[:4096]:
                    continue
                texte = brut.decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            continue
        for m in motifs:
            n = len(m.findall(texte))
            if n:
                print(f"{rel} : « {m.pattern.replace(chr(92), '')} » x{n}")


if __name__ == "__main__":
    a = sys.argv[1:]
    e = "--ecrire" in a
    if not a:
        print(__doc__)
    elif a[0] == "photo" and len(a) > 1:
        cmd_photo(a[1], e)
    elif a[0] == "deposer" and ("--cv" in a or "--lm" in a):
        genre = "cv" if "--cv" in a else "lm"
        cmd_deposer(genre, a[a.index(f"--{genre}") + 1], e)
    elif a[0] == "texte" and len(a) > 1:
        cmd_texte(a[1])
    elif a[0] == "messages":
        cmd_messages(e)
    elif a[0] == "originaux":
        garder = set(a[a.index("--garder") + 1].split(",")) if "--garder" in a else set()
        assert garder <= {"cv", "lm"}, "--garder accepte : cv, lm ou cv,lm"
        cmd_originaux(a[a.index("--textes") + 1], garder, e)
    elif a[0] == "controle":
        cmd_controle()
    elif a[0] == "apercus":
        cmd_apercus(e)
    elif a[0] == "recherche":
        cmd_recherche(a[1:])
    else:
        print(__doc__)
```

---

## Annexe B — Squelette de `config/deepseek/nouveaux/message_2.txt`

Ce sont les lignes de consigne exactes du fichier actuel. Remplace chaque `<…>` par les faits réels de
l'utilisateur, une puce `- <…>` par réalisation, mission ou projet. Un chiffre réel par stage au plus ; un stage
sans chiffre prend la ligne « Pas de résultat chiffré… ». Les chiffres « 3 », « E1…E3 » et « plus de 100 »
suivent la réalité de l'utilisateur. Supprime une rubrique sans contenu.

```text
MES FAITS RÉELS — la seule matière autorisée pour mon CV et ma lettre.

FORMATION (déjà dans le modèle, à ne pas réécrire)
- <période> : <formation>, <établissement>, <ville> (<matières principales>).
- <période> : <formation>, <établissement>, <ville>.
- <période> : <formation>, <établissement>.

MES 3 STAGES — entreprise et période sont écrites par le programme (fixes). Pour chacun, tu écris un titre adapté à l'offre (E1_TITRE, E2_TITRE, E3_TITRE) et UN paragraphe (E1_P, E2_P, E3_P), uniquement avec les faits réels de CE stage.

STAGE 1 (E1) — <Entreprise>, <ville> — <mois année> à <mois année>
Réalisations réelles (tu choisis celle qui correspond le mieux à l'offre) :
- <réalisation>
- <réalisation>
- Technologies utilisées pendant ce stage : <liste>.
Résultat chiffré RÉEL de ce stage (le SEUL chiffre autorisé pour <Entreprise>) : <chiffre et ce qu'il mesure>.

STAGE 2 (E2) — <Entreprise>, <ville> — <mois année> à <mois année> — <poste réel>
Réalisations réelles :
- <réalisation>
Résultat chiffré RÉEL de ce stage (le SEUL chiffre autorisé pour <Entreprise>) : <chiffre et ce qu'il mesure>.
Technologies utilisables pour ce stage (existant en <année>) : <liste>.

STAGE 3 (E3) — <Entreprise>, <ville> — <mois année> à <mois année> — <poste réel>
Réalisations réelles :
- <réalisation>
Pas de résultat chiffré pour ce stage : décris l'apport avec des mots (ex. temps gagné sur des tâches manuelles).
Technologies utilisables pour ce stage (existant en <année>) : <liste>.

Règle sur les chiffres : chacun de ces chiffres réels n'est utilisé que dans SON stage, au plus une fois, sans jamais en changer la valeur.

MISSIONS FREELANCE RÉELLES (<période> ; utilisables dans la section « Projets & Missions Freelance »)
- <mission> : <ce qui a été fait, technologies>.

EXEMPLES DE PROJETS (une petite partie de mes plus de 100 projets de formation : reprends-en un s'il correspond à l'offre ; sinon écris un projet de formation adapté à l'offre, voir règle « Projets » ; résultat seulement, sans chiffre inventé)
- <titre> : <ce qui a été fait, technologies, résultat>.

COMPÉTENCES RÉELLES (liste non exhaustive : ma formation couvre aussi d'autres technologies, dont .NET / C# et Java, utilisables dans la section Compétences si l'offre les demande)
- IA & Data : <liste>.
- Logiciel & Data engineering : <liste>.
- ML complémentaires : <liste>.

SOFT SKILLS : <liste>.

Réponds « OK » si tu as tout noté, puis attends mon message suivant.
```

---

## Annexe C — Photo sur le CV (facultatif)

La commande `photo` (§ 4.0) fait tout. Ce qui suit sert à comprendre, ou à le faire à la main si la commande
refuse (en-tête du gabarit modifié) :

1. **Le fichier** : `input/photo.jpg` (nom sans espace ni accent). Le robot le trouve parce qu'il compile chaque
   CV avec `input/` comme dossier de ressources ; il n'est jamais copié dans `output/`. Format : 2,8 × 3,16 cm,
   soit un rapport largeur / hauteur de 850 / 960. L'en-tête est calibré pour cette hauteur : une photo plus
   haute fait passer le CV sur 2 pages.
2. **Le gabarit** : dans `config/cv_gabarit/gabarit.tex`, bloc `HEADER`, les deux lignes
   ```latex
   \noindent
   \begin{minipage}[c]{\textwidth}
   ```
   deviennent :
   ```latex
   \noindent
   \begin{minipage}[c]{0.12\textwidth}
       \includegraphics[width=2.8cm]{photo.jpg}
   \end{minipage}%
   \begin{minipage}[c]{0.88\textwidth}
   ```
   `\usepackage{graphicx}` est déjà dans le préambule. Sans photo, c'est l'inverse (`photo --aucune`).
3. **Contrôle** : `controle` (1 page, « image « photo.jpg » présente dans input/ »), puis `originaux` et
   `apercus --ecrire` pour régénérer le CV de secours et les aperçus.
