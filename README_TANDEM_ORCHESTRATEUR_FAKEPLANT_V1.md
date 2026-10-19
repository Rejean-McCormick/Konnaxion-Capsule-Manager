# Tandem FakePlant — Orchestrateur permanent V1

Installer **une fois** à la racine :

`C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`

Double-cliquer **`TANDEM_FAKEPLANT_ORCHESTRATEUR.cmd`**, ou lancer dans PowerShell 7 :

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
.\TANDEM_FAKEPLANT_ORCHESTRATEUR.cmd
```

## Une installation — des étapes durables

| Menu | Étape | Écriture |
|---|---|---|
| 1 | Audit complet : inventaire → revalidation E2E IK → aperçu garde-fou → SSO/ACL → inventaire Konfid | Aucun changement de base ou de source; le rapport local V2 peut être revalidé |
| 2 | Reprendre les étapes en échec/en attente du dernier rapport | Lecture seule; **ne réinstalle jamais** le garde-fou |
| 3 | Afficher l'état précédent | Lecture seule |
| 4 | Installer le garde-fou OIDC sans inscription automatique | **Deux fichiers sources Konnaxion modifiés**, test de politique ajouté, copies originales sauvegardées; aucun changement de DB/env |
| 5 | Exporter le dernier rapport JSON en ZIP | Fichier local de diagnostic uniquement |
| 6 | Quitter | Aucun |

Orchestrateur conservé sous `tools/tandem_pipeline/` : composants éprouvés embarqués sous `vendor/`, sans remplacer les anciens lanceurs. L'option 1 réutilise la **preuve de la décision 1** et ne rejoue jamais l'ancien `TEST_TANDEM_IK_E2E_FAKEPLANT.cmd`. Les sources de l'Interaction Kernel, d'Orgo et de Konfid restent inchangées.

### Suivi des échecs

Les journaux et archives restent à :

`C:\mycode\Konnaxion\Tandem_Local\orchestrateur\LATEST_REPORT.json`

Un fichier JSON daté est conservé pour chaque passage et contient les étapes OK, ATTENTE et BLOQUE. L'étape 5 produit un ZIP de diagnostic **sur votre PC seulement**. Examinez le rapport avant partage. Il ne contient pas intentionnellement de secrets, mais la désensibilisation automatique ne peut pas garantir l'absence de tout secret dans des messages d'erreur externes.

Quand une étape bloque : transmettre **l'étape + la sortie ou le ZIP de diagnostic** pour préparer un correctif ciblé, puis lancer **option 2 : Reprendre**. Aucune intervention distante autonome ni auto-réparation qui modifie Orgo/Neon n'est prévue.

### Périmètre et alignement Konfid

- L'E2E IK prouvé est un **test technique synthétique** FakePlant. L'option audit vérifie qu'il reste corroborable.
- `guard-install` (action explicite) modifie seulement `backend/config/settings/base.py` et `backend/konnaxion/users/adapters.py`, plus un test de politique. Sauvegardes originales et empreintes SHA256 dans `...\orchestrateur\sauvegardes_sources\...`.
- Le garde-fou est **désactivé tant que `COMMON_OIDC_ENABLED=False`**; quand activé, il bloque par défaut la création de compte à partir d'un nouveau login OIDC. Une liaison explicite de compte humain reste nécessaire.
- Konfid n'est **pas** le fournisseur OIDC : l'IdP reste externe. Konfid assure les principals et account bindings, évaluations contextuelles avec assertions d'acteur signées et authentification de service. Il faut ultérieurement un vrai principal humain, une liaison dans chacune des deux applications et des tests ALLOW / DENY / STEP_UP + intertenant.
- Le contrôle `konfid-inventory` examine les contrats du dépôt local s'il le trouve; **il ne prouve ni la disponibilité du service ni sa qualification en production**. En l'absence d'un dépôt local reconnu, `KOA_KONFID_REPO` peut pointer vers la vraie racine (PowerShell : `$env:KOA_KONFID_REPO='C:\...\Konfid'`). Ne pas inventer une URL d'IdP.
- L'audit SSO/ACL peut rester en **ATTENTE** avec les 900 personas inchangés. **`ATTENTE` est un état attendu**, non un échec de l'E2E IK.
- Pas d'activation automatique du SSO, pas de mode `enforce`, pas de rôle attribué, pas de nouvel utilisateur, pas de mutation de Neon/Orgo, pas de migrations ni de refresh des jetons.

### Pré-requis et limites

Windows PowerShell 7, `uv`, Docker Compose Orgo accessible, `C:\mycode\Konnaxion\Konnaxion\backend`, `C:\mycode\Orgo\Orgo`. Pour revalider l'E2E, Uvicorn et l'API Orgo doivent être disponibles. Pour l'audit SSO, l'API Orgo, Docker et Neon doivent être joignables.

**Retour d'exécution :** 0 = OK ; 10 = ATTENTE de configuration ; 20 = échec bloquant. Un échec n'autorise jamais l'activation SSO ou un déploiement production.

Les tests statiques/simulations de ce ZIP ne remplacent pas un essai sur votre machine.
