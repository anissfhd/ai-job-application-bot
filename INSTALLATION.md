# Installation guide — from A to Z

This guide takes you from a fresh Windows PC to a robot that applies to offers for you. Follow the
steps in order. Plan about **1 hour**, plus the time to create the accounts.

> The robot's interface (menus, questionnaire, Excel statuses) is in **French**. This guide gives the
> meaning of every French word you will see.

| Step | What you do | Time |
|---|---|---|
| [0](#step-0--create-the-accounts-you-need) | Create the accounts you need | 15–30 min |
| [1](#step-1--download-the-project) | Download the project | 2 min |
| [2](#step-2--install-the-software) | Install the software (one click) | 10–20 min |
| [3](#step-3--prepare-your-documents) | Prepare your CV, cover letter and photo | 5 min |
| [4](#step-4--add-the-nopecha-extension-optional) | Add the NopeCHA extension (optional) | 3 min |
| [5](#step-5--replace-the-fictional-data-with-yours) | Replace the fictional data with yours | 30–45 min |
| [6](#step-6--enter-your-secrets) | Enter your secrets (passwords, keys) | 5 min |
| [7](#step-7--log-in-to-the-robots-browsers) | Log in to the robot's browsers | 10 min |
| [8](#step-8--choose-what-to-search-for) | Choose what to search for | 5 min |
| [9](#step-9--check-the-installation) | Check the installation | 1 min |
| [10](#step-10--test-without-sending-anything) | Test without sending anything | 15 min |
| [11](#step-11--start-applying) | Start applying | — |
| [12](#step-12--find-new-offers-automatically) | Find new offers automatically | — |

---

## Step 0 — Create the accounts you need

| Account | Required? | What it is used for |
|---|---|---|
| **Gmail** | Yes | Your application email: "Sign in with Google", confirmation emails, applications sent by email |
| **LinkedIn** | Yes | Reading offers and "Easy Apply" applications |
| **[DeepSeek](https://chat.deepseek.com)** | Yes — **at least 2**, **5 recommended** | Writing the CV and letter, filling in forms, filtering offers |
| **[Apify](https://apify.com)** | Recommended (free plan, up to 10 accounts) | Collecting new LinkedIn offers every day |
| **[NopeCHA](https://nopecha.com)** | Optional | Solving CAPTCHAs automatically (100/day free without an account) |

Tips:
- **One DeepSeek account per robot browser.** Each account has a usage limit; when one is saturated,
  the robot switches to the next browser.
- Create the DeepSeek accounts with **email addresses other than your application email**.
- **Apify key**: Apify Console → *Settings* → *API & Integrations* → copy your personal API token.

---

## Step 1 — Download the project

**Option A — ZIP (simplest):** on the GitHub page, click **Code → Download ZIP**, then right-click the
file → **Extract All…** → choose `C:\Users\<you>\Documents\`.

**Option B — Git:**
```bash
git clone https://github.com/anissfhd/ai-job-application-bot.git
```

The final path should look like `C:\Users\<you>\Documents\ai-job-application-bot\`.
Avoid OneDrive folders and paths containing spaces or accents.

---

## Step 2 — Install the software

1. In the project folder, double-click **`Click_here_to_install_all_requirements.bat`**.
2. Answer **Yes** when Windows asks for administrator rights.
3. The installer adds what is missing (what is already installed and up to date is left alone):
   - **Python 3.14** (64-bit);
   - **Google Chrome**;
   - **MiKTeX**, configured to install missing LaTeX packages by itself;
   - a private Python environment (`.venv`) with the exact package versions of
     `requirements-verrouille.txt`;
   - the working folders, the Excel tracking file and the `.env` file (copied from `.env.example`).
4. At the end it says *"Ensuite : double-clique sur FORMULAIRE.bat"* ("next: double-click
   FORMULAIRE.bat").

> One or two warnings about an empty profile at this stage are **normal**.

---

## Step 3 — Prepare your documents

| Document | Format | Notes |
|---|---|---|
| **CV** | `.tex` (LaTeX, **recommended**) or `.pdf` / `.docx` | A LaTeX CV can be used as is; a PDF is only read to fill in your information |
| **Cover letter** | `.tex` (recommended) or `.pdf` / `.docx` | Its body becomes 3 paragraphs that the robot rewrites for each offer |
| **Photo** (optional) | `.jpg`, `.jpeg`, `.png` | Cropped automatically to the size of the CV header (2.8 × 3.16 cm) |

If your LaTeX CV uses other files (icons, `.cls`, `.sty`), keep them **in the same folder** as the
`.tex` file: they are copied with it.

---

## Step 4 — Add the NopeCHA extension (optional)

The extension is not included in this repository (third-party code). Without it, the robot still works:
offers protected by a CAPTCHA simply end as `Action manuelle`.

1. Open the official releases page: **<https://github.com/NopeCHALLC/nopecha-extension/releases>**.
2. In release **0.6.1** (the version this project was built with) or a later one, download
   **`chromium_automation.zip`**.
3. Extract its **contents** into the project's `extensions\nopecha\` folder, so that this file exists:
   `extensions\nopecha\manifest.json`.
4. *(Optional)* Put your NopeCHA key in that `manifest.json`, in the `"nopecha"` block, field
   `"key"` (`FORMULAIRE.bat` → section 7 does it for you).
5. You will load the extension into each robot browser in [step 7](#step-7--log-in-to-the-robots-browsers).

**If you do not want NopeCHA:** in `config\config.yaml`, set `nopecha:` → `actif: false`.

---

## Step 5 — Replace the fictional data with yours

The project contains a complete **fictional** candidate. Every personal file is already filled with
fake data, so the structure is always valid: you replace values, never the structure. Choose **one**
of the two methods.

### Method A — With an AI assistant (recommended): Claude Code

1. Install [Claude Code](https://claude.com/claude-code) and open the project folder with it.
2. Send any first message (e.g. *"Bonjour"*). Claude reads `CLAUDE.md`, which tells it to follow
   [GUIDE_IA_DONNEES_PERSONNELLES.md](GUIDE_IA_DONNEES_PERSONNELLES.md).
3. Claude asks you questions **one at a time**, in this order:
   - **0. Your documents** — your photo, CV and cover letter (give their full path: in Windows Explorer,
     `Shift` + right-click on the file → *Copy as path*). Claude copies them to the right place and
     reads them to pre-fill the next answers;
   - **1–6.** Identity, links, education, goal (internship, apprenticeship, job, duration, start date),
     languages, standard answers to forms (work permit, visa, phone formats…);
   - **7–10.** CV content, cover letter, your "real facts" for DeepSeek, fallback CV and letter;
   - **11.** Target field and LinkedIn searches.
4. Before each section is written, Claude shows a summary and waits for your **"o"** (yes). Every
   file is backed up first in `config\sauvegardes_formulaire\`.
5. Claude checks that the CV and letter fit on one page and shows you a preview of your new CV.

Claude never asks for your passwords or keys: you enter them yourself in steps 6 and 7.

### Method B — With the built-in questionnaire: `FORMULAIRE.bat`

Double-click **`FORMULAIRE.bat`** and answer the questions (in French). Press `Enter` to skip an
optional question. Answers are saved as you go: you can close the window and resume later.

| # | Section | Writes to |
|---|---|---|
| 1 | Identity | `config\profile.yaml` |
| 2 | Links (LinkedIn, GitHub, portfolio) | `config\profile.yaml` |
| 3 | Education | `config\profile.yaml` |
| 4 | Goal (internship, apprenticeship, job, duration, start date) | `config\objectif.yaml`, `config\profile.yaml` |
| 5 | Languages and levels | `config\profile.yaml` |
| 6 | Standard answers to forms | `config\profile.yaml` |
| 7 | Site password, Apify keys, NopeCHA key | `.env`, `input\Scraping\.env`, `extensions\nopecha\manifest.json` |
| 8 | Robot browsers: LinkedIn, Gmail, DeepSeek logins, **checked automatically** | Chrome profiles in `%USERPROFILE%\.tappi\` |
| 9 | CV, letter, photo → robot template, with **previews to approve** | `config\cv_gabarit\`, `config\deepseek\nouveaux\`, `input\` |
| 10 | Target field and LinkedIn searches | `input\Scraping\criteres_domaine.json`, `liens.txt` |
| 11 | Final check | runs the robot's own verification |

Section 9 uses DeepSeek to turn your own CV and letter into the robot's templates, so you must do
section 8 first. To redo a single section later, run `FORMULAIRE.bat` again and type its number.
Answer details: [GUIDE_FORMULAIRE.md](GUIDE_FORMULAIRE.md) (French).

---

## Step 6 — Enter your secrets

Secrets never go into the chat with an AI assistant, and the `.env` files are ignored by Git.
`FORMULAIRE.bat` → section 7 asks for them interactively; or edit the files with Notepad:

| File | Key | What to put |
|---|---|---|
| `.env` | `MOT_DE_PASSE_SITES` | The password the robot uses to **create your candidate accounts** on company career sites. Choose a **new** password (8+ characters), different from your email password |
| `.env` | `MOT_DE_PASSE_SITES_2`, `_3` | Longer backup versions (about 16 and 20 characters), used when a site rejects the first one |
| `.env` | `OPENAI_API_KEY` (others) | Optional. Leave empty: the robot uses the DeepSeek web chat |
| `input\Scraping\.env` | `APIFY_TOKEN`, `APIFY_TOKEN_2` … `_10` | Your Apify token(s). Create this file by copying `input\Scraping\.env.example` |
| `extensions\nopecha\manifest.json` | `"nopecha"` → `"key"` | Optional NopeCHA key (see step 4) |

The robot never writes these values in its logs, and DeepSeek only ever sees `{MOT_DE_PASSE}`.

---

## Step 7 — Log in to the robot's browsers

The robot uses its **own** Chrome profiles, separate from your usual Chrome. You log in to each one
**once**; the sessions are kept. Open them with **`LANCER.bat` → 8 → browser number**
(`FORMULAIRE.bat` → section 8 does the same with automatic checks).

| # | Port | Chrome profile | Log in to | Role |
|---|---|---|---|---|
| 1 | 9222 | `%USERPROFILE%\.tappi\job-automation` | LinkedIn, Gmail, DeepSeek (account 1) | Main — **required** |
| 2 | 9223 | `…\.tappi\job-automation-2` | LinkedIn, Gmail, DeepSeek (account 2) | Backup |
| 3 | 9224 | `…\.tappi\job-automation-3` | LinkedIn, Gmail, DeepSeek (account 3) | Backup |
| 4 | 9225 | `…\.tappi\job-automation-4` | LinkedIn, Gmail, DeepSeek (account 4) | Backup |
| 5 | 9226 | `…\.tappi\job-automation-verif` | DeepSeek (account 5) **only** | Domain filter — **required** |

- In browsers 1–4, the Gmail account must be **your application email** (the one in your profile).
- **NopeCHA** (if you added it): in each browser, open `chrome://extensions`, turn on **Developer
  mode**, click **Load unpacked** and select the `extensions\nopecha` folder. The extension ID shown
  should be `dknlfmjaanfblgfdfebhijalfmhmjjjo`.
- **Browsers 2–4 are optional.** If you do not prepare one, remove it from the rotation: in
  `config\config.yaml`, under `navigateurs_secours`, put a `#` in front of its line
  (`FORMULAIRE.bat` → section 8 does it for you).

---

## Step 8 — Choose what to search for

- **Target field** (used to filter the scraped offers): `FORMULAIRE.bat` → section 10, or
  `input\Scraping\PARAMETRER_SCRAPING.bat`, or `LANCER.bat` → 13. It is stored in
  `input\Scraping\criteres_domaine.json` (degree, wanted field, include, exclude, mixed cases).
- **LinkedIn searches**: `input\Scraping\liens.txt`, one search URL per line. To get one, open
  <https://www.linkedin.com/jobs/search> in a **private window**, search with your keywords and
  location, and copy the address, for example:
  ```
  https://www.linkedin.com/jobs/search/?keywords=data%20scientist%20intern&location=France
  ```
  Use the `/jobs/search/` format. The robot adds the date filter itself.

---

## Step 9 — Check the installation

Run **`LANCER.bat` → 1**. The robot lists what is missing, or shows **"Configuration OK"**.

Expected warnings at this point:
- a missing site password → fill in `.env` (step 6);
- *"… n'apparaît pas tel quel dans ton CV original"* → a text listed in `config\config.yaml` →
  `garde_fous` → `chaines_obligatoires_cv` is not found word for word in your fallback CV: adjust the
  list, or set it to `[]`.

---

## Step 10 — Test without sending anything

1. **Add 2 or 3 offers** to the Excel file: `LANCER.bat` → **9** and paste offer links (or run the
   scraping, step 12).
2. **`LANCER.bat` → 2**: generates **only** the CV and letter for the first offer. Read the PDFs in
   `output\Candidatures\<Company>_<date>\`.
3. **`LANCER.bat` → 3**: fills in the forms of 3 offers **without submitting** (status
   `Prêt (test)`). Watch the Chrome window work, and look at the screenshots in the offer's
   `captures\` folder.
4. If everything is right, set those rows back to `À traiter` in the Excel file (`LANCER.bat` → 6).

---

## Step 11 — Start applying

- **`LANCER.bat` → 4**: real submissions, continuously. The robot processes the Excel file from top to
  bottom; when nothing is left, it checks again every 10 minutes and can start a scraping round by
  itself (at most every 2 hours).
- **`LANCER.bat` → 5**: same, but applies to **everything** (also permanent and fixed-term jobs and
  apprenticeships), even outside your goal, without asking.
- **To stop**: `Ctrl + C` in the robot's window, or close it. The current offer goes back to
  `À traiter`.

Close the Excel file while the robot runs: Windows prevents it from saving an open workbook (it waits
and retries).

---

## Step 12 — Find new offers automatically

- Double-click **`input\Scraping\LANCER_SCRAPING.bat`** and give the time of the daily run (e.g.
  `09:00`, or `Enter` for now). **Keep the window open**: it runs again every 24 hours.
- New offers arrive as `À vérifier`. A separate robot (`scripts\verify_eligibility.py`, browser 5)
  asks DeepSeek whether each offer matches your target field: matching offers become `À traiter`;
  the others go to the **`Écartées`** sheet with the reason. To filter right away: `LANCER.bat` → 12.

Settings (`input\Scraping\config_scraping.json`):

| Key | Role |
|---|---|
| `intervalle_minutes` | 1440 = one run per day |
| `offres_max_par_lien` | Maximum offers collected per search |
| `cout_max_par_passage_usd` | Maximum Apify spending per run |
| `candidats_max` | Skips offers that already have more than N applicants |
| `priorite_easy_apply` | Puts LinkedIn "Easy Apply" offers first |
| `verification_domaine` | Turns the automatic domain filter on or off |

---

## Every day

- In the Excel file, look at the rows marked **`Action manuelle`**: the `Commentaire` column says what
  blocked (image CAPTCHA, SMS code, question without an answer in your profile…). Fix it, then set the
  row back to `À traiter`.
- The **daily report** (20:00 by default) is saved in `rapports\` — `LANCER.bat` → 10 shows it.
- What the robot learned about each site: `LANCER.bat` → 11.

## Changing your information later

| Change | How |
|---|---|
| Phone, address, goal, standard answers… | `FORMULAIRE.bat` → section number, or ask Claude Code |
| New CV, letter or photo | `FORMULAIRE.bat` → 9, or ask Claude Code (section 0 of the guide) |
| New site password or keys | `FORMULAIRE.bat` → 7, or edit the `.env` files |
| Log in again on a site | `LANCER.bat` → 8 |
| Other field or other searches | `FORMULAIRE.bat` → 10, or `PARAMETRER_SCRAPING.bat` |

After any change, run `LANCER.bat` → 1.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `pdflatex introuvable` | MiKTeX is missing: run the installer again, then **reopen** the window |
| `identite.… est vide (lance FORMULAIRE.bat)` | Your profile is incomplete: step 5 |
| `Excel verrouillé` | Close the Excel file; the robot retries by itself |
| DeepSeek *"n'a pas commencé à répondre"* | The account is saturated: the robot switches browser. Add DeepSeek accounts (browsers 2–4) |
| The domain filter never runs | Browser 5 is not logged in to DeepSeek: `LANCER.bat` → 8 → 5, then → 12 |
| Many `Action manuelle` rows "connexion requise" | Log in to those sites once in the robot's browser 1 |
| `Champ obligatoire sans réponse dans le profil : X` | Add the answer in `config\profile.yaml` (`reponses_standard`), then set the row back to `À traiter` |
| *"Limite quotidienne Easy Apply"* | LinkedIn limits Easy Apply per day: the offer stays `À traiter` and is retried later |
| Accents look broken in the console | Cosmetic only; log files are UTF-8 |

Always look at: the `Commentaire` column, `logs\orchestrator.log` and the screenshots in
`output\Candidatures\<offer>\captures\`.

---

## Uninstall

1. Close the robot and its Chrome windows.
2. Delete the project folder.
3. Delete the robot's Chrome profiles: `%USERPROFILE%\.tappi\job-automation*` (they contain your
   logged-in sessions).
4. *(Optional)* Uninstall Python, MiKTeX or Chrome from Windows Settings → Apps.
