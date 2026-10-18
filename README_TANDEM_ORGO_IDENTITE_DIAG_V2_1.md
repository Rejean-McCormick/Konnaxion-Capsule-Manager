# Capsule Manager — Tandem V2.1 : vérifier le tenant réel FakePlant dans Orgo

Dépôt cible : **`C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`**. Extraire ce ZIP à la racine, sans désinstaller Tandem V2. Il ajoute **trois fichiers seulement** (deux scripts et ce guide), sans écraser le Manager, ses modules ou ses paramètres.

## Un double-clic

**`START_TANDEM_ORGO_IDENTITE_DIAG.cmd`**. Exige PowerShell 7.

Le script confirme la liaison `Konnaxion_Worlds/universes/fakeplant/integrations/orgo.tandem.json`, puis interroge en **lecture seule** PostgreSQL via Prisma. Il utilise d'abord **l'API du Docker Compose Orgo actif** (`docker compose exec -T api node -`), pour vérifier la base **réellement utilisée par l'API**. Si aucun conteneur API Orgo n'est lancé, il peut utiliser Node et `Orgo/.env` ou le `DATABASE_URL` déjà présent dans la session. **Attention** : en mode Node local, vérifier que la base ciblée est bien celle de l'API Orgo.

L'opération effectue `SET TRANSACTION READ ONLY` dans PostgreSQL avant les lectures, ne lance aucune commande seed/migrate/apply/promote et **n'affiche pas les identifiants de connexion ni les secrets**.

Elle recherche **uniquement** `Organization.slug = fakeplant` et rapporte : UUID réel, statut et workflows actifs ayant des `WorkflowVersion` publiées (y compris définitions globales). Les UUID de workflows sont des **candidats** et non une preuve de permissions.

Le seed Orgo natif crée par défaut une organisation **`orgo`** (`ORGO_ORGANIZATION=orgo`), pas `fakeplant`. Si FakePlant n'existe pas dans la base interrogée, le rapport dira **ABSENT**. Il n'en crée pas une autre.

## Suite de la démarche

Copier ici le diagnostic : l'UUID `Organization.id` est nécessaire au binding `koa.tandem-identity/v1`, mais **le diagnostic ne fabrique pas un binding autorisé à la compilation**. Il reste à vérifier séparément : une `WorkflowVersion` appropriée, la correspondance tenant/World, les ACL des deux produits, les origines HTTPS et les échanges Interaction Kernel de bout en bout. Le déploiement distant composite n'est toujours **pas** implémenté ni autorisé.

## PS7 (option)

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
pwsh -NoProfile -File .\tools\tandem_v2\DIAG_ORGO_FAKEPLANT_IDENTITY.ps1
```

Ne pas coller le contenu de `.env`, le `DATABASE_URL` ou des jetons dans le chat.
