# Konnaxion Capsule Manager hotfix v0.1.4

Fixes the production schema-integrity guard for host apps that intentionally live outside the public PostgreSQL schema.

## Root cause

The Agent used `connection.introspection.table_names()`, which only sees relations visible on the current PostgreSQL `search_path`. Konnaxion intentionally keeps the normal host connection on `public`, while EkoH and Smart Vote store their canonical host tables in `ekoh_smartvote`. The guard therefore falsely classified those apps as having no tables and could delete their `django_migrations` rows during guarded repair. A rerun of `ekoh.0001_initial` then collided with the already-existing `ekoh_smartvote.expertise_category` table.

## Fix

The integrity probe now queries `pg_catalog` by namespace and compares each app only with its canonical host schema:

- `ekoh` -> `ekoh_smartvote`
- `smart_vote` -> `ekoh_smartvote`
- other host apps -> `public`

World release schemas are deliberately not allowed to satisfy host-runtime table checks.

No database migration is included in this hotfix. Existing databases affected by an already-attempted repair need their deleted migration-recorder rows restored before the Agent retries migrations.
