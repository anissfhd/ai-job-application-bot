# =====================================================================
#  Installation Windows — job-automation
#  Lancer depuis le dossier du projet, dans PowerShell :
#      powershell -ExecutionPolicy Bypass -File .\install.ps1
# =====================================================================

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Ok($m)   { Write-Host "[OK]    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!]     $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "[ERREUR] $m" -ForegroundColor Red }

# --- 1. Trouver un Python >= 3.10 (on privilégie le lanceur « py ») -----------
$pythonCmd = $null
foreach ($candidat in @("py -3", "python", "python3")) {
    $morceaux = $candidat.Split(" ")
    $exe = $morceaux[0]
    $opts = @($morceaux | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $v = & $exe @opts -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $parts = "$v".Trim().Split(".")
            if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) {
                $pythonCmd = @{ exe = $exe; opts = $opts }
                Ok "Python $v trouvé ($candidat)"
                break
            }
        }
    } catch { }
}
if (-not $pythonCmd) {
    Err "Python 3.10+ introuvable. Installe-le : winget install Python.Python.3.12  (puis rouvre PowerShell)"
    exit 1
}

# --- 2. Environnement virtuel + dépendances ------------------------------------
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Création de l'environnement virtuel .venv ..."
    $opts = $pythonCmd.opts
    & $pythonCmd.exe @opts -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"
Write-Host "Installation des dépendances (quelques minutes) ..."
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { Err "Échec de pip install."; exit 1 }
Ok "Dépendances Python installées dans .venv"

# --- 3. LaTeX -------------------------------------------------------------------
if (Get-Command pdflatex -ErrorAction SilentlyContinue) {
    Ok "pdflatex trouvé : $((Get-Command pdflatex).Source)"
} else {
    Warn "pdflatex introuvable. Installe MiKTeX :"
    Warn "    winget install MiKTeX.MiKTeX"
    Warn "  puis ouvre « MiKTeX Console » > Settings > « Always install missing packages » = Yes,"
    Warn "  et rouvre PowerShell."
}

# --- 4. Chrome ------------------------------------------------------------------
$chromes = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
             "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
             "$env:LocalAppData\Google\Chrome\Application\chrome.exe")
if ($chromes | Where-Object { Test-Path $_ }) { Ok "Google Chrome trouvé" }
else { Warn "Google Chrome introuvable : installe-le (winget install Google.Chrome)" }

# --- 5. Dossiers et fichiers modèles ------------------------------------------
foreach ($d in @("input", "output\Candidatures", "logs", "config", "prompts")) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
foreach ($f in @("output\Candidatures\.gitkeep", "logs\.gitkeep")) {
    if (-not (Test-Path $f)) { New-Item -ItemType File -Path $f | Out-Null }
}
& $py scripts\excel_manager.py --init
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Ok "Fichier .env créé (à remplir)" }

foreach ($f in @("input\cv_original.tex", "input\lm_original.tex")) {
    if (-not (Test-Path $f)) { Warn "À faire : dépose ton fichier $f" }
}

Write-Host ""
Ok "Installation terminée."
Write-Host "Étapes suivantes :"
Write-Host "  1. Remplis config\config.yaml (clé API) et config\profile.yaml (tes infos)"
Write-Host "  2. Dépose input\cv_original.tex et input\lm_original.tex"
Write-Host "  3. Vérifie :   .\.venv\Scripts\python.exe scripts\orchestrator.py --verifier"
Write-Host "  4. Lance :     .\.venv\Scripts\python.exe scripts\orchestrator.py"
