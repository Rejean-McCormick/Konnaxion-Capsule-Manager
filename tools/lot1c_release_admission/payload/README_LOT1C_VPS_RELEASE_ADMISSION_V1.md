# Lot 1C — admission de release VPS Linux / Docker / Capsule Agent (V1)

## Périmètre réel de ce correctif

Ce lot modifie **le code local du dépôt `Konnaxion_Capsule_Manager` seulement**.
Il n'installe pas KonnaxionDiag sur Linux, ne pousse rien sur un VPS, ne signe
aucune release, ne lance pas Docker et ne modifie aucune base ni aucun tenant.
Il complète le **Lot 1A** et consomme le vérificateur **KonnaxionDiag 4.2.5** du **Lot 1B**.

### Ce que l'Agent fait lorsqu'une admission est requise

Pour `instance.start`, après le Security Gate et le chargement local des images,
**avant** `docker compose down`, les migrations et `docker compose up`, l'Agent :

1. Lit une politique d'admission **propre à l'Agent**, non fournie par la requête HTTP :
   `/etc/konnaxion/release-admission/<instance-id>.json`.
2. Mesure le Git commit/tree d'une arborescence propre, les octets du manifeste
   de capsule, les composants `runtime_pack`, `policy_bundle`, `infra_manifest`,
   `sbom`, `provenance` et les identités Docker des images du Compose effectif.
3. Exige `image: registre/produit@sha256:<digest>` pour chaque service et interdit
   les constructions `build` du Compose de production. Les identifiants sont
   obtenus au moyen de `docker image inspect` (pas de la requête utilisateur).
4. Reconstruit `identity` et son SHA-256 canonique et vérifie son égalité exacte
   avec le ReleaseSet. Toute absence ou différence bloque le démarrage.
5. Appelle la **vraie fonction** `assurance.pep.verify_activation_admission()`
   de KonnaxionDiag 4.2.5 avec la clé publique de l'autorité approuvée,
   l'empreinte mesurée et la version de politique imposée par l'Agent.
6. Exécute `docker compose up --no-build` uniquement si admis.

Les appels `backup.test_restore` destinés à un profil public sont **bloqués**
provisoirement : cette opération démarre elle aussi des conteneurs et doit
bénéficier d'une admission propre à sa cible isolée.

### Conditions de déclenchement

L'admission est obligatoire pour le réseau d'instance `public_vps`,
`public_temporary`, `KX_PUBLIC_MODE_ENABLED`, ou un Agent qui a un mode public.
Sur un Agent final, imposer en plus, dans la configuration **système de son
service Linux** (non dans une requête API) :

```
KX_RELEASE_ACTIVATION_MODE=enforce
```

Ce mode s'applique même si l'instance a un profil réseau privé. Toute autre
valeur explicite est rejetée. Le profil FakePlant `local_only` sans mode
`enforce` conserve les contrôles du Lot 1A sans réclamer de ReleaseSet de
production : son E2E technique n'est pas rejoué par cette mise à jour.

### Exemple structurel de politique d'admission Linux

**Ce fichier est un modèle de documentation, pas une politique à activer** :
les chemins, les artefacts et les clés doivent exister et être préparés par
l'opérateur et par le Builder / la chaîne de release approuvée.

```json
{
  "schema": "koa.agent.release-admission.v1",
  "instance_id": "instance-prod",
  "policy_version": "konnaxion-security-policy-v1",
  "deployment_root": "/opt/konnaxion/releases/<RELEASE_ID>",
  "source_marker": "source/README.md",
  "capsule_manifest": "capsule/manifest.json",
  "release_set": "release-set.json",
  "verdict": "release-verdict.json",
  "signature": "release-verdict.sig",
  "trusted_public_keys": ["/etc/konnaxion/trust/kdiag-release.pub"]
}
```

Le répertoire et le fichier sous `/etc/konnaxion/release-admission` doivent
appartenir à root ou à l'utilisateur de service de confiance, et ne pas être
modifiables par le groupe/autres. Les clés publiques doivent être protégées.
Les clés **privées** de signature restent hors du VPS et de Capsule Manager.
Installer KonnaxionDiag 4.2.5 dans le Python du **Capsule Agent Linux**.

Les fichiers du ReleaseSet doivent être réellement présents au même emplacement
relatif que dans `component_files`, et les digests du manifeste et des images
signés doivent correspondre aux octets/IDs mesurés. Les builds actuels qui
utilisent des tags Docker mutables, n'embarquent pas les composants complets
ou ne correspondent pas à cette représentation sont **volontairement bloqués**.
Il reste à adapter/qualifier le Builder, le staging d'une release candidate,
les chemins complets de déploiement composite, et les tests sur VPS.

### Limites et barrières non franchies

- Lot 1C validé **dans les tests sur copie du code**, pas sur l'Agent du VPS.
- L'Agent de production n'est pas actualisé. Les vraies clés et preuves ne sont
  pas provisionnées et aucune admission de production n'est constatée.
- L'admission de `instance.start` ne qualifie pas `instance.restore`,
  `instance.rollback`, la migration d'un runtime composite Konnaxion+Orgo ou
  la résistance à toute évolution du cycle de vie. Ces chemins restent des
  barrières de qualification avant GO LIVE.
- `ReleaseSet` complet, images digest-pinned et provenance doivent être
  préparés **par les dépôts propriétaires**, pas inventés à partir de
  métadonnées remises par le Manager.

## Installation locale sur Windows / PS7

Extraire le ZIP **à la racine réelle** :

`C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`

Puis :

```powershell
cd 'C:\mycode\Konnaxion\Konnaxion_Capsule_Manager'
.\PREVIEW_LOT1C_RELEASE_ADMISSION.cmd
.\INSTALL_LOT1C_RELEASE_ADMISSION.cmd
.\VERIFY_LOT1C_RELEASE_ADMISSION.cmd
```

Pour revenir exactement au code antérieur si le dépôt n'a pas été modifié
entre-temps : `ROLLBACK_LOT1C_RELEASE_ADMISSION.cmd`.

L'installateur vérifie les SHA-256 du snapshot **22 h 56**, teste sur une copie
isolée, sauvegarde chaque fichier remplacé, réutilise l'installation si elle
est déjà conforme et refuse tout contenu inattendu. Les sauvegardes et les
journaux sont conservés dans `Tandem_Local/lot1c_release_admission` à côté du dépôt.

## État du programme global

**Lot 1A :** validé sur ton Windows (40 tests).
**Lot 1B :** validé sur ton Windows (71 tests).
**C0 VPS :** validé sur ton Windows (19 tests).
**Lot 1C code local :** à installer chez toi, testé sur le snapshot fourni.
**Admission et Agent VPS :** non qualifiés à ce jour.
