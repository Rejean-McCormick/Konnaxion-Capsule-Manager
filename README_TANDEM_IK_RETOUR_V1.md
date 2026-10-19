# FakePlant IK — liaison Orgo Worker → Konnaxion local

Cible : extraire ce ZIP directement dans `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`.

- `PREVIEW_TANDEM_IK_RETOUR.cmd` : contrôle sans écriture.
- `CONFIG_TANDEM_IK_RETOUR.cmd` : copie **sans l'afficher** le secret `KONNAXION_IK_TOKEN` déjà présent dans le conteneur Worker Orgo vers `IK_ORGO_INBOUND_TOKEN` dans `Konnaxion\backend\.env`. **Écrit uniquement ce fichier local**, jamais les bases. Aucun jeton nouveau. Refuse les valeurs divergentes, une URL Worker non canonique, les tokens courts et, si le fichier DPAPI FP-S01 existe, l'égalité avec le jeton FP-S01.
- Redémarrer manuellement Uvicorn Konnaxion pour charger `.env`.
- `TEST_TANDEM_IK_RETOUR.cmd` : POST d'une enveloppe **volontairement invalide** `{}` de Worker Docker vers Django. Réussite : 400 `IK_UNSUPPORTED_PROTOCOL`, démontrant que le Bearer est accepté mais qu'aucune écriture n'a lieu.

Cette phase ne configure PAS `IK_ORGO_TOKEN`, ne crée PAS le jeton Orgo `signals:write/workflows:execute`, ne publie PAS le workflow de décision et ne qualifie PAS l'E2E. Ne pas lancer l'ancien `prepare-local.ps1` (Docker/migrations/seed). Ne pas activer SSO ou prod.

**Sécurité :** ces scripts sont prévus uniquement pour le poste Windows local avec Orgo Docker et Konnaxion `uv`; ils ne montrent ni n'enregistrent les secrets dans les journaux. Le secret est persisté dans `.env` conformément à la configuration Django existante. Ne pas partager ce fichier. Aucune confirmation à retaper.
