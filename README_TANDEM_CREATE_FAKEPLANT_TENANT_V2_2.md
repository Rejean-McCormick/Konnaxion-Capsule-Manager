# Capsule Manager - Creation minimale du tenant FakePlant dans Orgo (V2.2)

**Depot cible :** `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager` (overlay root-drop). Le script ne modifie pas ce depot ni ses anciennes fonctions.

## Double-clic

1. Demarrer **Docker Desktop** et s'assurer que `docker compose ps` dans `C:\mycode\Orgo\Orgo` montre **api** et **postgres** actifs.
2. Double-cliquer `START_TANDEM_CREER_FAKEPLANT_TENANT.cmd`.
3. Lire l'**apercu en lecture seule** (base active et presence du tenant). S'il est deja present et actif, le script s'arrete sans ecriture.
4. Si absent, verifier la base et taper exactement `CREER fakeplant` pour autoriser **une seule creation `Organization`** dans la base active du conteneur API.
5. Le script relit l'UUID et l'etat apres creation. Relancer `START_TANDEM_ORGO_IDENTITE_DIAG.cmd` pour l'inventaire des workflows.

## Garde-fous

- Ne fait **aucune migration**, ne lance **aucun seed standard**, n'importe **aucun des 900 profils**.
- Ne cree ni administrateur, ni role, ni compte utilisateur, ni token, ni workflow, ni case, ni task.
- Refuse de modifier un tenant existant mais inactif; idempotent pour un tenant actif existant.
- Execute Prisma dans le **conteneur API Orgo actif**, pas contre une base `DATABASE_URL` Windows distincte.
- Verifie la liaison canonique : `Konnaxion_Worlds/universes/fakeplant/integrations/orgo.tandem.json`.
- Aucune URL, mot de passe ou jeton de connexion n'est affiche.

**Ne pas utiliser si la base Compose Orgo n'est pas la base voulue.** Bien que `docker compose ps` soit local, son volume PostgreSQL est persistant. Faire une sauvegarde avant d'autoriser l'ecriture.

## Validation sans ecriture

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
pwsh -NoProfile -File .\tools\tandem_v2\CREATE_ORGO_FAKEPLANT_TENANT.ps1 -PreviewOnly
```

## Portee et limites

Le script termine **uniquement la premiere dependance d'identite** (`Organization.id`). Les ACL, l'authentification, le binding machine-verifiable Konnaxion/Orgo et les echanges Interaction Kernel restent a mettre en place et a tester. Aucune publication tandem n'est autorisee par ce changement.
