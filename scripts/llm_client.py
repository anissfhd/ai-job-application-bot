"""
Client LLM unique pour OpenAI, Gemini (gratuit), Groq (gratuit) et Claude.
Le fournisseur est choisi dans config/config.yaml -> llm.fournisseur.

    llm = ClientLLM(config)
    texte = llm.generer(prompt_systeme, message_utilisateur)
    texte = llm.generer(prompt, message, images=[jpeg_bytes], tache="formulaire")

`tache` permet d'utiliser un modèle différent selon le travail :
    llm.<fournisseur>.modele              -> textes (extraction, CV, lettre)
    llm.<fournisseur>.modele_formulaire   -> remplissage des formulaires (vision)
    llm.<fournisseur>.modele_diagnostic   -> analyse des échecs
"""

from __future__ import annotations

import base64
import logging
import time


class ErreurLLM(Exception):
    """Appel au LLM impossible (clé manquante, quota épuisé, réponse vide…)."""


class ClientLLM:
    def __init__(self, config: dict, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("job_automation")
        cfg = config.get("llm", {})
        self.fournisseur = str(cfg.get("fournisseur", "openai")).lower()
        self.params = cfg.get(self.fournisseur, {}) or {}
        self.cle = (self.params.get("cle_api") or "").strip()
        self.modele = self.params.get("modele")
        self.temperature: float | None = float(cfg.get("temperature", 0.3))
        self.max_tokens = int(cfg.get("max_tokens_sortie", 16000))
        self.tentatives = int(cfg.get("tentatives_api", 5))
        self.pause_min = float(cfg.get("pause_entre_appels_secondes", 0))
        self._dernier_appel = 0.0
        self._sans_temperature: set[str] = set()   # modèles qui refusent « temperature »
        self._alerte_images = False
        # Moteur de secours (ex. chat web Gemini) utilisé si la clé API est morte.
        # Branché après coup par brancher_secours() : il a besoin du navigateur.
        self._secours = None
        self._nom_secours = ""
        self._api_hors_service = False

        if self.fournisseur not in ("openai", "gemini", "groq", "claude"):
            raise ErreurLLM(f"Fournisseur LLM inconnu : {self.fournisseur} (openai | gemini | groq | claude)")
        # Avec un moteur de chat web, l'absence de clé API n'est PAS bloquante : tout passera
        # par le moteur de secours. Sans cela, supprimer une clé expirée empêcherait le démarrage.
        chat_web = str(cfg.get("moteur_formulaire", "api")).lower() != "api"
        if not self.cle:
            if not chat_web:
                raise ErreurLLM(
                    f"Clé API manquante pour « {self.fournisseur} ». Renseigne llm.{self.fournisseur}.cle_api "
                    f"dans config/config.yaml (ou la variable d'environnement correspondante dans .env)."
                )
            self._api_hors_service = True
            self._client = None
            self.logger.info(f"Aucune clé {self.fournisseur} : tous les appels passeront par le chat web.")
            return
        self._client = self._creer_client()

    @staticmethod
    def chainer(*moteurs):
        """
        Assemble plusieurs moteurs en une chaîne de secours exposant la même interface.
        Le premier qui répond gagne ; si l'un échoue, on passe au suivant.
        Sert à enchaîner « chat Gemini » puis « chat DeepSeek » : Gemini esquive parfois une
        tâche qu'il réussit d'habitude, et DeepSeek est meilleur pour réécrire du LaTeX.
        """
        return _MoteurEnChaine([m for m in moteurs if m is not None])

    def brancher_secours(self, moteur, nom: str = "chat web") -> None:
        """
        Déclare un moteur de repli exposant la même méthode generer(...). Il prend le relais
        dès que la clé API se révèle épuisée ou invalide, pour toute la session.
        """
        self._secours = moteur
        self._nom_secours = nom

    def api_disponible(self) -> bool:
        """
        Faux si la clé est absente ou constatée morte : tout appel partirait alors au moteur
        de secours. Les appels qui ont un meilleur moteur dédié (réécriture LaTeX) le
        consultent pour ne pas passer par un chat qui n'a pas le bon contexte.
        """
        return not self._api_hors_service

    # ------------------------------------------------------------------

    def modele_pour(self, tache: str | None) -> str:
        """Modèle à utiliser pour une tâche (retombe sur le modèle principal)."""
        if tache:
            specifique = self.params.get(f"modele_{tache}")
            if specifique:
                return specifique
        return self.modele

    def _creer_client(self):
        if self.fournisseur == "openai":
            from openai import OpenAI
            return OpenAI(api_key=self.cle, timeout=300)
        if self.fournisseur == "gemini":
            from google import genai
            return genai.Client(api_key=self.cle)
        if self.fournisseur == "groq":
            from groq import Groq
            return Groq(api_key=self.cle)
        import anthropic
        return anthropic.Anthropic(api_key=self.cle)

    def _appel_openai(self, systeme: str, message: str, images: list[bytes], modele: str) -> str:
        if images:
            contenu: list | str = [{"type": "text", "text": message}] + [
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(img).decode(), "detail": "high"}}
                for img in images
            ]
        else:
            contenu = message
        params = {}
        if self.temperature is not None and modele not in self._sans_temperature:
            params["temperature"] = self.temperature
        try:
            reponse = self._client.chat.completions.create(
                model=modele,
                messages=[{"role": "system", "content": systeme}, {"role": "user", "content": contenu}],
                max_completion_tokens=self.max_tokens,
                **params,
            )
        except Exception as e:  # noqa: BLE001
            # Certains modèles (raisonnement) refusent « temperature » : on la retire et on réessaie
            if "temperature" in str(e) and modele not in self._sans_temperature:
                self._sans_temperature.add(modele)
                return self._appel_openai(systeme, message, images, modele)
            raise
        choix = reponse.choices[0]
        if choix.finish_reason == "length":
            raise ErreurLLM("Réponse tronquée (limite de tokens) : augmente llm.max_tokens_sortie.")
        return choix.message.content or ""

    def _appel_gemini(self, systeme: str, message: str, images: list[bytes], modele: str) -> str:
        from google.genai import types
        contenus: list = [message] + [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in images]
        reponse = self._client.models.generate_content(
            model=modele,
            contents=contenus,
            config=types.GenerateContentConfig(
                system_instruction=systeme,
                temperature=self.temperature,
                max_output_tokens=self.max_tokens,
            ),
        )
        return reponse.text or ""

    def _appel_groq(self, systeme: str, message: str, images: list[bytes], modele: str) -> str:
        if images and not self._alerte_images:
            self._alerte_images = True
            self.logger.warning("Groq : les captures d'écran ne sont pas envoyées (vision non gérée).")
        reponse = self._client.chat.completions.create(
            model=modele,
            messages=[{"role": "system", "content": systeme}, {"role": "user", "content": message}],
            temperature=self.temperature,
            max_tokens=min(self.max_tokens, 8000),
        )
        return reponse.choices[0].message.content or ""

    def _appel_claude(self, systeme: str, message: str, images: list[bytes], modele: str) -> str:
        contenu: list = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(img).decode()}}
            for img in images
        ] + [{"type": "text", "text": message}]
        # Streaming : évite les délais d'attente sur les longues réponses (CV complet).
        # « fallbacks » : si le modèle refuse une requête, l'API la rejoue sur un modèle de secours.
        with self._client.beta.messages.stream(
            model=modele,
            max_tokens=self.max_tokens,
            system=systeme,
            messages=[{"role": "user", "content": contenu}],
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as flux:
            reponse = flux.get_final_message()
        if reponse.stop_reason == "refusal":
            raise ErreurLLM("Claude a refusé la requête.")
        if reponse.stop_reason == "max_tokens":
            raise ErreurLLM("Réponse tronquée (max_tokens atteint) : augmente llm.max_tokens_sortie.")
        return "".join(bloc.text for bloc in reponse.content if bloc.type == "text")

    # ------------------------------------------------------------------

    def generer(self, systeme: str, message: str, images: list[bytes] | None = None,
                tache: str | None = None) -> str:
        """Comme _generer, mais sans aucune donnée personnelle envoyée (voir confidentialite.py)."""
        from confidentialite import courante as confidentialite
        c = confidentialite()
        return c.demasquer(self._generer(c.masquer(systeme), c.masquer(message), images=images, tache=tache))

    def _generer(self, systeme: str, message: str, images: list[bytes] | None = None,
                 tache: str | None = None) -> str:
        """
        Envoie (prompt système, message [, images JPEG]) au LLM et renvoie le texte de la réponse.
        Réessaie avec une attente croissante en cas d'erreur temporaire (quota, 5xx, réseau).
        """
        # Clé absente ou déjà constatée morte : on ne perd plus de temps à la réessayer.
        if self._api_hors_service:
            if self._secours is None:
                raise ErreurLLM(f"Clé {self.fournisseur} inutilisable et aucun moteur de secours "
                                f"disponible (llm.moteur_formulaire).")
            return self._secours.generer(systeme, message, images=images, tache=tache)

        appel = {"openai": self._appel_openai, "gemini": self._appel_gemini,
                 "groq": self._appel_groq, "claude": self._appel_claude}[self.fournisseur]
        modele = self.modele_pour(tache)
        images = images or []
        derniere_erreur: Exception | None = None

        for essai in range(1, self.tentatives + 1):
            # Espacement minimal entre deux appels (quotas par minute)
            attente = self.pause_min - (time.monotonic() - self._dernier_appel)
            if attente > 0:
                time.sleep(attente)
            try:
                self._dernier_appel = time.monotonic()
                texte = appel(systeme, message, images, modele)
                if not texte.strip():
                    raise ErreurLLM("Réponse vide du LLM.")
                return texte
            except Exception as e:  # noqa: BLE001 — chaque SDK a ses propres classes d'erreurs
                derniere_erreur = e
                message_erreur = str(e)
                epuise = "insufficient_quota" in message_erreur
                definitive = any(code in message_erreur for code in ("401", "403", "API key", "api_key", "invalid x-api-key"))
                # Clé épuisée ou invalide : si un moteur de secours est branché, on bascule
                # dessus définitivement pour la session au lieu d'arrêter le robot.
                if (epuise or definitive) and self._secours is not None:
                    if not self._api_hors_service:
                        self._api_hors_service = True
                        self.logger.warning(f"Clé {self.fournisseur} inutilisable ({message_erreur[:120]}) "
                                            f"-> bascule sur le moteur de secours « {self._nom_secours} ».")
                    return self._secours.generer(systeme, message, images=images, tache=tache)
                if epuise:
                    raise ErreurLLM("Crédit API épuisé : recharge ton compte (ex. platform.openai.com > Billing).") from e
                if definitive or (isinstance(e, ErreurLLM) and ("refusé" in message_erreur or "tronquée" in message_erreur)):
                    break
                delai = min(20 * essai, 120) if ("429" in message_erreur or "quota" in message_erreur.lower()) else 5 * essai
                self.logger.warning(f"LLM {self.fournisseur} ({modele}) : erreur « {message_erreur[:200]} » — "
                                    f"essai {essai}/{self.tentatives}, nouvel essai dans {delai} s")
                time.sleep(delai)

        raise ErreurLLM(f"Échec de l'appel au LLM ({self.fournisseur}, {modele}) : {derniere_erreur}")


class _MoteurEnChaine:
    """
    Plusieurs moteurs essayés dans l'ordre, derrière l'interface habituelle generer(...).

    Construit par ClientLLM.chainer(...). Chaque moteur garde ses propres ressources
    (onglet de chat) : fermer() est propagé à tous, pour que la fin d'une candidature
    referme aussi bien la conversation Gemini que l'éventuelle conversation DeepSeek.
    """

    def __init__(self, moteurs: list, logger: logging.Logger | None = None):
        self.moteurs = moteurs
        self.logger = logger or logging.getLogger("job_automation")

    def generer(self, systeme: str, message: str, images: list[bytes] | None = None,
                tache: str | None = None) -> str:
        derniere: Exception | None = None
        for rang, moteur in enumerate(self.moteurs, start=1):
            try:
                return moteur.generer(systeme, message, images=images, tache=tache)
            except Exception as e:  # noqa: BLE001 — chaque moteur a ses propres erreurs
                derniere = e
                nom = type(moteur).__name__
                if rang < len(self.moteurs):
                    self.logger.warning(f"Moteur {nom} indisponible ({str(e)[:110]}) "
                                        f"-> bascule sur le moteur suivant.")
                else:
                    self.logger.error(f"Moteur {nom} en échec, plus aucun secours : {str(e)[:160]}")
        raise ErreurLLM(f"Tous les moteurs ont échoué : {derniere}")

    def fermer(self) -> None:
        """Fin de candidature : referme les conversations de tous les moteurs de la chaîne."""
        for moteur in self.moteurs:
            fermer = getattr(moteur, "fermer", None)
            if callable(fermer):
                try:
                    fermer()
                except Exception:  # noqa: BLE001 — une fermeture ratée ne doit rien bloquer
                    pass
