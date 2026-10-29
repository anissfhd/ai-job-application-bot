"""
FORMULAIRE — section « Navigateurs » : prépare les Chrome du robot et VÉRIFIE les connexions.

Le robot ne saisit jamais un mot de passe de LinkedIn, Google ou DeepSeek : il réutilise les
sessions déjà ouvertes dans ses navigateurs (un dossier de profil Chrome chacun). Cette section
ouvre chaque navigateur sur les bonnes pages ; le candidat s'y connecte lui-même, puis le
programme contrôle, page par page, que la connexion a bien réussi.

  Navigateur 1 (port 9222) : LinkedIn + Gmail (compte de candidature) + DeepSeek   — obligatoire
  Navigateurs 2 à 4        : LinkedIn + Gmail + un AUTRE compte DeepSeek            — recommandés
  Navigateur 5 (port 9226) : DeepSeek seulement (tri du domaine des offres)         — obligatoire
Un navigateur 2 à 4 non préparé est retiré de la rotation (config.yaml), jamais laissé à moitié.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from utils import charger_config

RACINE = Path(__file__).resolve().parent.parent
CONFIG = RACINE / "config" / "config.yaml"
NOPECHA = RACINE / "extensions" / "nopecha"
PAGES = {"linkedin": "https://www.linkedin.com/login",
         "gmail": "https://mail.google.com/mail/",   # + authuser=<email de candidature> (voir ouvrir_pages)
         "deepseek": "https://chat.deepseek.com/"}


def trouver_chrome() -> str:
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("LocalAppData", "")):
        p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and p.exists():
            return str(p)
    raise FileNotFoundError("Chrome introuvable : lance Click_here_to_install_all_requirements.bat")


def navigateurs() -> list[dict]:
    config, _ = charger_config()
    nav = config.get("navigateur", {}) or {}
    liste = [{"numero": 1, "port": 9222, "profil": nav.get("profil_chrome", "~/.tappi/job-automation"),
              "sites": ["linkedin", "gmail", "deepseek"], "obligatoire": True}]
    for i, s in enumerate(nav.get("navigateurs_secours") or [], start=2):
        liste.append({"numero": i, "port": int(s["port"]), "profil": s["profil"],
                      "sites": ["linkedin", "gmail", "deepseek"], "obligatoire": False})
    liste.append({"numero": 5, "port": 9226, "profil": "~/.tappi/job-automation-verif",
                  "sites": ["deepseek"], "obligatoire": True})
    return liste


def _repond(port: int) -> bool:
    try:
        urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def lancer(n: dict) -> None:
    if _repond(n["port"]):
        return
    profil = str(Path(os.path.expanduser(n["profil"])))
    subprocess.Popen([trouver_chrome(), f"--remote-debugging-port={n['port']}", f"--user-data-dir={profil}",
                      f"--load-extension={NOPECHA}", "--disable-features=DisableLoadExtensionCommandLineSwitch",
                      "--start-maximized", "--no-first-run"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fin = time.monotonic() + 20
    while time.monotonic() < fin and not _repond(n["port"]):
        time.sleep(0.5)
    if not _repond(n["port"]):
        raise RuntimeError(f"le navigateur {n['numero']} ne démarre pas (port {n['port']})")


def _onglets(port: int) -> list[dict]:
    return [t for t in json.load(urlopen(f"http://127.0.0.1:{port}/json", timeout=5)) if t.get("type") == "page"]


def ouvrir_pages(n: dict) -> None:
    from tappi import Browser
    b = Browser(f"http://127.0.0.1:{n['port']}")
    for site in n["sites"]:
        b.newtab(PAGES[site])
        time.sleep(0.5)


def _lire(port: int, url: str, js: str, attente: float = 6.0):
    """Ouvre `url` dans un onglet neuf, attend, évalue `js`, referme l'onglet. (url finale, valeur)"""
    from tappi import Browser
    b = Browser(f"http://127.0.0.1:{port}")
    avant = {t["id"] for t in _onglets(port)}
    b.newtab(url)
    time.sleep(attente)
    nouvel = next((t for t in _onglets(port) if t["id"] not in avant), None)
    if nouvel is None:
        return "", None
    page = b._connect_page(nouvel["id"])
    try:
        r = page.send("Runtime.evaluate", expression=js, returnByValue=True)
        valeur = r.get("result", {}).get("value")
        url_finale = page.send("Runtime.evaluate", expression="location.href", returnByValue=True)["result"]["value"]
    finally:
        page.close()
        try:
            urlopen(f"http://127.0.0.1:{port}/json/close/{nouvel['id']}", timeout=5).read()
        except Exception:  # noqa: BLE001
            pass
    return str(url_finale or ""), valeur


def verifier(n: dict, email: str) -> dict[str, str]:
    """{site: "" si connecté, sinon le problème en clair}."""
    res = {}
    if "linkedin" in n["sites"]:
        url, _ = _lire(n["port"], "https://www.linkedin.com/feed/", "document.title")
        res["LinkedIn"] = "" if "/feed" in url and not re.search(r"login|authwall|checkpoint|signup", url) \
            else "pas connecté (connecte-toi sur linkedin.com)"
    if "gmail" in n["sites"]:
        # La boîte de l'email de CANDIDATURE (authuser), jamais « le premier compte » : si ce
        # compte n'est pas connecté dans ce navigateur, Google ne l'ouvre pas et on le signale.
        from candidature_gmail import url_gmail_du_compte
        url, titre = _lire(n["port"], url_gmail_du_compte("https://mail.google.com/mail/", email),
                           "document.title", 8.0)
        if "mail.google.com/mail" not in url:
            res["Gmail"] = "pas connecté (connecte-toi à Google sur mail.google.com)"
        elif email and email.lower() not in str(titre or "").lower():
            res["Gmail"] = (f"connecté, mais PAS avec {email} (titre : « {str(titre)[:60]} »). "
                            f"Ajoute ce compte Google dans ce navigateur (l'ordre des comptes n'a pas d'importance).")
        else:
            res["Gmail"] = ""
    if "deepseek" in n["sites"]:
        url, zone = _lire(n["port"], "https://chat.deepseek.com/",
                          "!!document.querySelector('textarea, [contenteditable=true]')", 7.0)
        res["DeepSeek"] = "" if zone and not re.search(r"sign_in|login", url) \
            else "pas connecté (connecte-toi sur chat.deepseek.com)"
    return res


def nopecha_ok(n: dict) -> bool:
    try:
        prefs = json.loads((Path(os.path.expanduser(n["profil"])) / "Default" / "Secure Preferences")
                           .read_text(encoding="utf-8"))
        exts = prefs.get("extensions", {}).get("settings", {})
        return any("nopecha" in str(v.get("path", "")).lower() and v.get("state", 1) != 0 for v in exts.values())
    except Exception:  # noqa: BLE001
        return False


def retirer_de_la_rotation(ports: list[int]) -> None:
    """Commente dans config.yaml les navigateurs de secours non préparés."""
    texte = CONFIG.read_text(encoding="utf-8")
    for port in ports:
        texte = re.sub(rf"^(    - \{{profil: [^}}]*port: {port}\}}.*)$", r"#\1   (non préparé : retiré par FORMULAIRE)",
                       texte, flags=re.M)
    import formulaire_modeles as M
    M.ecrire(CONFIG, texte)


def section(r: dict) -> None:
    email = r.get("p.identite.email", "")
    print("\n  Le robot utilise des navigateurs Chrome dédiés, où TU te connectes une fois pour toutes.")
    print("  Il ne saisit jamais tes mots de passe LinkedIn / Google / DeepSeek.")
    print(f"  Compte Google à utiliser : {email} (l'ordre des comptes n'a pas d'importance : le robot")
    print("  choisit toujours ce compte par son adresse, et DeepSeek ne la voit jamais).")
    print("  DeepSeek : un compte DIFFÉRENT par navigateur, pour ne jamais saturer.")
    retires = []
    for n in navigateurs():
        print("\n  " + "-" * 64)
        role = "tri du domaine des offres — DeepSeek seulement" if n["numero"] == 5 else \
               ("principal" if n["numero"] == 1 else "secours (rotation quand DeepSeek sature)")
        print(f"  NAVIGATEUR {n['numero']} — {role}{'' if n['obligatoire'] else ' — recommandé'}")
        if not n["obligatoire"]:
            if input("  Préparer ce navigateur ? (O/n) > ").strip().lower() in ("n", "non"):
                retires.append(n["port"])
                print("  -> non préparé : il sera retiré de la rotation.")
                continue
        lancer(n)
        ouvrir_pages(n)
        sites = ", ".join({"linkedin": "LinkedIn", "gmail": "Gmail", "deepseek": "DeepSeek"}[s] for s in n["sites"])
        print(f"  Une fenêtre Chrome est ouverte : connecte-toi à {sites}.")
        if not nopecha_ok(n):
            print("  Extension NopeCHA : chrome://extensions -> Mode développeur -> « Charger l'extension non")
            print(f"  empaquetée » -> {NOPECHA}")
        while True:
            input("  Quand c'est fait, appuie sur Entrée pour que je vérifie… ")
            etats = verifier(n, email)
            for site, pb in etats.items():
                print(f"     {'✓' if not pb else '✗'} {site}{' : ' + pb if pb else ' : connecté'}")
            nope = nopecha_ok(n)
            print(f"     {'✓' if nope else '✗'} NopeCHA{'' if nope else ' : extension non chargée (voir ci-dessus)'}")
            if all(not pb for pb in etats.values()) and (nope or n["numero"] == 5):
                print(f"  ✓ Navigateur {n['numero']} prêt.")
                break
            if not n["obligatoire"] and input("  Réessayer (Entrée) ou P pour passer ce navigateur > ").strip().lower() == "p":
                retires.append(n["port"])
                break
    if retires:
        retirer_de_la_rotation(retires)
        print(f"\n  Navigateurs retirés de la rotation : ports {retires} (tu pourras les ajouter plus tard).")
