# Netcup — préparation et vérifications avant tout GO LIVE

**État : documentation de préproduction, non qualification d’un VPS réel.**
Aucune action d’installation, de redémarrage, de sécurité SSH ou de déploiement
n’est accomplie par la lecture de ce document ni par les tests unitaires.

## Sources effectives dans le dépôt

- L’interface de préparation est définie dans `kx_manager/ui/page_parts/deploy.py`
  et les opérations et vérifications dans `kx_manager/services/netcup.py`.
- Les contrats d’opération, profils réseau et barrières de sécurité sont décrits
  dans `docs/DOC-12_Konnaxion_Install_Runbook.md`,
  `docs/DOC-14_Konnaxion_Operator_Guide.md`,
  `docs/DOC-07_Konnaxion_Security_Gate.md` et
  `docs/BRANCH08_RELEASE_ADMISSION.md`.
- `NETCUP_GO_LIVE.md` n’est pas présent dans le SmartDump du 23 novembre 2026 ;
  cette page remplace son lien depuis le README sans se présenter comme un
  rapport d’admission ou une autorisation opérationnelle.

## Contrôles à réaliser et à faire approuver séparément

1. Vérifier l’identité du serveur et les autorisations opérateur dans l’interface
   du fournisseur ; toute réinstallation du système est une action manuelle
   destructrice, hors du périmètre de l’overlay.
2. Vérifier hors bande l’empreinte de la clé SSH du nouvel hôte avant de lui
   faire confiance. Ne pas importer de mot de passe, clé privée, fingerprint ou
   secret dans un dépôt, un rapport ou un journal d’audit.
3. Contrôler l’utilisateur administrateur, sudo, la désactivation des connexions
   non nécessaires et la compatibilité du profil réseau choisi.
4. Obtenir une admission indépendante signée correspondant aux identités et
   images *réellement mesurées* ; refuser toute divergence (fail-closed).
5. Vérifier sauvegardes, reprise, rollback, santé produit et reçus persistants
   avant qu’une autorité habilitée décide d’un GO LIVE.

Aucun de ces points n’est marqué comme accompli sur un hôte réel par le seul
snapshot ou par les tests bilatéraux synthétiques.
