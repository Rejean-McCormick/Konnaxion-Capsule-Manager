# Tandem IK FakePlant — paramètres réseau non secrets (V1)

**Déposer à la racine** de `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`.

Lancer depuis PowerShell 7 :

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
.\CONFIG_TANDEM_IK_BASE.cmd
```

Cette commande n'ajoute que deux variables dans `C:\mycode\Konnaxion\Konnaxion\backend\.env` **si elles sont absentes ou vides** :

- `IK_ORGO_INTERACTIONS_URL=http://127.0.0.1:4000/api/v3/ik/interactions`
- `IK_ORGO_TARGET_ORGANIZATION=0c2d7244-a0c0-4a10-8f11-0389f275f133`

Elle refuse tout écrasement de configuration préexistante différente, ou des clés dupliquées. Préserve les autres lignes du `.env` sans les afficher. Écriture atomique du fichier; pas de SQL, pas de migration, pas de Docker, pas de secrets ou de jetons créés. Si la configuration change, **redémarrer le processus Uvicorn Konnaxion** pour que Django recharge les variables. Aucune confirmation à taper.

**Cette étape ne rend pas IK opérationnel** : il reste à émettre un jeton Orgo dédié aux scopes `signals:write` et `workflows:execute`, à configurer la paire de secrets Django/Worker, à publier un workflow isolé FakePlant et à réaliser le test de bout en bout. Le jeton FP-S01 ne doit pas être réutilisé.

Sources : snapshots `znapshot_Konnaxion_10180856.zip`, `znapshot_Orgo_10180856.zip`, `znapshot_Konnaxion_Capsule_Manager_10180856.zip` ; rapport de diagnostic confirmé par l'utilisateur. Adapté exclusivement au développement local Windows, jamais à un déploiement public.
