# Konnaxion Capsule Manager — correctif IK retour V1.1

Cible : `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`

## Erreur corrigée

`Exception lors de l’appel de « Replace » avec « 4 » argument(s) : « The path is empty. (Parameter 'path') »`

Le script V1 utilisait `[IO.File]::Replace($temp, $envFile, $null, $true)` : PowerShell 7.6/.NET sous Windows rejette le chemin de sauvegarde nul. V1.1 emploie un chemin de sauvegarde temporaire unique dans `backend`, puis supprime cette sauvegarde immédiatement. L'opération conserve la substitution atomique et les autorisations de `.env`. Le nettoyage est également tenté si une erreur survient.

## Installation

Extraire le ZIP **à la racine** de `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`, en acceptant uniquement le remplacement de `tools\tandem_ik\BIND_FAKEPLANT_IK_INGRESS.ps1`. Les autres fichiers du correctif V1 restent inchangés.

## Commandes

PowerShell 7 :

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
.\CONFIG_TANDEM_IK_RETOUR.cmd
```

Puis redémarrer Uvicorn **si** le script indique que le secret a été ajouté et, dans une autre fenêtre :

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
.\TEST_TANDEM_IK_RETOUR.cmd
```

Si l'erreur se reproduit, ne pas partager de secret ; envoyer seulement la sortie sans jeton.

## Portée et sécurité

Le script valide le Worker, le tenant, la présence du jeton dédié et la configuration de base; **ne génère ni n'affiche de jeton**. Il n'écrit **que** dans `Konnaxion\backend\.env` et ne modifie ni Neon ni PostgreSQL Orgo. Le test d'ingress volontairement invalide n'écrit aucun impact.

La vérification de syntaxe sous PowerShell 7/Windows doit être confirmée localement; les contrôles hors environnement sont statiques.
