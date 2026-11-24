<!-- kOA Doc Server · Docs IA · lot 03 · projet : Konnaxion_Capsule_Manager · proposition dérivée à valider -->
# Navigation IA — Konnaxion_Capsule_Manager

**Objet constaté :** Constructeur et gestionnaire de capsules Konnaxion, préparation des installations et opérateur d’admission.

**Frontière de responsabilité :** La construction de capsule, le diagnostic indépendant et le PEP restent séparés; le Manager ne contourne pas les règles de release.

**Source :** `SmartDump_20261120_221507.zip` → `Konnaxion_Capsule_Manager_Code_snapshot.zip` (20 novembre 2026). Le chemin Windows est une déclaration de snapshot, pas une vérification de l'état actuel.

**Statut :** Documentation de navigation dérivée de sources propriétaires, à revoir. Ce document ne vaut ni profil d'ingénierie mesuré, ni certification, ni permission d'exécution.

## Ordre de lecture — fichiers réellement présents dans la capture

1. [`README.md`](../../README.md) — SHA-256 `407275eba6d78827…` dans la capture.
2. [`docs/DOC-02_Konnaxion_Capsule_Architecture.md`](../../docs/DOC-02_Konnaxion_Capsule_Architecture.md) — SHA-256 `ec7f2f524c76cb47…` dans la capture.
3. [`docs/DOC-07_Konnaxion_Security_Gate.md`](../../docs/DOC-07_Konnaxion_Security_Gate.md) — SHA-256 `d751f2c2947a8af1…` dans la capture.
4. [`docs/BRANCH08_RELEASE_ADMISSION.md`](../../docs/BRANCH08_RELEASE_ADMISSION.md) — SHA-256 `70c63095be6a229b…` dans la capture.
5. [`pyproject.toml`](../../pyproject.toml) — SHA-256 `6fafe2365286cd36…` dans la capture.

## Parcours recommandé pour une intervention assistée par IA

1. Identifier la question, le sous-système concerné et les responsabilités du propriétaire.
2. Lire les documents ci-dessus **dans cet ordre**; consulter les contrats et consignes propriétaires plus récents si le checkout a évolué.
3. Lire les contrats de build et d’action UI, vérifier les artefacts et signatures, utiliser les prévols sur la cible sans GO LIVE implicite.
4. Établir les vérifications effectivement disponibles dans la révision locale, consigner les résultats et ne conclure qu'à l'intérieur du périmètre couvert.
5. Proposer les changements; demander une décision explicite avant toute écriture à effet, déploiement, publication ou promotion de preuves.

## Risque propre à ce dépôt

Ne pas exécuter de commande de déploiement, de remplacement VPS ou de reprise de données sur la seule base d’un guide IA.

## Compléments

- [`AI_CONTEXT.md`](AI_CONTEXT.md) : règles de travail et frontières de confiance.
- `AGENTS.md` : s'il existe déjà chez le propriétaire, ses consignes priment sur ces propositions; sinon l'overlay peut en ajouter un sans remplacement.
- `PROFIL_INGENIERIE.md` : optionnel, uniquement après exécution réelle de RepoSurveyor sur un dépôt local et accord explicite pour sa copie.
- Le Framework Kristal v11 est un standard documentaire **facultatif** : lire le profil réellement verrouillé, sans imposer de migration.
