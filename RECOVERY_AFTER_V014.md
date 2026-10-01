# Production recovery after v0.1.4

The failed guarded repair deleted `django_migrations` rows for `ekoh`, `smart_vote`, `socialaccount`, `teambuilder`, and `trust`. The subsequent migration failed at `ekoh.0001_initial` because EkoH's real host tables already exist in the canonical `ekoh_smartvote` schema.

Safe recovery strategy:

1. Install v0.1.4 locally and refresh only the Agent source on the Droplet.
2. Verify every expected EkoH and Smart Vote table exists in `ekoh_smartvote`.
3. Restore only the migration-recorder rows for `ekoh` and `smart_vote` from the current migration graph (equivalent to their known pre-repair applied state; the initial `migrate` immediately before the repair had succeeded).
4. Leave `socialaccount`, `teambuilder`, and `trust` pending so Django creates their genuinely missing `public` tables normally.
5. Start through the authenticated Agent with automatic drift repair disabled.
6. Re-run the schema-integrity probe and readiness checks.

Do not rerun the old v0.1.3 repair path before v0.1.4 is installed.
