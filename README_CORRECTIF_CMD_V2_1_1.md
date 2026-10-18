# Correction du lanceur Tandem Orgo Identite V2.1.1

Dépôt cible **uniquement** : `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`.

Extraire le ZIP à la racine du dépôt en remplaçant seulement `START_TANDEM_ORGO_IDENTITE_DIAG.cmd`, puis double-cliquer sur ce fichier.

Cause corrigée : les parenthèses non échappées dans le `echo` d'un bloc `if (...)` de l'ancien CMD déclenchaient `. était inattendu.` sous `cmd.exe`, y compris quand PowerShell 7 était présent.

Le nouveau CMD supprime les blocs `if (...)` et les caractères spéciaux pouvant perturber l'encodage Windows, vérifie `pwsh.exe` et le script existant, puis transmet le code de sortie du diagnostic.

Le script Python/PowerShell/JavaScript de diagnostic existant **n'est pas modifié**. Il reste en lecture seule et ne déploie rien.

Alternative sans installer l'overlay :

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
pwsh -NoProfile -File .\tools\tandem_v2\DIAG_ORGO_FAKEPLANT_IDENTITY.ps1
```

Aucune connexion à Orgo n'a été testée sous Windows dans cet environnement ; seul le lanceur est corrigé et contrôlé statiquement.
