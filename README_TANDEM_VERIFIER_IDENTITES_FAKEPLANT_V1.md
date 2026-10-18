# Konnaxion + Orgo — preuve locale des identités FakePlant V1

À extraire à la racine de : `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`.

Double-clic : `START_TANDEM_VALIDER_IDENTITES_FAKEPLANT.cmd`

Le lanceur ne demande **aucune confirmation à taper**. Il vérifie :

- la fixture de 900 personas et 2 736 adhésions et ses empreintes SHA256 dans Konnaxion_Worlds et Orgo_Worlds;
- les 900 comptes canoniques Konnaxion non connectables, leurs 900 memberships Universe et 2 736 memberships World actifs dans `neondb`;
- les 900 comptes Orgo désactivés et 900 profils rattachés à l'organisation `fakeplant` (UUID fixé par la vérification précédente : `0c2d7244-a0c0-4a10-8f11-0389f275f133`), dans la **base du conteneur API actif**;
- l'égalité exacte des ensembles de courriels et de références personnelles, sans les imprimer.

Les transactions PostgreSQL des deux côtés sont explicitement `READ ONLY`.

À réussite seulement, il crée ou vérifie le **brouillon local** `C:\mycode\Konnaxion\Tandem_Local\fakeplant-binding.DRAFT.json`. Le fichier préexistant ne sera **jamais écrasé** s'il diverge. Il écrit aussi un justificatif sans secrets dans `fakeplant-identity-verified.LOCAL.json` (écrasement atomique de ce fichier de diagnostic uniquement).

**Limite volontaire :** la preuve locale d'identité n'est pas un binding de production. Les URL HTTPS, permissions ACL, OIDC, Interaction Kernel, workflows et GO LIVE demeurent non vérifiés. Le preflight strict du Capsule Manager reste bloqué. Aucun seed, import, migration ni déploiement. Les 75 clones des releases sont conservés sans aucune modification.

En cas d'échec, aucune opération n'est effectuée dans les bases. La preuve locale ne sera pas mise à jour.
