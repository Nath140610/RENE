from __future__ import annotations

import threading
import time

from rene.core import DISCORD_TOKEN, bot, logger, run_web_server


def run() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError(
            "La variable d'environnement DISCORD_TOKEN est absente. "
            "Ajoute-la dans Render > Environment."
        )

    logger.info("Démarrage du serveur web Render...")
    web_thread = threading.Thread(
        target=run_web_server,
        name="rene-web-server",
        daemon=True,
    )
    web_thread.start()

    logger.info("Connexion de René à Discord...")
    try:
        bot.run(DISCORD_TOKEN.strip(), log_handler=None)
    except Exception:
        logger.exception("René s'est arrêté à cause d'une erreur fatale.")
        raise

    # Sur Render, /stop garde le serveur web actif pour ne pas provoquer
    # un redémarrage automatique immédiat du service.
    if bot.stopping:
        logger.info(
            "René dort volontairement. Redémarre le service Render pour le réveiller."
        )
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    run()
