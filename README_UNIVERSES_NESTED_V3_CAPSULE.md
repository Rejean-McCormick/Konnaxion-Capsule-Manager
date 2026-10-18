# Capsule Manager — Universes imbriqués V3 (correctif source)

## Objectif et limites

Ce correctif aligne **la construction d'une nouvelle capsule** et **la configuration Docker d'une instance** sur le répertoire canonique `C:\mycode\Konnaxion\Konnaxion_Worlds\universes\` :

```
Konnaxion_Worlds/
  backend/                  # moteur Python >= 0.3.7
  universes/
    <universe>/
      universe.yaml
      worlds/
        <seed_pack_key>/
          world.yaml
          scenarios/...     # ou scenario.json
```

Le Builder détecte `Konnaxion_Worlds` comme dépôt frère de `Konnaxion` (ou par `KX_WORLDS_SOURCE_DIR`). Il **échoue** si le moteur est antérieur à `0.3.7`, si le catalogue imbriqué manque, ou si un manifeste World référencé manque ou ne correspond pas. Il copie le catalogue canonique sous `seed-data/universes` **dans le contexte temporaire de build Docker uniquement**. Il ne copie plus l'ancien `Konnaxion_Worlds/backend/seed-data/worlds` comme référence principale. Les seeds spécialisés non-Universe de Konnaxion restent en place.

La configuration `django.env`, `runtime.env` et les trois services Django/Celery déclarent désormais :

```
KONNAXION_UNIVERSE_SEED_ROOT=/app/seed-data/universes
KONNAXION_WORLD_SEED_ROOT=/app/seed-data/universes
```

Les variables explicites Compose protègent également les **instances existantes** dont les anciens fichiers `.env` n'ont pas encore été régénérés.

Lors d'un **nouveau GO LIVE** d'une capsule reconstruite, le Capsule Manager parcourt les manifestes dans `/app/seed-data/universes` et exécute le service canonique `worlds_apply_universe` avec la version déclarée et `--promote`. Ce flux peut modifier la base de données de l'instance déployée : utilisez le mécanisme normal de sauvegarde, approbation et publication. **Installer cet overlay ne déclenche aucun build ni déploiement.**

## Validation locale, sans déploiement

À la racine `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager` :

```
py -m compileall -q kx_builder kx_agent kx_shared
py -m pytest -q tests/test_worlds_build_context_v26.py tests/test_worlds_data_plane_runtime_v21.py tests/test_packaged_universe_publication.py tests/test_runtime_django_env_v14.py
```

Les tests ont été exécutés dans un environnement Python hors Windows, et un contrôle a été fait avec le snapshot `Konnaxion_Worlds 0.3.7` : **6 Universes, 55 Worlds** chargés dans un contexte Docker de simulation. Un véritable build Docker, GO LIVE, contrôle d'API et retour arrière d'instance **n'ont pas été exécutés**.

## Gouvernance et visibilité

Le Capsule Manager **ne force jamais** `private` en `public`. Chaque Universe et ses Worlds conservent les valeurs de leur `universe.yaml`. Les changements effectués seulement en base locale sans édition du **catalogue source** ne se propagent pas automatiquement à une nouvelle capsule. Pour une publication, utiliser le Manager Universes/Worlds qui écrit dans la source canonique, vérifier les YAML puis reconstruire la capsule.

Les six Universes du catalogue peuvent être inclus dans l'image, mais **leur visibilité finale dépend des manifestes et de la base de l'instance cible**; les sept enregistrements d'une base locale (y compris `legacy`) ne sont pas copiés automatiquement dans une autre base.
