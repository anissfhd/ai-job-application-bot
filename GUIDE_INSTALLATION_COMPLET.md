# Guide complet — installer et faire fonctionner le robot pour une autre personne

Ce guide décrit **toutes** les étapes, dans l'ordre, pour faire tourner le robot de candidatures
chez une nouvelle personne, sur une autre machine Windows. Il indique aussi l'emplacement exact de
chaque fichier (`.bat`, CV, lettre, photo, réglages, résultats).

> Pour savoir **quoi répondre** à chaque question du formulaire, lire ensuite
> **[GUIDE_FORMULAIRE.md](GUIDE_FORMULAIRE.md)**.

---

## Sommaire

1. [Vue d'ensemble : comment le robot fonctionne](#1-vue-densemble--comment-le-robot-fonctionne)
2. [Ce qu'il faut avoir avant de commencer](#2-ce-quil-faut-avoir-avant-de-commencer)
3. [Étape A — Sur le PC d'origine : créer la copie vierge](#3-étape-a--sur-le-pc-dorigine--créer-la-copie-vierge)
4. [Étape B — Sur la nouvelle machine : extraire le projet](#4-étape-b--sur-la-nouvelle-machine--extraire-le-projet)
5. [Étape C — Installer les logiciels](#5-étape-c--installer-les-logiciels)
6. [Étape D — Remplir le formulaire (obligatoire)](#6-étape-d--remplir-le-formulaire-obligatoire)
7. [Étape E — Premier test sans rien envoyer](#7-étape-e--premier-test-sans-rien-envoyer)
8. [Étape F — Lancer les candidatures](#8-étape-f--lancer-les-candidatures)
9. [Étape G — Le scraping automatique des offres](#9-étape-g--le-scraping-automatique-des-offres)
10. [Utilisation au quotidien](#10-utilisation-au-quotidien)
11. [Emplacement de TOUS les fichiers](#11-emplacement-de-tous-les-fichiers)
12. [Les statuts de l'Excel](#12-les-statuts-de-lexcel)
13. [Les 5 navigateurs du robot](#13-les-5-navigateurs-du-robot)
14. [Modifier une information plus tard](#14-modifier-une-information-plus-tard)
15. [Dépannage](#15-dépannage)
16. [Sécurité et confidentialité](#16-sécurité-et-confidentialité)

---

## 1. Vue d'ensemble : comment le robot fonctionne

```
 LinkedIn ──(Apify, 1 fois par jour)──► Excel : offres « À vérifier »
                                            │
                    verify_eligibility (tri du domaine par DeepSeek, navigateur 5)
                         │                                   │
                  dans le domaine                       hors domaine
                         ▼                                   ▼
              Excel : « À traiter »             feuille « Écartées » (gardée)
                         │
          robot de candidature (LANCER.bat -> 4), en continu :
            1. lit l'offre (page LinkedIn ou site de l'entreprise)
            2. DeepSeek adapte le CV et la lettre à l'offre (gabarit à trous, 1 page)
            3. remplit le formulaire du site, dépose CV + lettre, envoie
               (ou envoie un email par Gmail si l'offre le demande)
            4. note le résultat dans l'Excel (« Postulé », « Action manuelle »…)
```

- **Aucune clé d'API payante n'est nécessaire** : le robot utilise les chats web de DeepSeek,
  dans ses propres navigateurs Chrome.
- Le robot **n'invente jamais** d'information : tout vient du profil, du CV et de la lettre.
- Le robot **n'a jamais les mots de passe** LinkedIn, Google ou DeepSeek : la personne s'y
  connecte elle-même une fois dans chaque navigateur du robot.

---

## 2. Ce qu'il faut avoir avant de commencer

### Matériel et logiciels
| Élément | Détail |
|---|---|
| PC | Windows 10 ou 11, 8 Go de RAM conseillés, connexion Internet stable |
| Droits | Compte administrateur (pour installer Chrome et MiKTeX) |
| Espace disque | Environ 3 Go (Python, MiKTeX, navigateurs) |

Python, Google Chrome et MiKTeX (compilation LaTeX) sont **installés automatiquement** à l'étape C.

### Comptes (à créer avant l'étape D)
| Compte | Obligatoire ? | À quoi il sert |
|---|---|---|
| **Google / Gmail** | Oui | Adresse de candidature, connexion « avec Google », réception des emails de confirmation, envoi des candidatures par email |
| **LinkedIn** | Oui | Lire les offres, postuler en « Candidature simplifiée » |
| **DeepSeek** (chat.deepseek.com) | Oui : au moins 2 comptes (navigateurs 1 et 5), **5 conseillés** | Adapter le CV et la lettre, remplir les formulaires, trier les offres |
| **Apify** (apify.com) | Conseillé, jusqu'à 10 comptes | Récupérer automatiquement les nouvelles offres LinkedIn chaque jour |
| **NopeCHA** (nopecha.com) | Facultatif | Résoudre automatiquement certains CAPTCHA |

> **Un compte DeepSeek différent par navigateur** : chaque compte a une limite d'utilisation.
> Quand un compte sature, le robot passe au navigateur suivant.

### Documents à préparer (étape D, section 9)
| Document | Format | Remarques |
|---|---|---|
| **CV** | `.tex` (LaTeX, **recommandé**) ou `.pdf` | Le LaTeX est utilisé **tel quel**. Un PDF est d'abord recréé en LaTeX par DeepSeek, puis tu le valides sur aperçu. |
| **Lettre de motivation** | `.tex` (recommandé) ou `.pdf` | Le corps de la lettre est découpé en 3 paragraphes, que le robot réécrit pour chaque offre. |
| **Photo** | `.jpg`, `.jpeg` ou `.png` | Elle prend le nom de fichier attendu par le CV LaTeX. |

Si le CV LaTeX utilise d'autres fichiers (icônes, `.cls`, `.sty`), **mets-les dans le même
dossier que le `.tex`** : ils sont copiés automatiquement.

---

## 3. Étape A — Sur le PC d'origine : créer la copie vierge

> À faire sur le PC qui contient déjà le projet en fonctionnement.

1. Ferme le robot et le fichier Excel.
2. Double-clique sur **`EXPORTER_PROJET_VIERGE.bat`**, à la racine du projet
   (`C:\Users\<toi>\Documents\job-automation\EXPORTER_PROJET_VIERGE.bat`).
3. Le programme crée, **à côté** du dossier du projet :
   - un dossier `job-automation_VIERGE_<date>_<heure>\` ;
   - une archive **`job-automation_VIERGE_<date>_<heure>.zip`** : c'est **elle** qu'il faut donner.
4. Il fouille ensuite la copie. Le message **« aucune donnée personnelle ni aucun secret »** doit
   s'afficher. Sinon, il liste les traces restantes, à corriger avant de donner l'archive.

Ce que contient la copie :
- tout le robot (code, consignes, réglages, mémoire des sites) ;
- **aucune** donnée personnelle, avec à la place des fichiers vides à la même structure : profil,
  objectif, critères, liens de recherche, Excel neuf, `.env` sans mot de passe ni clé.

Le dossier d'origine **n'est jamais modifié** par l'export.

---

## 4. Étape B — Sur la nouvelle machine : extraire le projet

1. Copie l'archive `.zip` sur la nouvelle machine.
2. Clic droit → **Extraire tout…** → choisis `C:\Users\<nom>\Documents\`.
3. **Renomme** le dossier extrait en **`job-automation`**. Le chemin final doit être :
   ```
   C:\Users\<nom>\Documents\job-automation\
   ```
   Évite les chemins qui contiennent des caractères spéciaux ou OneDrive.

---

## 5. Étape C — Installer les logiciels

1. Dans `job-automation\`, double-clique sur **`Click_here_to_install_all_requirements.bat`**.
2. Réponds **Oui** à la fenêtre Windows qui demande les droits administrateur.
3. Le programme installe s'ils manquent (10 à 20 minutes la première fois) :
   - Python 3.14 ;
   - Google Chrome ;
   - MiKTeX, réglé pour installer seul les paquets LaTeX manquants ;
   - les paquets Python, aux versions exactes du PC d'origine (`requirements-verrouille.txt`).
4. À la fin, il indique : **« Ensuite : double-clique sur FORMULAIRE.bat »**.

> Une ou deux alertes « profil vide » à cette étape sont **normales** : le formulaire n'est pas
> encore rempli.

---

## 6. Étape D — Remplir le formulaire (obligatoire)

Double-clique sur **`FORMULAIRE.bat`**, à la racine du projet. Le détail question par question est
dans **[GUIDE_FORMULAIRE.md](GUIDE_FORMULAIRE.md)**.

| N° | Section | Ce qu'elle écrit |
|---|---|---|
| 1 | Identité | `config/profile.yaml` |
| 2 | Liens (LinkedIn, GitHub, portfolio) | `config/profile.yaml` |
| 3 | Formation | `config/profile.yaml` |
| 4 | Objectif de recherche (stage PFE / PFA, alternance, emploi, durée, date de début…) | `config/objectif.yaml` + `config/profile.yaml` |
| 5 | Langues et niveaux | `config/profile.yaml` |
| 6 | Réponses types des formulaires (autorisation de travail, visa, téléphone, salaire…) | `config/profile.yaml` |
| 7 | Mot de passe des sites, clés Apify, clé NopeCHA | `.env`, `input/Scraping/.env`, `extensions/nopecha/manifest.json` |
| 8 | Navigateurs : connexions LinkedIn, Gmail, DeepSeek et extension NopeCHA, **vérifiées automatiquement** | profils Chrome dans `C:\Users\<nom>\.tappi\` |
| 9 | CV, lettre, photo → gabarit du robot (DeepSeek), **aperçus à valider** | `config/cv_gabarit/`, `config/deepseek/nouveaux/`, `input/` |
| 10 | Domaine recherché et recherches LinkedIn | `input/Scraping/criteres_domaine.json`, `input/Scraping/liens.txt` |
| 11 | Vérification finale | crée `input/entreprises.xlsx` et lance la vérification du robot |

Règles du formulaire :
- **Toutes les sections sont obligatoires.** Quelques questions sont facultatives (ex. GitHub) :
  appuie sur Entrée pour les laisser vides.
- **Ordre imposé.** La section 8 demande que l'identité soit faite. La section 9 demande les
  sections 1 à 6 et 8. La section 11 demande toutes les autres.
- **Reprise possible.** Les réponses sont gardées au fur et à mesure dans
  `config/formulaire_reponses.json`. On peut fermer la fenêtre, puis relancer `FORMULAIRE.bat` et
  appuyer sur Entrée pour continuer.
- **Refaire une seule section** : relance `FORMULAIRE.bat` et tape son **numéro**.
- **Sauvegarde automatique.** Avant chaque remplacement de fichier, l'ancienne version est copiée
  dans `config/sauvegardes_formulaire/<date>/`.
- **Contrôle des réponses.** Email, téléphone, date (JJ/MM/AAAA), lien (https://…) et Oui/Non sont
  vérifiés ; une réponse invalide est redemandée.

---

## 7. Étape E — Premier test sans rien envoyer

1. Ajoute 2 ou 3 offres dans l'Excel. Plusieurs façons :
   - `LANCER.bat` → **9** : coller des liens d'offres ;
   - ou `input\Scraping\LANCER_SCRAPING.bat` (voir étape G).
2. `LANCER.bat` → **2** : génère **seulement** le CV et la lettre de la 1re offre. Relis les PDF dans
   `output\Candidatures\<Entreprise>_<date>\`.
3. `LANCER.bat` → **3** : remplit les formulaires de 3 offres **sans envoyer** (statut
   « Prêt (test) »). Regarde la fenêtre Chrome travailler.
4. Si tout est correct, remets ces lignes à **« À traiter »** dans l'Excel (`LANCER.bat` → **6**).

---

## 8. Étape F — Lancer les candidatures

- `LANCER.bat` → **4** : **envoi réel**, en continu. Le robot traite l'Excel de haut en bas, sans
  limite par jour. Quand il n'y a plus d'offre, il revérifie toutes les 10 minutes et peut lancer
  un scraping automatique (au plus un toutes les 2 heures).
- `LANCER.bat` → **5** : pareil, mais il postule **à tout** (CDI, CDD, alternance), même hors de
  l'objectif, sans rien demander.
- Pour arrêter : **Ctrl+C** dans la fenêtre du robot, ou ferme-la. L'offre en cours est remise
  « À traiter ».

---

## 9. Étape G — Le scraping automatique des offres

- **Où** : `input\Scraping\`
- **Lancer** : double-clique sur **`input\Scraping\LANCER_SCRAPING.bat`**, puis donne l'heure du
  passage quotidien (ex. `09:00`, ou Entrée pour tout de suite). **Laisse cette fenêtre ouverte** :
  elle refait un passage toutes les 24 h.
- **Scraping automatique** : même sans cette fenêtre, le robot de candidature lance lui-même un
  passage quand l'Excel est vide (au plus un toutes les 2 heures).
> **À faire une seule fois avant le premier scraping : connecter le navigateur 5 à DeepSeek**
> (`LANCER.bat` → **8** → **5**). C'est le navigateur qui trie les offres. Tant que ce n'est pas
> fait, les nouvelles offres récupérées restent « À vérifier », sans rien perdre : elles seront
> triées dès que le navigateur 5 sera connecté (`LANCER.bat` → **12** pour trier tout de suite).

- **Tri automatique du domaine** (`verification_domaine: true` dans
  `input\Scraping\config_scraping.json`) :
  1. les nouvelles offres arrivent au statut **« À vérifier »**, que le robot de candidature ignore ;
  2. le robot séparé **`scripts\verify_eligibility.py`** s'ouvre dans sa propre fenêtre et fait
     trier chaque offre par DeepSeek (navigateur 5), d'abord sur le titre, puis sur la description,
     puis sur le site de l'entreprise si besoin ;
  3. les offres dans le domaine passent en **« À traiter »** ; les autres vont dans la feuille
     **« Écartées »** de l'Excel, avec la raison ;
  4. la fenêtre se ferme toute seule à la fin.
- **Changer le domaine** : `input\Scraping\PARAMETRER_SCRAPING.bat` (ou `LANCER.bat` → **13**).
- **Trier tout de suite** les offres « À vérifier » : `LANCER.bat` → **12**.

Réglages (`input\Scraping\config_scraping.json`) :
| Clé | Rôle |
|---|---|
| `intervalle_minutes` | 1440 = un passage par jour |
| `offres_max_par_lien` | Nombre d'offres récupérées par recherche |
| `cout_max_par_passage_usd` | Plafond de dépense Apify par passage |
| `candidats_max` | Écarte les offres ayant déjà plus de N candidats (100) |
| `priorite_easy_apply` | Offres « Candidature simplifiée » placées en premier |
| `verification_domaine` | Active le tri automatique du domaine |

---

## 10. Utilisation au quotidien

| Action | Comment |
|---|---|
| Lancer les candidatures | `LANCER.bat` → 4 |
| Voir l'Excel | `LANCER.bat` → 6 (fermer Excel avant de relancer le robot) |
| Voir les CV / lettres envoyés | `LANCER.bat` → 7 |
| Se reconnecter dans un navigateur du robot | `LANCER.bat` → 8 → numéro du navigateur (1 à 5) |
| Ajouter des offres à la main | `LANCER.bat` → 9 |
| Rapport du jour | `LANCER.bat` → 10 |
| Ce que le robot a appris sur chaque site | `LANCER.bat` → 11 |
| Trier les offres « À vérifier » | `LANCER.bat` → 12 |
| Changer le domaine recherché | `LANCER.bat` → 13 |
| Scraping quotidien | `input\Scraping\LANCER_SCRAPING.bat` |
| Vérifier que tout est en ordre | `LANCER.bat` → 1 |

Chaque jour, regarde dans l'Excel les lignes **« Action manuelle »**. Le commentaire dit ce qui a
bloqué : CAPTCHA à images, code SMS, question sans réponse dans le profil… Règle le problème, puis
remets la ligne à « À traiter ».

---

## 11. Emplacement de TOUS les fichiers

Racine du projet : `C:\Users\<nom>\Documents\job-automation\`

### Fichiers `.bat` (à double-cliquer)
| Fichier | Rôle |
|---|---|
| `Click_here_to_install_all_requirements.bat` | Installation des logiciels (une fois) |
| `FORMULAIRE.bat` | Installation du candidat (obligatoire la 1re fois ; relançable) |
| `LANCER.bat` | Menu principal du robot |
| `EXPORTER_PROJET_VIERGE.bat` | Copie du projet sans données, pour une autre personne |
| `input\Scraping\LANCER_SCRAPING.bat` | Scraping quotidien des offres LinkedIn (Apify) |
| `input\Scraping\PARAMETRER_SCRAPING.bat` | Domaine recherché (tri des offres) |

### Informations du candidat (écrites par le formulaire)
| Fichier | Contenu |
|---|---|
| `config\profile.yaml` | Identité, liens, formation, recherche, langues, réponses types |
| `config\objectif.yaml` | Objectif : type (stage / alternance / emploi), durée, date de début, sigle (PFE…) |
| `.env` | `MOT_DE_PASSE_SITES` : mot de passe des comptes candidat sur les sites d'entreprises ; `MOT_DE_PASSE_SITES_2` et `_3` : versions de secours (créées par le formulaire), essayées si un site refuse le précédent |
| `input\Scraping\.env` | Clés Apify (`APIFY_TOKEN`, `APIFY_TOKEN_2` … `APIFY_TOKEN_10`) |
| `extensions\nopecha\manifest.json` | Clé NopeCHA (champ `"key"`) |
| `input\Scraping\criteres_domaine.json` | Diplôme, domaine, à inclure, à exclure, cas mixtes, précisions |
| `input\Scraping\liens.txt` | Recherches LinkedIn surveillées (une par ligne) |
| `config\formulaire_reponses.json` | Réponses du formulaire (pour reprendre) |
| `config\sauvegardes_formulaire\<date>\` | Anciennes versions, avant chaque remplacement |

### CV, lettre et photo
| Fichier | Contenu |
|---|---|
| `input\cv_original.tex` | Le CV LaTeX d'origine (secours si le gabarit échoue) |
| `input\lm_original.tex` | La lettre LaTeX d'origine |
| `input\<nom de la photo>.jpg` | La photo, sous le nom attendu par le CV |
| `input\*.png`, `input\*.cls`, `input\*.sty` | Autres ressources du CV (icônes…) |
| `config\cv_gabarit\gabarit.tex` | Le CV à trous (`<<PROFIL>>`, `<<E1_P>>`…) |
| `config\cv_gabarit\reference_en.json` | Textes d'origine des parties variables (longueurs cibles) |
| `config\cv_gabarit\fixe_fr.json` / `fixe_en.json` | Parties fixes du CV, en français et en anglais |
| `config\cv_gabarit\lettre.tex` | La lettre à trous |
| `config\cv_gabarit\lettre_fixe.json` | Parties fixes de la lettre (FR/EN) + longueur des 3 paragraphes |
| `config\cv_gabarit\apercu_cv_fr.pdf` / `apercu_cv_en.pdf` | Aperçus validés dans le formulaire |
| `config\deepseek\nouveaux\message_1.txt` | Règles d'écriture données à DeepSeek |
| `config\deepseek\nouveaux\message_2.txt` | « Mes faits réels » : la seule matière autorisée |
| `config\deepseek\nouveaux\message_3.txt` | Format des réponses |

### Offres et résultats
| Emplacement | Contenu |
|---|---|
| `input\entreprises.xlsx` | Feuille **Offres** (une ligne par offre) + feuille **Écartées** |
| `input\sauvegardes\` | Sauvegardes horaires automatiques de l'Excel |
| `output\Candidatures\<Entreprise>_<date>\` | CV et lettre envoyés (PDF + TEX), `offre_page.txt`, `offre.json`, `email_candidature.txt`, `email_deepseek.txt`, `captures\` |
| `logs\orchestrator.log` | Journal du robot de candidature |
| `logs\verify_eligibility.log` | Journal du tri des offres |
| `input\Scraping\logs\scraping.log` | Journal du scraping |
| `rapports\` | Rapports quotidiens |
| `memoire\` | Ce que le robot a appris sur chaque site (leçons, séquences d'actions) |

### Réglages généraux (rarement à toucher)
| Fichier | Contenu |
|---|---|
| `config\config.yaml` | Réglages du robot (navigateurs, DeepSeek, délais, garde-fous…) |
| `input\Scraping\config_scraping.json` | Réglages du scraping |
| `config\modeles\` | Modèles vides de la structure exacte (utilisés par le formulaire et l'export) |
| `prompts\` | Consignes envoyées à DeepSeek (adaptées automatiquement à l'objectif) |

---

## 12. Les statuts de l'Excel

| Statut | Signification |
|---|---|
| **À vérifier** | Offre scrapée, en attente du tri du domaine (ignorée par le robot) |
| **À traiter** (ou vide) | Le robot va postuler |
| **En cours** | Le robot y travaille |
| **Postulé** | Candidature envoyée (formulaire ou email) |
| **Prêt (test)** | Formulaire rempli sans envoi (mode test) |
| **Action manuelle** | Le robot a besoin de toi : lire le commentaire |
| **Erreur** | Échec technique, retenté automatiquement (nombre d'essais : `max_tentatives_par_offre` dans `config\config.yaml`) |
| **Offre expirée** | L'offre n'existe plus |
| **Ignoré** / **Fait** | Mis à la main : le robot n'y touche plus |

La feuille **« Écartées »** contient les offres hors domaine, avec la raison. Pour en récupérer
une, copie sa ligne dans la feuille « Offres » avec le statut « À traiter ».

---

## 13. Les 5 navigateurs du robot

| N° | Port | Dossier du profil Chrome | Connexions | Rôle |
|---|---|---|---|---|
| 1 | 9222 | `C:\Users\<nom>\.tappi\job-automation` | LinkedIn, Gmail, DeepSeek (compte 1) | Principal |
| 2 | 9223 | `…\.tappi\job-automation-2` | LinkedIn, Gmail, DeepSeek (compte 2) | Secours |
| 3 | 9224 | `…\.tappi\job-automation-3` | LinkedIn, Gmail, DeepSeek (compte 3) | Secours |
| 4 | 9225 | `…\.tappi\job-automation-4` | LinkedIn, Gmail, DeepSeek (compte 4) | Secours |
| 5 | 9226 | `…\.tappi\job-automation-verif` | DeepSeek (compte 5) seulement | Tri du domaine des offres |

- **Navigateur 5 — à connecter à DeepSeek une seule fois** (`LANCER.bat` → **8** → **5**). C'est
  le navigateur qui trie les offres. Tant que ce n'est pas fait, les nouvelles offres récupérées
  restent « À vérifier », sans rien perdre.
- **Obligatoires** : 1 et 5. **Recommandés** : 2 à 4. Un navigateur 2 à 4 non préparé est retiré
  de la rotation dans `config\config.yaml`.
- **Compte Google** : dans les navigateurs 1 à 4, l'**email de candidature** du profil doit être
  connecté. L'ordre des comptes n'a pas d'importance : le robot choisit toujours ce compte par son
  adresse (écran « Choisir un compte », boîte Gmail avec `authuser=`), sur le PC, sans que DeepSeek
  la voie.
- **Comptes DeepSeek** : crée-les avec d'**autres adresses** que ton email de candidature, sinon
  DeepSeek connaîtrait cet email par le compte lui-même.
- **Extension NopeCHA** : à charger une fois dans chaque navigateur, via `chrome://extensions` →
  Mode développeur → « Charger l'extension non empaquetée » → dossier `extensions\nopecha`.
- **Se reconnecter** plus tard : `LANCER.bat` → 8 → numéro du navigateur.

---

## 14. Modifier une information plus tard

| Changement | Comment |
|---|---|
| Téléphone, adresse, objectif, réponses types… | `FORMULAIRE.bat` → numéro de la section |
| Nouveau CV / nouvelle lettre / nouvelle photo | `FORMULAIRE.bat` → **9** |
| Nouveau mot de passe des sites, nouvelles clés | `FORMULAIRE.bat` → **7** |
| Se reconnecter à un site | `FORMULAIRE.bat` → **8**, ou `LANCER.bat` → 8 |
| Autre domaine / autres recherches LinkedIn | `FORMULAIRE.bat` → **10**, ou `PARAMETRER_SCRAPING.bat` |

Après chaque modification, `LANCER.bat` → **1** pour vérifier.

---

## 15. Dépannage

| Problème | Solution |
|---|---|
| « identite.… est vide (lance FORMULAIRE.bat) » | Le formulaire n'est pas terminé : `FORMULAIRE.bat` |
| « config/cv_gabarit/… manquant » | Section 9 du formulaire non faite |
| « Excel verrouillé » | Ferme le fichier Excel ; le robot réessaie tout seul |
| DeepSeek « n'a pas commencé à répondre » | Compte saturé : le robot passe au navigateur suivant. Ajoute des comptes DeepSeek (navigateurs 2 à 4). |
| Commentaire « Limite quotidienne Easy Apply » | LinkedIn bloque les « Candidatures simplifiées » pour la journée : l'offre reste « À traiter », le robot passe aux 10 offres suivantes hors Easy Apply puis réessaie |
| Le tri ne se lance pas | Navigateur 5 non connecté à DeepSeek : `LANCER.bat` → 8 → 5, puis → 12 |
| Section 9 : « traduction incomplète » ou « repérage impossible » | Relancer la section 9 (DeepSeek varie d'un essai à l'autre). Réponses brutes dans `logs\formulaire_*.txt`. |
| Le CV aperçu ne ressemble pas à l'original | Répondre **n** à la validation : rien n'est écrit, relancer la section 9 |

---

## 16. Sécurité et confidentialité

- **Fichiers secrets** : `.env`, `input\Scraping\.env` et `extensions\nopecha\manifest.json`
  contiennent le mot de passe et les clés. Ne les partage jamais : pour donner le projet, utilise
  **toujours** `EXPORTER_PROJET_VIERGE.bat`.
- **Mots de passe** : le robot ne les écrit jamais dans les journaux ; DeepSeek ne voit que
  `{MOT_DE_PASSE}`, `{MOT_DE_PASSE_2}` et `{MOT_DE_PASSE_3}`.
- **Fenêtres des sites** (« Fichier bien reçu » + OK, « Voulez-vous continuer ? ») : le robot les
  ferme lui-même (OK) et continue ; aucune offre ne peut le retenir plus de 25 minutes
  (`duree_max_offre_minutes` dans `config\config.yaml`).
- **Données personnelles et DeepSeek** (`scripts\confidentialite.py`) : DeepSeek ne voit **jamais**
  le prénom, le nom, l'email, le téléphone ni l'adresse.
  - **Texte envoyé** : chaque apparition est remplacée par un repère (`{PRENOM}`, `{NOM}`,
    `{EMAIL}`, `{TELEPHONE}`, `{ADRESSE}`…), y compris en majuscules, dans un lien ou dans un nom de
    fichier.
  - **Réponses reçues** : quand DeepSeek décide de taper `{EMAIL}`, le robot tape le vrai email, sur
    le PC.
  - **Captures d'écran** : elles sont **floutées** aux endroits où ces informations apparaissent,
    **sauf le numéro de téléphone** : DeepSeek doit le voir pour repérer un indicatif faux ou en
    double (le texte, lui, reste masqué).
  - **Restent visibles**, parce qu'elles aident DeepSeek : ville, pays, nationalité, date de
    naissance, formation.
- **Compte Google** : il est choisi par son **adresse**, sur le PC (écran « Choisir un compte »,
  boîte Gmail ouverte avec `authuser=`), jamais « le premier compte ». DeepSeek n'en a pas besoin.
- **Comptes DeepSeek** : crée-les avec d'autres adresses que l'email de candidature.
- **Limite** : si un site affiche le CV sous forme d'**image**, le texte de cette image ne peut
  pas être flouté. C'est rare et bref.
- **Données sensibles** : le robot ne saisit jamais de numéro de passeport, de sécurité sociale ni
  de coordonnées bancaires (liste `ne_jamais_communiquer` du profil).
