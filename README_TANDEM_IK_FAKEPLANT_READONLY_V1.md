# Diagnostic Interaction Kernel FakePlant (lecture seule)

Déposer cette archive à la **racine** de `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`.

Lancer :

```powershell
cd C:\mycode\Konnaxion\Konnaxion_Capsule_Manager
.\START_TANDEM_IK_FAKEPLANT_DIAG.cmd
```

Ce diagnostic interroge les réglages **Django réellement chargés** (Konnaxion avec `uv`, PostgreSQL `neondb`), l'API **et le Worker Orgo Docker**, et vérifie en transaction SQL **READ ONLY** l'organisation fixe `fakeplant`, le workflow actif et la présence de jetons avec portées IK. Il effectue aussi trois requêtes de santé GET. Il ne lit/affiche aucun secret ou mot de passe, ne crée aucun fichier et ne démarre pas de service.

## Constats issus des snapshots du 18 octobre 2026

- Contrat IK: `ik/1.1`; Konnaxion émet `governance.decision.execute/1.0.0`; Orgo répond avec un `Signal`, puis, via son Worker, publie `accountability.impact.publish/1.0.0` vers Konnaxion.
- Konnaxion API entrante : `POST /api/integrations/ik/konnaxion/interactions/`, `IK_ORGO_INBOUND_TOKEN`.
- Orgo API entrante : `POST /api/v3/ik/interactions`, authentification Orgo par token d'API ayant au moins `signals:write` et `workflows:execute` pour la réception de la décision.
- Orgo **Worker**, et non API, charge `KONNAXION_IK_URL` et `KONNAXION_IK_TOKEN` dans le Docker Compose.
- Les 900 personas FakePlant sont non connectables. Le jeton FP-S01 scopes `work:read,work:write` ne suffit pas pour IK; ne pas le modifier ni le réutiliser.
- Pour la décision gouvernée, Orgo exige un workflow actif dont le code est `KONNAXION_DECISION_WORKFLOW_CODE`. Le dernier état vérifié était **0 workflow publié**.
- Le script existant `Orgo/validation/konnaxion-orgo-ik/prepare-local.ps1` est **incompatible avec la topologie actuelle** : il démarre Docker pour Konnaxion, applique migrations et seed, et manipule les credentials. **Ne pas l'exécuter** sur les bases actuelles. `run-e2e.ps1` suppose aussi Konnaxion Docker et ne tient pas compte du scope de World strict.
- Le prochain test IK devra créer une vraie `DecisionRecord` de qualification dans un World FakePlant explicite, publier sa version avec empreinte et joindre les receipts; la création de dossier+tâche FP-S01 ne remplace pas ce test.

## Limites

Ce diagnostic est **sans POST**, n'accorde aucune permission et ne prouve aucune livraison IK, SSO humain, ni déploiement. Même avec des réglages `OK`, un test E2E isolé reste nécessaire. Si un service est arrêté, le diagnostic le signale sans le démarrer.
