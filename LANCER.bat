@echo off
rem =====================================================================
rem  Menu de lancement — double-clique sur ce fichier.
rem =====================================================================
chcp 65001 >nul
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
set PYTHONIOENCODING=utf-8

if not exist "%PY%" (
    echo L'environnement Python n'est pas installe. Lancement de install.ps1...
    powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
)

:menu
cls
echo ==========================================================
echo           CANDIDATURES AUTOMATIQUES - STAGE PFE
echo ==========================================================
echo.
echo   1. Verifier l'installation et la configuration
echo   2. Generer seulement CV + lettre pour la 1re offre (aucun envoi)
echo   3. TEST : remplir les formulaires de 3 offres SANS envoyer
echo   4. LANCER les candidatures (envoi reel, tourne en continu)
echo   5. LANCER SANS RIEN DEMANDER - postule a TOUT : CDI, CDD, alternance
echo.
echo   6. Ouvrir le fichier Excel des offres
echo   7. Ouvrir le dossier des CV / lettres generes
echo   8. Ouvrir un navigateur du robot (1 principal, 2 a 4 de secours)
echo   9. Coller des liens d'offres (les ajoute dans l'Excel)
echo  10. Voir le rapport du jour (envoyees, erreurs, actions a faire par toi)
echo  11. Voir ce que le robot a appris sur chaque site
echo  12. Trier maintenant les offres "A verifier" (domaine, par DeepSeek)
echo  13. Regler le domaine recherche (tri des offres scrapees)
echo.
echo   Q. Quitter
echo.
set /p choix="Ton choix : "

if /i "%choix%"=="1" ( "%PY%" scripts\orchestrator.py --verifier & pause & goto menu )
if /i "%choix%"=="2" ( "%PY%" scripts\orchestrator.py --documents-seulement --limite 1 & start "" "output\Candidatures" & pause & goto menu )
if /i "%choix%"=="3" ( "%PY%" scripts\orchestrator.py --test --limite 3 --once & pause & goto menu )
if /i "%choix%"=="4" (
    echo.
    echo Les candidatures vont etre ENVOYEES. Ferme cette fenetre ou Ctrl+C pour arreter.
    pause
    "%PY%" scripts\orchestrator.py
    pause
    goto menu
)
if /i "%choix%"=="5" (
    echo.
    echo  ATTENTION - Mode SANS ARRET.
    echo  Le robot postule a TOUTES les offres, y compris les CDI, les CDD et les
    echo  alternances qui ne correspondent PAS a ton stage PFE de 6 mois.
    echo  Il ne te demandera plus ton avis : les candidatures partent directement.
    echo  Il se connecte, cree le compte et confirme l'email lui-meme. Seuls un
    echo  CAPTCHA a images, un code SMS ou une inscription impossible l'arreteront
    echo  L'offre est alors notee Action manuelle, puis offre suivante.
    echo.
    echo  Ferme cette fenetre ou Ctrl+C pour annuler.
    pause
    "%PY%" scripts\orchestrator.py --tout-postuler
    pause
    goto menu
)
if /i "%choix%"=="6" ( start "" "input\entreprises.xlsx" & goto menu )
if /i "%choix%"=="7" ( start "" "output\Candidatures" & goto menu )
if /i "%choix%"=="8" goto navigateurs
if /i "%choix%"=="9" ( "%PY%" scripts\ajouter_liens.py & pause & goto menu )
if /i "%choix%"=="10" ( "%PY%" scripts\orchestrator.py --rapport & pause & goto menu )
if /i "%choix%"=="11" (
    if exist "memoire\lecons_sites.json" ( start "" notepad "memoire\lecons_sites.json" ) else ( echo Le robot n'a encore rien appris. & pause )
    goto menu
)
if /i "%choix%"=="12" ( "%PY%" scripts\verify_eligibility.py & pause & goto menu )
if /i "%choix%"=="13" ( "%PY%" input\Scraping\parametrer_domaine.py & pause & goto menu )
if /i "%choix%"=="q" exit /b
goto menu

rem =====================================================================
rem  Ouvrir un des navigateurs du robot, pour s'y connecter aux sites.
rem  Chacun a son PROPRE dossier de profil et son PROPRE port : c'est ce
rem  qui permet au robot de basculer de l'un a l'autre quand un compte
rem  DeepSeek est sature. Les ports et les profils doivent correspondre a
rem  navigateur.navigateurs_secours dans config\config.yaml.
rem =====================================================================
:navigateurs
cls
echo ==========================================================
echo        OUVRIR UN NAVIGATEUR DU ROBOT
echo ==========================================================
echo.
echo  Dans CHAQUE navigateur, connecte-toi a :
echo     - LinkedIn et les autres sites d'emploi
echo     - Google : omarelamrano@gmail.com   (c'est lui qui postule)
echo     - DeepSeek : un compte DIFFERENT dans chaque navigateur
echo.
echo   1. Navigateur 1 - principal   (port 9222)
echo   2. Navigateur 2 - secours     (port 9223)
echo   3. Navigateur 3 - secours     (port 9224)
echo   4. Navigateur 4 - secours     (port 9225)
echo   5. Navigateur 5 - VERIFICATION du domaine (port 9226) : DeepSeek SEULEMENT
echo      (un compte DeepSeek different des 4 autres, si possible)
echo.
echo   R. Retour au menu
echo.
set nav=
set /p nav="Quel navigateur ouvrir : "
if /i "%nav%"=="r" goto menu
if "%nav%"=="1" ( set PORT=9222& set DOSSIER=job-automation& goto lancer_chrome )
if "%nav%"=="2" ( set PORT=9223& set DOSSIER=job-automation-2& goto lancer_chrome )
if "%nav%"=="3" ( set PORT=9224& set DOSSIER=job-automation-3& goto lancer_chrome )
if "%nav%"=="4" ( set PORT=9225& set DOSSIER=job-automation-4& goto lancer_chrome )
if "%nav%"=="5" ( set PORT=9226& set DOSSIER=job-automation-verif& goto lancer_chrome )
goto navigateurs

:lancer_chrome
rem Chrome peut etre installe a trois endroits selon la machine.
set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" (
    echo.
    echo  Chrome est introuvable. Installe-le, ou lance
    echo  Click_here_to_install_all_requirements.bat
    echo.
    pause
    goto navigateurs
)
echo.
echo  Ouverture du navigateur %nav% ^(port %PORT%^)...
echo  Profil : %USERPROFILE%\.tappi\%DOSSIER%
echo.
echo  Connecte-toi aux sites dans la fenetre qui s'ouvre, puis reviens ici.
echo  Laisse cette fenetre Chrome OUVERTE tant que tu t'y connectes.
echo.
echo  NopeCHA : si l'extension n'apparait pas, chrome://extensions ^> Mode developpeur
echo  ^> Charger l'extension non empaquetee ^> %~dp0extensions\nopecha
echo.
start "" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%USERPROFILE%\.tappi\%DOSSIER%" --load-extension="%~dp0extensions\nopecha" --disable-features=DisableLoadExtensionCommandLineSwitch
timeout /t 3 >nul
goto navigateurs
