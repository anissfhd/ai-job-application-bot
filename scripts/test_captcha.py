"""
TEST 2CAPTCHA — outil ISOLÉ, réservé à TON PROPRE SITE.

Sert à vérifier le fonctionnement de l'API 2Captcha (https://2captcha.com) sur une
page dont TU es propriétaire : le script détecte le CAPTCHA, demande sa résolution
au service, injecte le jeton dans la page, soumet si demandé, et mesure le temps
de résolution et le taux de réussite.

⚠️  Ce module n'est PAS utilisé par le robot de candidatures : orchestrator.py et
    candidature.py ne l'importent jamais. GARDE-FOU explicite : toute URL dont le
    domaine n'est pas listé dans config.yaml -> captcha_test.domaines_autorises est
    REFUSÉE (liste vide = le script ne tourne pas).

Utilisation :
    python scripts/test_captcha.py --url https://mon-site.com/contact --type recaptcha_v2
    python scripts/test_captcha.py --url https://mon-site.com/contact --essais 5 --soumettre

Types gérés : recaptcha_v2 (défaut) | hcaptcha | turnstile.
Clé API   : TWOCAPTCHA_API_KEY dans .env, ou captcha_test.cle_api dans config.yaml.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from navigateur import Navigateur  # noqa: E402
from utils import charger_config, chemin_projet, configurer_logs  # noqa: E402

API_IN = "https://2captcha.com/in.php"
API_RES = "https://2captcha.com/res.php"
# method= attendu par 2Captcha pour chaque type de CAPTCHA
TYPE_VERS_METHODE = {"recaptcha_v2": "userrecaptcha", "hcaptcha": "hcaptcha", "turnstile": "turnstile"}
# Prix public 2Captcha par 1000 résolutions (voir https://2captcha.com/pricing)
PRIX_POUR_1000 = {"recaptcha_v2": (1.00, 2.99), "hcaptcha": (2.99, 2.99), "turnstile": (1.45, 1.45)}


class ErreurCaptcha(Exception):
    """Impossible de détecter ou de résoudre le CAPTCHA."""


# --- JS : trouver la clé du site (sitekey) ---------------------------------

JS_SITEKEY = r"""
(() => {
  for (const el of document.querySelectorAll('[data-sitekey], .g-recaptcha, .h-captcha, .cf-turnstile')) {
    const k = el.getAttribute('data-sitekey');
    if (k) return k;
  }
  for (const f of document.querySelectorAll('iframe')) {
    const src = f.src || '';
    if (!/recaptcha|hcaptcha|challenges\.cloudflare/i.test(src)) continue;
    const m = src.match(/[?&]k=([^&]+)/) || src.match(/sitekey=([^&]+)/);
    if (m) return decodeURIComponent(m[1]);
  }
  return '';
})()
"""

# --- JS : écrire le jeton résolu dans la page ------------------------------

JS_INJECTER = r"""
((jeton, type) => {
  const poser = (nom) => {
    const el = document.querySelector('textarea[name="' + nom + '"], input[name="' + nom + '"], #' + nom);
    if (!el) return false;
    if ('value' in el) { el.value = jeton; } else { el.textContent = jeton; }
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  };
  let ok = false;
  if (type === 'recaptcha_v2') {
    ok = poser('g-recaptcha-response');
    const boite = document.querySelector('.g-recaptcha');
    const cb = boite && boite.getAttribute('data-callback');
    if (cb && typeof window[cb] === 'function') { try { window[cb](jeton); } catch (e) {} }
  } else if (type === 'hcaptcha') {
    ok = poser('h-captcha-response') || poser('g-recaptcha-response');
  } else {
    ok = poser('cf-turnstile-response') || poser('g-recaptcha-response');
  }
  return ok;
})
"""

# --- JS : cliquer le bouton d'envoi du formulaire --------------------------

JS_SOUMETTRE = r"""
(() => {
  const mots = /envoyer|valider|soumettre|submit|send|rechercher|continue|continuer/i;
  for (const e of document.querySelectorAll('button, input[type=submit], [role=button]')) {
    const r = e.getBoundingClientRect();
    const t = (e.innerText || e.value || '').trim();
    if (r.width > 10 && r.height > 10 && t && mots.test(t)) { e.click(); return 'clic : ' + t.slice(0, 40); }
  }
  return 'aucun bouton trouvé';
})()
"""


# --- API 2Captcha ----------------------------------------------------------

def _requete(url: str, donnees: dict | None = None, timeout: int = 30) -> dict:
    """Appel HTTP simple (urllib, aucune dépendance externe). Renvoie le JSON décodé."""
    if donnees is None:
        requete = urllib.request.Request(url)
    else:
        corps = urllib.parse.urlencode(donnees).encode("utf-8")
        requete = urllib.request.Request(url, data=corps, method="POST")
    with urllib.request.urlopen(requete, timeout=timeout) as reponse:
        return json.loads(reponse.read().decode("utf-8"))


def cle_api(config: dict) -> str:
    """Clé 2Captcha : .env (TWOCAPTCHA_API_KEY) prioritaire, sinon config.yaml."""
    import os
    return (os.environ.get("TWOCAPTCHA_API_KEY") or config.get("captcha_test", {}).get("cle_api") or "").strip()


def domaine_autorise(url: str, autorises: list[str]) -> bool:
    """Vrai si le domaine de l'URL est dans la liste blanche (garde-fou anti-mauvais usage)."""
    hote = (urlparse(url).hostname or "").lower()
    return any(hote == d.lower() or hote.endswith("." + d.lower()) for d in autorises)


def extraire_sitekey(nav: Navigateur) -> str:
    try:
        return str(nav.b.eval(JS_SITEKEY) or "").strip()
    except Exception as e:  # noqa: BLE001
        raise ErreurCaptcha(f"Lecture de la page impossible : {e}") from e


def resoudre_2captcha(cle: str, type_captcha: str, sitekey: str, pageurl: str,
                      timeout: int, pause: int, logger) -> tuple[str, float]:
    """Demande la résolution à 2Captcha et attend le jeton. Renvoie (jeton, durée en secondes)."""
    params = {"key": cle, "method": TYPE_VERS_METHODE[type_captcha], "pageurl": pageurl, "json": 1,
              "googlekey" if type_captcha == "recaptcha_v2" else "sitekey": sitekey}
    debut = time.monotonic()
    reponse = _requete(API_IN, params)
    if reponse.get("status") != 1:
        raise ErreurCaptcha(f"2Captcha a refusé la tâche : {reponse.get('request')}")
    identifiant = reponse["request"]
    logger.info(f"2Captcha : tâche {identifiant} créée (sitekey {sitekey[:18]}…) — attente du jeton.")
    while time.monotonic() - debut < timeout:
        time.sleep(pause)
        reponse = _requete(API_RES, {"key": cle, "action": "get", "id": identifiant, "json": 1})
        if reponse.get("status") == 1:
            return str(reponse["request"]), time.monotonic() - debut
        if reponse.get("request") != "CAPCHA_NOT_READY":
            raise ErreurCaptcha(f"2Captcha : {reponse.get('request')}")
    raise ErreurCaptcha(f"2Captcha : aucun jeton après {timeout} s (timeout)")


def injecter_jeton(nav: Navigateur, type_captcha: str, jeton: str) -> bool:
    """Écrit le jeton dans le champ caché du CAPTCHA (et déclenche le callback éventuel)."""
    try:
        return bool(nav.b.eval(JS_INJECTER + f"({json.dumps(jeton)}, {json.dumps(type_captcha)})"))
    except Exception as e:  # noqa: BLE001
        raise ErreurCaptcha(f"Injection du jeton impossible : {e}") from e


def soumettre(nav: Navigateur) -> str:
    try:
        return str(nav.b.eval(JS_SOUMETTRE))
    except Exception:  # noqa: BLE001
        return "soumission impossible (page changée ?)"


# --- Programme principal ---------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Test de l'API 2Captcha sur TON PROPRE SITE")
    parser.add_argument("--url", required=True, help="URL de TON site contenant le CAPTCHA")
    parser.add_argument("--type", default="recaptcha_v2", choices=sorted(TYPE_VERS_METHODE),
                        help="Type de CAPTCHA présent sur la page (défaut : recaptcha_v2)")
    parser.add_argument("--essais", type=int, default=1, help="Nombre de résolutions chronométrées")
    parser.add_argument("--soumettre", action="store_true", help="Soumettre aussi le formulaire après injection")
    args = parser.parse_args()

    config, _ = charger_config()
    logger = configurer_logs(chemin_projet(config["chemins"]["logs"]), nom="test_captcha")
    cfg = config.get("captcha_test", {}) or {}
    autorises = [str(d) for d in (cfg.get("domaines_autorises") or [])]

    # GARDE-FOU : uniquement les domaines de ta liste blanche (ton propre site).
    if not autorises:
        logger.error("Refus : aucun domaine dans captcha_test.domaines_autorises (config.yaml). "
                     "Ajoute TON site, ex. domaines_autorises: [\"mon-site.com\"].")
        return 2
    if not domaine_autorise(args.url, autorises):
        logger.error(f"Refus : « {args.url} » n'appartient pas à ta liste blanche {autorises}. "
                     f"Cet outil ne sert QUE sur ton propre site.")
        return 2

    cle = cle_api(config)
    if not cle:
        logger.error("Clé API manquante : mets TWOCAPTCHA_API_KEY dans .env (ou captcha_test.cle_api).")
        return 2

    timeout = int(cfg.get("timeout_secondes", 180))
    pause = max(2, int(cfg.get("pause_verification_secondes", 5)))
    prix_min, prix_max = PRIX_POUR_1000[args.type]

    nav = Navigateur(config, logger)
    nav.demarrer()
    resultats: list[tuple[bool, float, str]] = []
    for essai in range(1, max(1, args.essais) + 1):
        logger.info(f"--- Essai {essai}/{args.essais} : {args.url} ({args.type}) ---")
        try:
            nav.aller(args.url)                       # recharge : nouveau CAPTCHA à chaque essai
            sitekey = extraire_sitekey(nav)
            if not sitekey:
                raise ErreurCaptcha("Aucun CAPTCHA détecté sur la page (sitekey introuvable).")
            logger.info(f"CAPTCHA détecté (sitekey {sitekey[:20]}…)")
            jeton, duree = resoudre_2captcha(cle, args.type, sitekey, args.url, timeout, pause, logger)
            logger.info(f"Jeton reçu en {duree:.1f} s ({len(jeton)} caractères).")
            if not injecter_jeton(nav, args.type, jeton):
                raise ErreurCaptcha("Jeton reçu mais champ du CAPTCHA introuvable dans la page.")
            logger.info("Jeton injecté dans la page.")
            detail = ""
            if args.soumettre:
                detail = soumettre(nav)
                logger.info(f"Soumission : {detail}")
            resultats.append((True, duree, detail))
        except ErreurCaptcha as e:
            logger.error(f"Échec : {e}")
            resultats.append((False, 0.0, str(e)))
        except Exception as e:  # noqa: BLE001 — on continue les essais suivants
            logger.error(f"Échec inattendu : {e}")
            resultats.append((False, 0.0, f"{type(e).__name__}: {e}"))
        time.sleep(2)

    # --- Bilan chiffré ------------------------------------------------------
    reussis = [r for r in resultats if r[0]]
    durees = [r[1] for r in reussis]
    bilan = [
        f"TEST 2CAPTCHA — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"URL : {args.url}   |   type : {args.type}",
        f"Résolutions réussies : {len(reussis)}/{len(resultats)} "
        f"({round(100 * len(reussis) / max(1, len(resultats)))} %)",
    ]
    if durees:
        cout_min = prix_min * len(reussis) / 1000
        cout_max = prix_max * len(reussis) / 1000
        bilan += [
            f"Temps de résolution : min {min(durees):.1f} s | moyen {sum(durees) / len(durees):.1f} s "
            f"| max {max(durees):.1f} s",
            f"Coût de ces essais : ${cout_min:.4f} – ${cout_max:.4f} "
            f"(tarif 2Captcha ${prix_min:.2f}–${prix_max:.2f} / 1000)",
        ]
    for i, (ok, duree, detail) in enumerate(resultats, 1):
        bilan.append(f"  Essai {i} : {'OK ' + format(duree, '.1f') + ' s' if ok else 'ÉCHEC'} {detail[:90]}")

    dossier = chemin_projet(config["chemins"]["logs"])
    dossier.mkdir(parents=True, exist_ok=True)
    rapport = dossier / f"test_captcha_{datetime.now().strftime('%Y-%m-%d_%Hh%M')}.txt"
    rapport.write_text("\n".join(bilan) + "\n", encoding="utf-8")
    print("\n" + "\n".join(bilan) + f"\n\nRapport enregistré : {rapport}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
