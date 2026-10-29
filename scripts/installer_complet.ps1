# =====================================================================
#  Installation COMPLETE sur une machine neuve — job-automation
#
#  Lancé par « Click_here_to_install_all_requirements.bat » (ne pas lancer
#  directement : le .bat demande les droits administrateur et fixe l'encodage).
#
#  Installe TOUT ce dont le projet a besoin, aux versions relevées sur la
#  machine d'origine :
#     Python 3.14.6 (64 bits) · Google Chrome · MiKTeX (pdflatex)
#     + les 105 paquets Python figés dans requirements-verrouille.txt
#
#  Idempotent : ce qui est déjà présent et à la bonne version est conservé.
# =====================================================================

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"   # sinon Invoke-WebRequest est très lent
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

# --- Versions de référence (machine d'origine) --------------------------------
$PY_SERIE   = "3.14"                 # série de Python attendue
$PY_VERSION = "3.14.6"               # version exacte installée si Python est absent
$PY_URL     = "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-amd64.exe"
$VERROU     = "requirements-verrouille.txt"

$etapes = 0
$echecs = @()
$avertissements = @()

function Titre($m) {
    $script:etapes++
    Write-Host ""
    Write-Host "──────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host " Étape $script:etapes — $m" -ForegroundColor Cyan
    Write-Host "──────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
}
function Ok($m)   { Write-Host "  [OK]   $m" -ForegroundColor Green }
function Info($m) { Write-Host "         $m" -ForegroundColor Gray }
function Warn($m) { Write-Host "  [!]    $m" -ForegroundColor Yellow; $script:avertissements += $m }
function Err($m)  { Write-Host "  [X]    $m" -ForegroundColor Red;    $script:echecs += $m }

function Winget-Dispo { [bool](Get-Command winget -ErrorAction SilentlyContinue) }

function Winget-Installer($id, $nom) {
    if (-not (Winget-Dispo)) { return $false }
    Info "Installation de $nom via winget (cela peut prendre plusieurs minutes)…"
    & winget install --id $id --exact --silent --accept-package-agreements `
        --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq -1978335189)   # -1978... = déjà installé
}

# =====================================================================
Titre "Python $PY_SERIE"
# =====================================================================
# On cherche l'exécutable par son CHEMIN, pas par le PATH : juste après une
# installation, le PATH de cette fenêtre n'est pas encore à jour.
function Trouver-Python {
    $pistes = @(
        "$env:LocalAppData\Programs\Python\Python314\python.exe",
        "$env:ProgramFiles\Python314\python.exe",
        "C:\Python314\python.exe"
    )
    foreach ($p in $pistes) { if (Test-Path $p) { return $p } }
    $lanceur = Get-Command py -ErrorAction SilentlyContinue
    if ($lanceur) {
        $chemin = & py -$PY_SERIE -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $chemin -and (Test-Path $chemin.Trim())) { return $chemin.Trim() }
    }
    return $null
}

$python = Trouver-Python
if ($python) {
    $v = (& $python -c "import sys; print(sys.version.split()[0])").Trim()
    Ok "Python $v déjà présent : $python"
} else {
    Info "Python $PY_SERIE absent — installation."
    $pose = Winget-Installer "Python.Python.3.14" "Python $PY_SERIE"
    $python = Trouver-Python
    if (-not $python) {
        Info "winget indisponible ou incomplet — téléchargement depuis python.org…"
        $tmp = Join-Path $env:TEMP "python-$PY_VERSION-amd64.exe"
        try {
            Invoke-WebRequest -Uri $PY_URL -OutFile $tmp -UseBasicParsing
            Info "Installation silencieuse de Python $PY_VERSION…"
            Start-Process -FilePath $tmp -ArgumentList @(
                "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1",
                "Include_pip=1", "Include_test=0") -Wait
            $python = Trouver-Python
        } catch { Err "Téléchargement de Python impossible : $_" }
    }
    if ($python) {
        $v = (& $python -c "import sys; print(sys.version.split()[0])").Trim()
        Ok "Python $v installé : $python"
    } else {
        Err "Python n'a pas pu être installé. Installe-le à la main : $PY_URL"
    }
}

# =====================================================================
Titre "Google Chrome"
# =====================================================================
# Chrome est indispensable : le robot le pilote (port de débogage 9222).
$chromes = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
             "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
             "$env:LocalAppData\Google\Chrome\Application\chrome.exe")
$chrome = $chromes | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) {
    Ok "Chrome déjà présent : $((Get-Item $chrome).VersionInfo.ProductVersion)"
} else {
    if (Winget-Installer "Google.Chrome" "Google Chrome") {
        $chrome = $chromes | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $chrome) {
        # winget absent (Windows 10 pas à jour) : installeur officiel autonome de Google.
        Info "winget indisponible ou en échec — téléchargement de Chrome depuis google.com…"
        $tmp = Join-Path $env:TEMP "ChromeStandaloneSetup64.exe"
        try {
            Invoke-WebRequest -UseBasicParsing -OutFile $tmp -Uri ("https://dl.google.com/tag/s/appguid%3D%7B8A69D345-D564-463C-AFF1-A69D9E530F96%7D" +
                "%26iid%3D%7B00000000-0000-0000-0000-000000000000%7D%26lang%3Dfr%26browser%3D4%26usagestats%3D0" +
                "%26appname%3DGoogle%2520Chrome%26needsadmin%3Dtrue%26ap%3Dx64-stable-statsdef_1%26installdataindex%3Dempty" +
                "/chrome/install/ChromeStandaloneSetup64.exe")
            Start-Process -FilePath $tmp -ArgumentList "/silent", "/install" -Wait
            $chrome = $chromes | Where-Object { Test-Path $_ } | Select-Object -First 1
        } catch { Info "Téléchargement de Chrome impossible : $_" }
    }
    if ($chrome) { Ok "Chrome installé." }
    else { Err "Chrome n'a pas pu être installé : https://www.google.com/chrome/" }
}

# =====================================================================
Titre "MiKTeX (pdflatex) — compilation du CV et de la lettre"
# =====================================================================
function Trouver-Pdflatex {
    $pistes = @(
        "$env:LocalAppData\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe",
        "$env:ProgramFiles\MiKTeX\miktex\bin\x64\pdflatex.exe",
        "C:\texlive\2025\bin\windows\pdflatex.exe"
    )
    foreach ($p in $pistes) { if (Test-Path $p) { return $p } }
    $c = Get-Command pdflatex -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

$pdflatex = Trouver-Pdflatex
if (-not $pdflatex) {
    Winget-Installer "MiKTeX.MiKTeX" "MiKTeX" | Out-Null
    $pdflatex = Trouver-Pdflatex
}
if (-not $pdflatex) {
    # winget absent : installeur « basic » officiel, dernière version publiée sur le CTAN.
    Info "winget indisponible ou en échec — téléchargement de MiKTeX depuis le CTAN…"
    try {
        $dossierCtan = "https://mirrors.ctan.org/systems/win32/miktex/setup/windows-x64/"
        $liste = (Invoke-WebRequest -UseBasicParsing -Uri $dossierCtan).Content
        $nom = [regex]::Matches($liste, 'basic-miktex-[0-9.]+-x64\.exe') | ForEach-Object { $_.Value } |
               Sort-Object { [version](($_ -replace 'basic-miktex-', '') -replace '-x64\.exe', '') } |
               Select-Object -Last 1
        if ($nom) {
            $tmp = Join-Path $env:TEMP $nom
            Invoke-WebRequest -UseBasicParsing -Uri ($dossierCtan + $nom) -OutFile $tmp
            Info "Installation silencieuse de $nom (plusieurs minutes)…"
            Start-Process -FilePath $tmp -ArgumentList "--unattended", "--auto-install=yes" -Wait
            $pdflatex = Trouver-Pdflatex
        } else { Info "Installeur MiKTeX introuvable sur le CTAN." }
    } catch { Info "Téléchargement de MiKTeX impossible : $_" }
}
if ($pdflatex) {
    Ok "pdflatex : $pdflatex"
    # Sans ceci, la première compilation s'arrête pour DEMANDER l'installation de chaque
    # paquet manquant (tikz, helvet…) et le robot reste bloqué sur une boîte de dialogue.
    $initexmf = Join-Path (Split-Path $pdflatex) "initexmf.exe"
    if (Test-Path $initexmf) {
        & $initexmf --set-config-value "[MPM]AutoInstall=1" 2>&1 | Out-Null
        Ok "MiKTeX configuré : installation automatique des paquets manquants."
    } else {
        Warn "initexmf introuvable : ouvre « MiKTeX Console » > Settings > « Always install missing packages » = Yes."
    }
} else {
    Err "MiKTeX n'a pas pu être installé — sans pdflatex, aucun CV ni lettre ne sera produit."
    Info "À installer à la main : https://miktex.org/download"
}

# =====================================================================
Titre "Environnement Python du projet (.venv)"
# =====================================================================
if (-not $python) {
    Err "Étape ignorée : Python est absent."
} else {
    # Un .venv venu d'une autre machine contient des chemins absolus inutilisables ici.
    if (Test-Path ".venv") {
        $ok = $false
        if (Test-Path ".venv\Scripts\python.exe") {
            & ".venv\Scripts\python.exe" -c "import sys" 2>$null
            $ok = ($LASTEXITCODE -eq 0)
        }
        if (-not $ok) {
            Info "Le dossier .venv existant ne fonctionne pas ici (copié d'une autre machine) — reconstruction."
            Remove-Item ".venv" -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Info "Création de l'environnement virtuel…"
        & $python -m venv .venv
    }
    $py = ".\.venv\Scripts\python.exe"
    if (Test-Path $py) {
        Ok "Environnement prêt : $((& $py -c 'import sys; print(sys.version.split()[0])').Trim())"
        Info "Mise à jour de pip…"
        & $py -m pip install --upgrade pip --quiet 2>&1 | Out-Null

        $source = if (Test-Path $VERROU) { $VERROU } else { "requirements.txt" }
        if ($source -eq $VERROU) {
            Info "Installation des paquets aux versions EXACTES de la machine d'origine ($VERROU)."
        } else {
            Warn "$VERROU absent — installation depuis requirements.txt (versions possiblement différentes)."
        }
        Info "Cela prend plusieurs minutes la première fois…"
        & $py -m pip install -r $source
        if ($LASTEXITCODE -eq 0) { Ok "Paquets Python installés." }
        else { Err "Échec de l'installation des paquets (voir les messages ci-dessus)." }
    } else {
        Err "L'environnement virtuel n'a pas pu être créé."
    }
}

# =====================================================================
Titre "Dossiers, Excel de suivi et fichier .env"
# =====================================================================
foreach ($d in @("input", "output\Candidatures", "logs", "config", "prompts", "memoire", "rapports")) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Ok "Dossiers de travail en place."

if (Test-Path ".venv\Scripts\python.exe") {
    & ".\.venv\Scripts\python.exe" scripts\excel_manager.py --init 2>&1 | Out-Null
    if (Test-Path "input\entreprises.xlsx") { Ok "Fichier Excel de suivi prêt." }
}
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") { Copy-Item ".env.example" ".env"; Ok ".env créé à partir de .env.example — à remplir." }
    else { Warn ".env absent et .env.example introuvable." }
} else {
    Ok ".env déjà présent."
}
foreach ($f in @("input\cv_original.tex", "input\lm_original.tex")) {
    if (-not (Test-Path $f)) { Warn "Manquant : $f (dépose ton CV / ta lettre en LaTeX)." }
}

# =====================================================================
Titre "Vérification finale"
# =====================================================================
if (Test-Path ".venv\Scripts\python.exe") {
    & ".\.venv\Scripts\python.exe" scripts\orchestrator.py --verifier
    if ($LASTEXITCODE -eq 0) { Ok "Le projet démarre correctement." }
    else { Warn "La vérification signale un point à corriger (souvent : clé API ou CV manquant)." }
} else {
    Err "Vérification impossible : l'environnement Python n'est pas installé."
}

# =====================================================================
Write-Host ""
Write-Host "══════════════════════════════════════════════════════════════" -ForegroundColor DarkGray
if ($echecs.Count -eq 0) {
    Write-Host " INSTALLATION TERMINÉE" -ForegroundColor Green
} else {
    Write-Host " INSTALLATION INCOMPLÈTE — $($echecs.Count) problème(s)" -ForegroundColor Red
    foreach ($e in $echecs) { Write-Host "   · $e" -ForegroundColor Red }
}
if ($avertissements.Count -gt 0) {
    Write-Host ""
    Write-Host " À FAIRE TOI-MÊME :" -ForegroundColor Yellow
    foreach ($a in $avertissements) { Write-Host "   · $a" -ForegroundColor Yellow }
}
Write-Host ""
if (-not (Test-Path "input\cv_original.tex")) {
    # Copie vierge (nouvel utilisateur) : tout se règle dans le formulaire, section par section.
    Write-Host " Ensuite : double-clique sur FORMULAIRE.bat (OBLIGATOIRE la première fois)" -ForegroundColor White
    Write-Host "   · ton identité, ton objectif (stage, alternance, emploi…), tes réponses types"
    Write-Host "   · ton mot de passe des sites et tes clés (Apify, NopeCHA)"
    Write-Host "   · tes navigateurs : connexions LinkedIn, Gmail et DeepSeek, vérifiées pour toi"
    Write-Host "   · ton CV, ta lettre et ta photo -> gabarit du robot (aperçu à valider)"
    Write-Host "   · ton domaine et tes recherches LinkedIn"
    Write-Host " Puis LANCER.bat -> entrée 4 pour démarrer les candidatures."
} else {
Write-Host " Ensuite : double-clique sur LANCER.bat" -ForegroundColor White
Write-Host "   · entrée 1 pour vérifier l'installation"
Write-Host "   · entrée 8 pour ouvrir le Chrome du robot et t'y connecter à LinkedIn"
Write-Host ""
Write-Host " À faire UNE FOIS dans CHAQUE navigateur du robot (entrée 8, navigateurs 1 à 5) :" -ForegroundColor White
Write-Host "   · te connecter : LinkedIn, Google (Gmail), DeepSeek"
Write-Host "   · charger NopeCHA : chrome://extensions > Mode développeur >"
Write-Host "     « Charger l'extension non empaquetée » > $((Get-Location).Path)\extensions\nopecha"
Write-Host "     (la clé NopeCHA est déjà dans extensions\nopecha\manifest.json)"
Write-Host ""
Write-Host " Scraping Apify (facultatif) : input\Scraping\LANCER_SCRAPING.bat"
Write-Host "   · clé dans input\Scraping\.env, liens dans input\Scraping\liens.txt"
}
Write-Host "══════════════════════════════════════════════════════════════" -ForegroundColor DarkGray
Write-Host ""
