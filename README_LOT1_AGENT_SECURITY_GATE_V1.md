# Konnaxion Capsule Manager — Lot 1, admission Security Gate fail-closed

**Cible :** `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`  
**Source exacte :** `Konnaxion_Capsule_Manager_Code_snapshot.zip` du 18 octobre 2026, 21:28.  
**Périmètre :** patch de code **local** pour le démarrage d'instances (pas d'activation de release distante).

Ce pack s'ajoute au dépôt. On conserve l'orchestrateur Tandem FakePlant existant, les preuves IK `complete=true` et les comptes synthétiques : ils ne sont jamais touchés.

## Installation sécurisée

Extraire le ZIP directement dans le dépôt `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`, puis, dans PowerShell 7 :

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
.\PREVIEW_LOT1_SECURITY_GATE.cmd
.\INSTALL_LOT1_SECURITY_GATE.cmd
.\VERIFIER_LOT1_SECURITY_GATE.cmd
```

L'aperçu est en **lecture seule**. L'installation :

- vérifie les empreintes SHA-256 **des trois sources** du snapshot et du test ajouté, et l'intégrité des payloads;
- refuse tout fichier local déjà modifié ou installation partielle (pas de `--force`);
- sauvegarde les trois sources dans `C:\mycode\Konnaxion\Tandem_Local\lot1_security_gate\backups\<horodatage>`;
- remplace de manière atomique **uniquement** `kx_agent/actions.py`, `kx_agent/api.py`, `kx_manager/services/deploy.py`, et ajoute `tests/test_agent_security_gate_failclosed_lot1.py`;
- lance automatiquement **40 tests ciblés**, sans démarrage d'Agent ni accès à Neon/Orgo. En cas d'échec des tests, l'ancien code est remis en place automatiquement si les empreintes correspondent;
- consigne les résultats dans `Tandem_Local\lot1_security_gate\backups\<horodatage>\tests.log`.

**Si l'aperçu affiche `UNEXPECTED`, ne pas installer.** Le dépôt a changé depuis le snapshot; il faut préparer un patch recalculé, sans écraser les changements.

Relancer l'installation après succès est **idempotent**. On peut vérifier avec `VERIFIER_LOT1_SECURITY_GATE.cmd`. En cas de besoin, `ROLLBACK_LOT1_SECURITY_GATE.cmd` ne restaure que si les fichiers sont toujours exactement ceux installés et si les sauvegardes sont intactes; aucun remplacement à l'aveugle.

## Correctifs apportés

1. `instance.start` dans **l'Agent** refuse `run_security_gate=false` et `ActionRequest.require_security_gate=false` **avant** toute opération Docker/migration.
2. Un résultat du contrôle ne permet un démarrage que si son statut Agent est **SUCCEEDED**, son statut de sécurité est **PASS** ou **WARN**, et son fichier de preuve est présent (créé par le contrôle réel). `SUCCEEDED` avec `SKIPPED`, `UNKNOWN` ou preuve manquante est bloquant.
3. Quand le runtime Compose n'existe pas, un contrôle `blocking=true` donne **BLOCKED** au lieu de `SUCCEEDED/SKIPPED`. Le contrôle d'information `blocking=false` peut encore signaler `SKIPPED` sans autoriser un démarrage.
4. L'API Pydantic rejette explicitement `run_security_gate=false` pour `InstanceStartRequest`.
5. Le Manager rejette `BaseDeployRequest.run_security_gate=false` et transmet **toujours** `run_security_gate=true` à `instance.start`.

La protection finale doit être appliquée **à l'Agent réellement installé sur la machine qui déploie**; installer le correctif dans les sources Windows ne met pas à jour automatiquement un éventuel Agent Linux sur VPS.

## Qualification locale effectuée

- Snapshot original : **19/19 tests ciblés de référence**.
- Correctif : **40/40 tests ciblés**.
- Cycle installateur sur copie du snapshot : **aperçu → installation → vérification → relance idempotente → rollback → aperçu** réussi.
- Suite globale sur cet environnement Linux : **109 échecs préexistants**, identiques avant et après correctif; **1021 tests passés + 2 ignorés avant**, **1042 tests passés + 2 ignorés après**. Les échecs restent à investiguer lors de la qualification multiplateforme (chemins Linux/Windows, UI et autres contrats). **Ce lot n'est pas une certification GA ni une campagne KonnaxionDiag.**

## Étapes suivantes du programme directeur

1. Raccorder à l'Agent réellement déployé la **vérification cryptographique de l'autorisation signée** de KonnaxionDiag pour le ReleaseSet exact. Ce contrôle est **non implémenté par ce correctif**.
2. Vérifier tous les chemins d'activation et de restauration de l'Agent, les interfaces de déploiement et le profil d'hébergement final. Réparer les tests historiques avant toute qualification de production.
3. Étendre l'admission à la release composite Konnaxion + Orgo, avec rollback et réconciliation coordonnés.

## Aucune modification cachée

Ce pack n'appelle aucune API de Konnaxion, Orgo, Konfid ni IK. Il ne touche à aucune base PostgreSQL, à aucun tenant ou persona, et ne redémarre pas les serveurs. L'installation se limite aux quatre fichiers source/test ci-dessus et aux sauvegardes locales. Les `.cmd` ne sont pas signés; examiner les scripts si leur provenance est incertaine.
