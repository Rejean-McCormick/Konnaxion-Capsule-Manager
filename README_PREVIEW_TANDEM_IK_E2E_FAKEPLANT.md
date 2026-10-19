# FakePlant — Préflight IK E2E local (lecture seule)

Cible: Capsule Manager à `C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`.

Lancer `PREVIEW_TANDEM_IK_E2E_FAKEPLANT.cmd` pendant qu'Uvicorn et Celery tournent sous Windows. Orgo API, Worker et PostgreSQL doivent être actifs dans Docker; Redis `konnaxion_ik_redis` doit répondre.

Contrôles: base `neondb`, Universe FakePlant, World `fp-electromecanique-informatique`, release courante d'ID **132**, statut `current` et non dirty; URL et UUID IK Konnaxion, secrets présents sans les révéler, `DEBUG` permettant HTTP de loopback; Redis et worker Celery; API et Worker Orgo; tenant UUID `0c2d7244-a0c0-4a10-8f11-0389f275f133`, 900 personas désactivés, workflow `konnaxion_decision_v1` actif avec version publiée, étiquette de décision valide; endpoints de santé Windows.

Ce contrôle ne crée aucune décision, aucun événement IK, aucun workflow ni jeton; la lecture de la base Orgo se fait dans une transaction `SET TRANSACTION READ ONLY`. L'exécution E2E réelle restera une étape séparée, après validation du préflight. Aucun script du kit historique `prepare-local.ps1` n'est lancé.

Le script n'est pas signé numériquement; le lanceur applique une politique de bypass **au seul processus pwsh**. N'installer que si la provenance du ZIP est acceptable.
