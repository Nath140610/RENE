# René L'Intérimaire — architecture modulaire

Le comportement est le même qu'avant, mais le projet est maintenant organisé en modules.

## Lancement

Render peut garder exactement l'ancien Start Command :

```bash
python -u rene_interimaire_render_final.py
```

Ou utiliser directement :

```bash
python -u main.py
```

## Ajouter une commande

1. Crée un nouveau fichier `.py` dans `rene/commands/`.
2. Mets ta commande dedans avec une fonction `async def setup(client)`.
3. Redéploie/redémarre René.
4. `rene.loader` découvre automatiquement le fichier, le charge et synchronise la commande Discord.

**Tu ne modifies jamais `main.py`, `rene_interimaire_render_final.py` ou une liste de commandes.**

Le fichier `NOUVELLE_COMMANDE_MODELE.py.txt` est un modèle prêt à copier.

Une commande `/reloadmodules` est aussi incluse pour recharger les extensions déjà présentes et synchroniser le CommandTree. Sur Render, un nouveau fichier venant de GitHub nécessite de toute façon le nouveau déploiement pour exister sur le serveur.

## Organisation

```text
rene_modulaire/
├── main.py
├── rene_interimaire_render_final.py   # compatibilité Render
├── requirements.txt
├── NOUVELLE_COMMANDE_MODELE.py.txt
└── rene/
    ├── core.py                        # moteur/services existants
    ├── loader.py                      # détection automatique
    ├── commands/                      # 1 commande = 1 fichier
    │   ├── config.py
    │   ├── moddefinir.py
    │   ├── salonquestions.py
    │   ├── vocalattente.py
    │   ├── diagnosticvocal.py
    │   ├── anciennete.py
    │   ├── dossier.py
    │   ├── lieroblox.py
    │   ├── warn.py
    │   ├── warnings.py
    │   ├── resetwarnings.py
    │   ├── stop.py
    │   └── reloadmodules.py
    └── events/
        ├── ready.py
        ├── members.py
        ├── messages.py
        └── voice.py
```

## Assets

Les images et `ATTENTE.mp3` restent à la racine, comme avant. `core.py` cherche ses assets depuis la racine du projet.

## Architecture discord.py

Le système utilise les **extensions** natives de discord.py. Chaque module possède un point d'entrée `setup(client)` et le loader appelle automatiquement `Bot.load_extension()` sur tous les fichiers trouvés.
