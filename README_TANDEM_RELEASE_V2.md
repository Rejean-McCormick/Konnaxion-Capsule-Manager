# Capsule Manager — Release tandem V2 (Konnaxion + Orgo)

**Portée actuelle : préflight strict, build de 2 images Docker Orgo, association à une capsule Konnaxion signée, verrou SHA-256, signature Ed25519, vérification.**

**Pas de publication : GO LIVE explicitement bloqué** parce que le Konnaxion Agent existant n'applique pas de capsule composite et ne sait pas restaurer les deux services. Cette version n'ajoute ni bouton déployer ni route HTTP privilégiée au Manager, ne touche à aucune base et n'injecte aucun seed.

## Installation

Décompresser l'overlay à la racine **`C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`**. Les modules existants `kx_builder`, `kx_agent`, `kx_manager` ne sont pas écrasés.

## Un double-clic pour commencer

Double-cliquer **`START_TANDEM_RELEASE_V2.cmd`**. Il fait uniquement le préflight FakePlant : 6 Worlds Konnaxion et 18 scénarios Orgo attendus. Le script exige que le manifeste `universes/fakeplant/integrations/orgo.tandem.json` (overlay tandem V1 précédent) soit présent; il refuse les dossiers incomplets. Il ne déploie rien.

## Verrouillage / compilation (uniquement après contrôle du tenant)

1. Créer un fichier d'identité **hors des dépôts / hors des seeds** à partir de `TANDEM_IDENTITY_EXEMPLE.json`. Remplacer `orgo_organization_id` par **l'UUID réel de la base Orgo de destination**, renseigner un WorkflowVersion publié et vérifier les ACL dans les deux produits. Ne mettre **aucun jeton ni secret** dans ce JSON. Indiquer les vraies origines HTTPS, en prévoyant que `*.konnaxion.com` **ne couvre pas** `fakeplant.orgo.konnaxion.com`.
2. Construire et vérifier la capsule **Konnaxion signée** par le Builder existant. Utiliser une paire Ed25519 persistante déjà autorisée par ce Builder.
3. Dans PowerShell 7 à la racine du Capsule Manager (Docker Desktop requis pour la construction Orgo) :

```powershell
py -3 -m tools.tandem_v2.cli preflight --strict-binding --binding C:\secrets\tandem\fakeplant-binding.json
py -3 -m tools.tandem_v2.cli build --binding C:\secrets\tandem\fakeplant-binding.json `
  --kxcap C:\artifacts\ma-capsule-signee.kxcap `
  --public-key C:\keys\release-ed25519-public.pem `
  --private-key C:\keys\release-ed25519-private.pem `
  --release-id fakeplant-v2-001 --out C:\artifacts\tandem
py -3 -m tools.tandem_v2.cli verify --release-dir C:\artifacts\tandem\fakeplant-v2-001 `
  --public-key C:\keys\release-ed25519-public.pem --verify-konnaxion-capsule
```

Le build exige **`Orgo/package-lock.json` sur le disque** pour `npm ci`. Les snapshots SmartDump peuvent l'omettre; le diagnostic l'indique séparément. Le build appelle le Builder Konnaxion uniquement en **vérification** (`uv run --no-sync kx-builder capsule verify --strict --require-signature-verifier`) et **Docker** pour compiler/exporter `orgo_api.oci.tar` et `orgo_web.oci.tar`; le worker réutilisera l'image API. La release immuable comprend la capsule Konnaxion signée, les archives Orgo, `release.json` et `signature.json` (Ed25519). Les sources Orgo et Orgo_Worlds doivent être compatibles avec leur déploiement; la validation du réseau Orgo_Worlds et de son URL interne reste une barrière de production.

La commande `plan` affiche le graphe de déploiement et les barrières de sécurité, **sans** publication :

```powershell
py -3 -m tools.tandem_v2.cli plan --release-dir C:\artifacts\tandem\fakeplant-v2-001 `
  --public-key C:\keys\release-ed25519-public.pem
```

**Ne pas mettre `ORGO_LOCAL_AUTO_LOGIN=true` en production**, ne pas utiliser `host.docker.internal` comme API Orgo_Worlds déployée, ne pas publier les ports 3000/4000 directement; utiliser un proxy TLS / OIDC, des secrets d'hôte et des ACL par tenant. Le service Orgo possède sa **propre base**, ses migrations et un worker; `accepted` de l'Interaction Kernel ne signifie pas que le Case est exécuté.

## Limitations / gates avant GO LIVE

- Agent capsule composite absent : mécanisme Konnaxion `.kxcap` reste centré sur les images Konnaxion; **ne pas changer artificiellement le schéma du manifeste pour contourner le vérificateur**.
- Publication coordonnée (deux migrations + deux bases + deux healthchecks + retour arrière par produit) **non implémentée**.
- Injecteur FakePlant Orgo `orgo.scenario.v1` **explicit-only**; les 18 scénarios ne sont pas exécutés par le build.
- Réseau OIDC/IK (scopes, droits, jetons, TLS, liaisons UUID), tests cross-tenant et receipts asynchrones **non validés en réel**.
- Domaine TLS `fakeplant.orgo.konnaxion.com` nécessite un certificat adapté, pas un wildcard premier niveau.
- Rollback de base non atomique entre Konnaxion et Orgo; sauvegardes distinctes requises.

## Tests hors ligne

`py -3 -m unittest discover -s tests -p 'test_tandem_release_v2.py' -v`

Les tests simulés ne constituent pas une preuve Docker, HTTP, IK, DNS ou GO LIVE. Le CLI ne possède volontairement **aucune commande `deploy`**. Le paquet n'inclut pas Orgo_Worlds comme service runtime; il exige une URL HTTPS disponible dans une release future.
