# Tandem — diagnostic Orgo FakePlant V2.1.2 (diagnostic seul)

**Dépôt cible uniquement :** `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`

Fermer la fenêtre du diagnostic, extraire ce ZIP à la racine du dépôt (remplacer les deux fichiers), puis double-cliquer sur **`START_TANDEM_ORGO_IDENTITE_DIAG.cmd`** (le lanceur `.cmd` existant V2.1.1 n'est pas remplacé).

Le rapport signale maintenant un `error_kind` et une `error_stage` lorsque Prisma ne peut interroger Orgo. L'ancien `QUERY_FAILED` était trop vague. Aucun message d'exception, DSN, mot de passe, secret ou jeton n'est affiché.

Catégories possibles : `PRISMA_CLIENT_NOT_GENERATED`, `PRISMA_MODULE_MISSING`, `PRISMA_SCHEMA_MISMATCH`, `DATABASE_UNREACHABLE`, `DATABASE_AUTH_FAILED`, `SCHEMA_NOT_ALIGNED`, `READ_ONLY_PROBE_REJECTED`, `UNCLASSIFIED`.

**Lecture seule** : transaction Prisma avec `SET TRANSACTION READ ONLY`, aucune commande d'import, seed ou migration, aucun appel au déploiement. Le script sonde d'abord l'API Docker Orgo lorsqu'elle fonctionne, sinon le Node local et son `.env`. Attention : la base locale peut être différente de la base du serveur Orgo.

**Important :** la réussite du diagnostic confirme l'identité dans la base interrogée, et non les droits d'accès ni la liaison IK. L'Agent composite et GO LIVE restent bloqués.

En cas d'erreur, transmettre uniquement `Mode`, `Code`, `Cause categorisee`, `Etape`, `Action`; ne jamais partager le contenu de `.env` ou `DATABASE_URL`.
