<!-- kOA Doc Server · Docs IA · lot 03 · projet : Konnaxion_Capsule_Manager · proposition dérivée à valider -->
# Contexte IA — Konnaxion_Capsule_Manager

## Identité et périmètre observés

Constructeur et gestionnaire de capsules Konnaxion, préparation des installations et opérateur d’admission.

**Limite d'autorité :** La construction de capsule, le diagnostic indépendant et le PEP restent séparés; le Manager ne contourne pas les règles de release.

**Provenance :** `SmartDump_20261120_221507.zip` → `Konnaxion_Capsule_Manager_Code_snapshot.zip` (20 novembre 2026). Les sources citées sont celles de cette capture; le propriétaire du dépôt conserve l'autorité sur les révisions présentes et futures.

## Avant toute proposition de modification

1. Déterminer si le travail demandé porte sur **un document, un contrat, une donnée, un exécutable ou un résultat dérivé**.
2. Consulter les fichiers propriétaires, dans cet ordre :
   - [`README.md`](../../README.md)
   - [`docs/DOC-02_Konnaxion_Capsule_Architecture.md`](../../docs/DOC-02_Konnaxion_Capsule_Architecture.md)
   - [`docs/DOC-07_Konnaxion_Security_Gate.md`](../../docs/DOC-07_Konnaxion_Security_Gate.md)
   - [`docs/BRANCH08_RELEASE_ADMISSION.md`](../../docs/BRANCH08_RELEASE_ADMISSION.md)
   - [`pyproject.toml`](../../pyproject.toml)

3. Vérifier les éventuels `AGENTS.md`, manifests, locks, schémas ou décisions de ce checkout; ne pas traiter cette fiche dérivée comme un substitut.
4. Cartographier producteur, consommateur, format, version et permissions pour chaque frontière franchie.

## Procédure adaptée

**Tâche type :** Lire les contrats de build et d’action UI, vérifier les artefacts et signatures, utiliser les prévols sur la cible sans GO LIVE implicite.

**Éviter précisément :** Ne pas exécuter de commande de déploiement, de remplacement VPS ou de reprise de données sur la seule base d’un guide IA.

**Contrôles attendus avant la conclusion :** identifier les tests, validateurs et rapports réellement présents sur le système cible; exécuter uniquement ceux qui sont non destructifs et appropriés; relier chaque résultat à une révision et un environnement précis. En absence de code/test (ou si les sources sont insuffisantes), écrire « non vérifié », jamais « PASS ».

## Utilisation par kOA Doc Server

- **Lecture et export :** n'inclure que les documents explicitement sélectionnés, avec chemins relatifs, empreintes et provenance.
- **RepoSurveyor :** profils mesurés séparément à partir du code local. Ne pas fabriquer `ENGINEERING_PROFILE.md` depuis cette fiche; l'alias français est `PROFIL_INGENIERIE.md` après génération réelle.
- **Diagnostics :** observations datées, preuves historiques et états certifiés restent distincts. Aucune promotion automatique.
- **Wikis et vues :** documents dérivés, jamais propriétaires des contrats ni des permissions.
- **Confidentialité :** filtrer secrets, données personnelles, médias sous droits, caches, backups et rapports privés avant un ZIP de contexte IA.
- **Standard Kristal Framework v11 :** si pertinent, consulter le cadre comme référence possible, sans l'exiger pour naviguer ni forcer une migration du profil historique du dépôt.

## État de cette fiche

`DOCUMENTATION_REDACTIONNELLE_A_REVOIR` : elle facilite le démarrage d'un agent, mais **ne prouve aucun état d'intégration, publication, conformité ni maturité**.
