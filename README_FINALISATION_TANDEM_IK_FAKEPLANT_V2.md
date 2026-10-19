# Finalisation du premier tandem IK FakePlant — preuves croisées V2

**Installer à la racine :** `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`. L'archive ajoute trois outils dans `tools\tandem_ik`, deux lanceurs, des tests de validation; elle **remplace seulement** le lanceur `STATUT_TANDEM_IK_E2E_FAKEPLANT.cmd` (pour éliminer l'ancien faux négatif). Elle **ne remplace pas** le lanceur d'écriture `TEST_TANDEM_IK_E2E_FAKEPLANT.cmd` : **ne le relance pas**.

## Exécution (double-clic ou PowerShell 7)

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
.\FINALISER_TANDEM_IK_FAKEPLANT.cmd
```

Ce lanceur ne nécessite aucune phrase à retaper. Il interroge **en lecture seule** :

- **Neon / Konnaxion** sous scope World 58, release 132 : décision 1 publiée, émission unique, acceptée, envoyée au bon tenant et au bon World. Le contrôle est historique : **aucun jeton valide n'est nécessaire** pour lire les résultats après son expiration.
- **Orgo Docker / PostgreSQL** en transaction `SET TRANSACTION READ ONLY` : le Signal `konnaxion:decision:1:r1`, l'unique dossier et sa tâche, une instance du workflow V2 `completed`, les trois actions attendues et **une opération `publish_impact` SUCCEEDED** avec reçu `succeeded`.
- **Neon / table d'entrée publique IK** (hors scope World) : l'impact publié, avec correspondance exacte sur la référence externe **identifiant du Signal**, le dossier, la corrélation UUID de l'opération, son identifiant et sa clé d'idempotence. Ceci corrige la fausse recherche précédente `decision:1`. Le `operation_id` Konnaxion est dérivé par `uuid5(NAMESPACE_URL, 'interaction-kernel:'+operation.id)` et est vérifié exactement.

Chaque partie doit avoir **exactement un** enregistrement pertinent. Si une correspondance manque ou est ambiguë, le programme refuse `complete=true`.

### Fichier final

`C:\mycode\Konnaxion\Tandem_Local\fakeplant-IK-E2E-LIVE.LOCAL.json`

La première exécution réussie remplace le rapport V1 (qui disait `complete:false`) par un **V2** contenant `complete:true`, `konnaxion.impact_count=1`, l'opération d'impact dans `orgo.impact_operations`, les preuves croisées et les décomptes anti-doublons. **Le rapport V1 original est conservé une fois** à :

`C:\mycode\Konnaxion\Tandem_Local\fakeplant-IK-E2E-LIVE.ORIGINAL.LOCAL.json`

La substitution du rapport utilise `File.Replace` **avec un vrai chemin de sauvegarde** (évite le bug antérieur du `$null`). Une nouvelle exécution refait les requêtes lecture seule, valide les mêmes identifiants et ne réécrit pas un V2 identique. Si un V2 existant diverge, elle échoue sans écraser.

**Écritures autorisées : uniquement le rapport JSON local et sa sauvegarde.** Aucune modification Neon ou Orgo, aucun POST IK, aucune migration, aucune création de compte, aucune réémission de décision, aucun changement de jeton ou de workflow.

## Portée de la conclusion

Ce rapport termine **le test technique synthétique FakePlant**. Il ne constitue **pas** un audit ACL humaines, SSO, sécurité production, déploiement de tous les Universes, ni GO LIVE. La conformité des identités simulées et des routes existantes ne prouve pas une session SSO commune.

## Vérification de l'archive

Le JavaScript a fait l'objet de vérification de syntaxe et de 12 tests de preuve valides/invalides. Le Python a passé la compilation et 4 simulations de correspondance Neon. **L'exécution PowerShell 7 et la consultation des bases sur Windows doivent encore être confirmées localement.** Ne fournir en retour que la sortie du lanceur et le booléen `complete` ; aucun secret, `.env` ou jeton n'est requis.
