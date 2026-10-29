# Guide du formulaire — ce que tu dois remplir pour que le robot fonctionne

Ce guide accompagne **`FORMULAIRE.bat`**, à la racine du projet
(`C:\Users\<nom>\Documents\job-automation\FORMULAIRE.bat`). Il dit, **question par question**, quoi
répondre et quoi préparer.

> L'installation des logiciels (`Click_here_to_install_all_requirements.bat`) doit être faite
> **avant**. Le guide complet est dans **[GUIDE_INSTALLATION_COMPLET.md](GUIDE_INSTALLATION_COMPLET.md)**.

---

## Avant de commencer : prépare tout ceci

- [ ] **Ton CV** : fichier **LaTeX `.tex`** (recommandé) ou **PDF**. Mets dans le même dossier que
      le `.tex` les fichiers qu'il utilise (icônes, `.cls`…).
- [ ] **Ta lettre de motivation** : `.tex` (recommandé) ou `.pdf`.
- [ ] **Ta photo** : `.jpg` ou `.png`.
- [ ] **Un compte Google / Gmail** : ce sera ton **email de candidature**.
- [ ] **Un compte LinkedIn.**
- [ ] **Des comptes DeepSeek** (chat.deepseek.com) : **au moins 2**, idéalement **5**, chacun
      avec une adresse différente.
- [ ] **Des clés Apify** (conseillé) : sur https://console.apify.com → *Settings* → *API &
      Integrations* → copie le *Personal API token*. Un compte gratuit = une clé ; tu peux en
      donner jusqu'à **10**.
- [ ] **Une clé NopeCHA** (facultatif) : sur https://nopecha.com.
- [ ] **Un mot de passe** pour tes comptes candidat sur les sites d'entreprises : 8 caractères
      ou plus, différent de celui de ton email.
- [ ] **Une heure devant toi** : on peut s'arrêter et reprendre à tout moment.

---

## Comment se servir du formulaire

- **Lancer** : double-clique sur `FORMULAIRE.bat`. Un menu liste les 11 sections ; celles qui sont
  faites sont cochées ✓.
- **Entrée** = continuer à la prochaine section à faire.
- **Un numéro** (ex. `9`) = refaire cette section.
- **Q** = quitter. Tes réponses sont gardées.
- Pour chaque question :
  - un **exemple** s'affiche (`ex. : …`) ;
  - si une réponse existe déjà : **Entrée** = la garder ;
  - **choix dans une liste** : tape le **numéro** ou le texte exact ;
  - **plusieurs valeurs** : sépare-les par des **`;`** (ex. `France; Belgique`) ;
  - **facultatif** : Entrée = aucun.
- Une réponse invalide (email mal écrit, date au mauvais format…) est **redemandée** avec la raison.
- **Glisser un fichier** dans la fenêtre colle son chemin (utile en section 9).

> **Écris en français ou en anglais, comme tu veux.** Le robot remplit les formulaires dans la
> langue du site, et les parties fixes du CV et de la lettre sont traduites pour toi en section 9.

---

## Section 1 — Identité

| Question | Quoi répondre | Exemple |
|---|---|---|
| Civilité | 1 = M., 2 = Mme, 3 = aucun | `2` |
| Genre | 1 = Homme, 2 = Femme (sert aussi aux accords : « intéressée »…) | `2` |
| Prénom | Ton prénom | `Sarah` |
| Nom de famille | Ton nom | `Martin` |
| Email de candidature | **L'adresse Gmail** avec laquelle tu te connecteras dans les navigateurs du robot | `sarah.martin@gmail.com` |
| Téléphone complet | Avec l'indicatif du pays | `+33 6 12 34 56 78` |
| Adresse | Rue ou quartier | `12 rue de la République, Lyon` |
| Code postal | | `69002` |
| Ville | | `Lyon` |
| Pays de résidence | | `France` |
| Nationalité | | `Française` |
| Date de naissance | **JJ/MM/AAAA** (facultatif) | `14/05/2004` |

## Section 2 — Liens

| Question | Quoi répondre |
|---|---|
| Profil LinkedIn | Lien complet `https://www.linkedin.com/in/…` (obligatoire) |
| GitHub | Lien complet (facultatif) |
| Portfolio / site | Lien complet (facultatif) |

## Section 3 — Formation

| Question | Exemple |
|---|---|
| École / université (avec la ville) | `INSA Lyon (Institut National des Sciences Appliquées), Lyon` |
| Diplôme préparé ou obtenu | `Diplôme d'ingénieur` / `Master 2` |
| Spécialité | `Informatique` |
| Année en cours (ou « Diplômé ») | `5ème année` / `Diplômée` |
| Année d'obtention | `2027` |

## Section 4 — Objectif de recherche

C'est la section **la plus importante** : **tout le robot s'y adapte** (email, lettre, CV,
formulaires, filtre des offres).

| Question | Quoi répondre | Exemples |
|---|---|---|
| Que cherches-tu ? | 1 = stage, 2 = alternance, 3 = emploi | `1` |
| Ce que tu cherches, en quelques mots, **en minuscules** | La nature exacte de ta recherche | `stage de fin d'études` · `stage de fin d'année` · `emploi de développeur backend` · `alternance en data` |
| La même chose en anglais | | `end-of-studies internship` · `backend developer position` |
| Sigle | Facultatif | `PFE` · `PFA` |
| Types de contrat acceptés | Parmi `stage`, `alternance`, `cdi`, `cdd`, `freelance`, séparés par `;` | `stage` · `cdi; cdd` |
| Durée recherchée | Facultatif (vide pour un CDI) | `6 mois` · `2 mois` |
| Date de début | **JJ/MM/AAAA**, ou vide = dès que possible | `01/03/2027` |
| Stage sous convention ? | Seulement si « stage » : Oui / Non | `o` |
| Ta situation en 2-3 mots (minuscules) | | `élève ingénieur` · `étudiante en master` · `jeune diplômé` |
| Ta situation, un peu plus précise | | `élève-ingénieur en dernière année` |
| Qui tu es, en une demi-phrase | Utilisée dans l'email de candidature | `Élève-ingénieure en informatique à l'INSA Lyon` |
| Pays où tu cherches | Séparés par `;` | `France` |
| Ta mobilité | Une phrase | `Mobile dans toute la France` |
| Convention de stage (phrase) | Seulement si « stage » | `Oui, stage conventionné (convention fournie par l'école)` |

Ce que ça donne : pour « stage de fin d'études », sigle « PFE », « 6 mois », « 01/03/2027 »,
l'email et la lettre diront « **stage de fin d'études (PFE) de 6 mois à partir du 1er mars
2027** ». Une offre en CDI, par exemple, sera signalée « hors critères ».

## Section 5 — Langues

| Question | Quoi répondre |
|---|---|
| Tes langues et niveaux | Une langue par valeur, séparées par `;`. Ex. `Français : langue maternelle; Anglais : professionnel` |
| Niveau d'ANGLAIS (menus des formulaires) | 1 Native or bilingual · 2 Full professional · 3 Professional · 4 Limited working · 5 Elementary · 6 (aucun) |
| Niveau de FRANÇAIS | Idem |
| Niveau d'ARABE | Idem ; **6 (aucun)** si tu ne le parles pas |

Ne sous-déclare jamais : ces niveaux doivent correspondre à ton CV.

## Section 6 — Réponses types des formulaires

Les sites posent souvent ces questions : le robot recopie **tes** réponses.

| Question | Exemple si tu es citoyen(ne) européen(ne) | Exemple si tu as besoin d'un visa |
|---|---|---|
| Autorisé(e) à travailler en France ? (réponse complète) | `Oui, je suis citoyenne française.` | `Non : stage conventionné, avec visa long séjour « stagiaire ».` |
| Même question, réponse courte | `o` | `n` |
| Autorisé(e) à travailler en France / UE / Europe ? | `o` | `n` |
| Autorisé(e) à travailler dans ton pays de résidence ? | `o` | `o` |
| Besoin d'un visa / d'un parrainage ? (réponse complète) | `Non, aucun visa n'est nécessaire.` | `Oui : visa long séjour « stagiaire » (VLS-TS)…` |
| Même question, réponse courte | `n` | `o` |
| Téléphone SANS indicatif | `612345678` (sans le 0, quand l'indicatif est choisi à part) | |
| Téléphone AVEC indicatif, sans espaces | `+33612345678` | |
| Rémunération / gratification souhaitée | `Selon la grille de l'entreprise` | |
| La même en CHIFFRES (facultatif) : montant mensuel brut en euros | `700` — pour les champs qui n'acceptent qu'un nombre (Workday…) | |
| « Comment avez-vous connu l'offre ? » | `Site carrière de l'entreprise` | |
| Accepter d'être recontacté(e) (phrase) | `Oui, j'accepte d'être recontactée pour de futures opportunités` | |
| Question sur le handicap (phrase) | `Je ne souhaite pas répondre` | |
| Permis de conduire | `Permis B` (facultatif) | |

> Dès que les sections 1 à 6 sont faites, le formulaire écrit `config\profile.yaml` et
> `config\objectif.yaml`, à la structure exacte du projet d'origine.

## Section 7 — Mot de passe des sites et clés

| Demande | Quoi faire | Où c'est enregistré |
|---|---|---|
| Mot de passe des sites (masqué) | Saisis-le, puis confirme-le. Le robot l'utilise pour **créer et ouvrir tes comptes candidat** sur les sites d'entreprises (Workday, Taleo…). 8 caractères minimum. | `.env` → `MOT_DE_PASSE_SITES` |
| *(automatique)* 2e et 3e mots de passe | Créés par le formulaire (16 et 20 caractères) et **affichés une fois : note-les**. Si un site refuse ton mot de passe (trop court, pas assez complexe), le robot essaie le 2e, puis le 3e. | `.env` → `MOT_DE_PASSE_SITES_2`, `MOT_DE_PASSE_SITES_3` |
| Clés Apify | **Jusqu'à 10 clés**, séparées par `;`. Si l'une n'a plus de crédit, la suivante prend le relais. Entrée = aucune (pas de scraping automatique). | `input\Scraping\.env` → `APIFY_TOKEN`, `APIFY_TOKEN_2` … `APIFY_TOKEN_10` |
| Clé NopeCHA | **Une clé** (facultatif). Entrée = aucune. | `extensions\nopecha\manifest.json` → `"key"` |

Le mot de passe ne s'affiche jamais, n'est jamais écrit dans les journaux, et DeepSeek ne le voit
pas (il ne voit que `{MOT_DE_PASSE}`).

## Section 8 — Navigateurs et connexions

Le robot utilise ses **propres** fenêtres Chrome, et c'est **toi** qui t'y connectes une fois. Pour
chaque navigateur :

1. Une fenêtre Chrome s'ouvre sur les pages de connexion.
2. Connecte-toi :
   - **navigateurs 1 à 4** : **LinkedIn**, **Gmail** (avec l'**email de candidature** de la
     section 1 ; l'ordre des comptes Google n'a pas d'importance) et **DeepSeek** (un compte
     **différent** par navigateur, **créé avec une autre adresse que ton email de candidature**) ;
   - **navigateur 5** : **DeepSeek seulement** (un autre compte). Il sert au tri des offres.
3. Si demandé, charge l'extension **NopeCHA** : dans cette fenêtre, ouvre `chrome://extensions`
   → active **Mode développeur** → **Charger l'extension non empaquetée** → choisis le dossier
   `job-automation\extensions\nopecha`.
4. Reviens dans la fenêtre du formulaire et appuie sur **Entrée** : le programme **vérifie tout
   seul** chaque connexion (✓ ou ✗ avec la raison). Recommence tant qu'il reste un ✗.

- **Navigateurs 2 à 4** : recommandés, mais tu peux répondre `n` (ou `P` pour passer). Ils sont
  alors retirés de la rotation.
- **Navigateurs 1 et 5** : obligatoires.
- **Navigateur 5 — à connecter à DeepSeek une seule fois.** Si ce n'est pas fait ici, fais-le plus
  tard avec `LANCER.bat` → **8** → **5**. C'est le navigateur qui trie les offres. Tant que ce
  n'est pas fait, les nouvelles offres récupérées restent « À vérifier », sans rien perdre.

## Section 9 — CV, lettre de motivation et photo

1. Donne le chemin de **ton CV**, de **ta lettre**, puis de **ta photo**. Le plus simple est de
   **glisser le fichier** dans la fenêtre.
2. Réponds `o` si ton CV est en anglais, sinon Entrée.
3. Le programme travaille avec DeepSeek, dans le navigateur 1, pendant **3 à 5 minutes** :
   - si c'est un PDF, il le recrée en LaTeX ;
   - il repère dans ton CV ce qui s'adapte à chaque offre (profil, expériences, projets,
     compétences) et ce qui reste fixe (en-tête, formation, langues…). **Ton document n'est jamais
     réécrit** : toute version qui modifierait un seul mot est refusée ;
   - il traduit les parties fixes dans l'autre langue ;
   - il fait la même chose avec ta lettre (le corps en 3 paragraphes) ;
   - il rédige tes **« faits réels »** : tes expériences, chiffres et projets, tels qu'écrits dans
     ton CV et ta lettre.
4. **Aperçus** : ton CV en français, ton CV en anglais et ta lettre s'ouvrent. Vérifie qu'ils
   ressemblent à tes documents.
5. Les **faits réels** s'ouvrent dans le Bloc-notes : **relis-les bien**, corrige si besoin,
   **enregistre**, puis appuie sur Entrée. Le robot n'utilisera **que** ces faits : il n'inventera
   rien d'autre.
6. **« Les aperçus et les faits réels sont-ils corrects ? »**
   - **o** : les fichiers sont écrits ;
   - **n** : **rien n'est écrit**, tu peux relancer la section.

## Section 10 — Domaine recherché et recherches LinkedIn

**1. Le domaine** (tri automatique des offres scrapées) : 6 questions.
| Question | Exemple |
|---|---|
| Quel DIPLÔME possèdes-tu (ou prépares-tu) exactement ? | `Diplôme d'ingénieur en informatique, INSA Lyon, 2027` |
| Quel DOMAINE (ou quels postes) veux-tu ? | `Informatique : développement, IA, data, cloud` |
| Qu'est-ce qu'il faut INCLURE en plus ? | `un poste IT combiné avec un autre domaine est accepté` |
| Qu'est-ce qu'il faut EXCLURE ? | `la cybersécurité ; le commercial, l'industriel, le marketing` |
| Que faire d'un poste MIXTE ? | `accepter si le cœur du poste est dans mon domaine, refuser sinon` |
| Autres précisions (facultatif) | `les postes de chef de projet digital sont acceptés` |
| Activer le tri automatique ? | `o` |

**2. Les recherches LinkedIn** surveillées chaque jour. Une par ligne, **ligne vide** pour finir :
- soit un **lien** de recherche LinkedIn :
  `https://www.linkedin.com/jobs/search/?keywords=…&location=…`
- soit **`mots-clés | lieu`** : le lien est construit pour toi. Ex. `stage data science | France`

## Section 11 — Vérification finale

Le programme crée l'Excel des offres s'il manque, puis lance la vérification du robot. Tu dois voir
**« Configuration OK »**. Sinon, le message dit quelle section refaire.

---

## Et après ?

1. `LANCER.bat` → **3** : un test qui remplit 3 formulaires **sans rien envoyer**.
2. `LANCER.bat` → **4** : lance les candidatures pour de vrai.
3. `input\Scraping\LANCER_SCRAPING.bat` : récupère chaque jour les nouvelles offres (laisse la
   fenêtre ouverte).

Pour modifier une réponse plus tard : `FORMULAIRE.bat`, puis le **numéro** de la section.
