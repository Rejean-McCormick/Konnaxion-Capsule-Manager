# Konnaxion Capsule Manager — Netcup Progress v19

Date: 2026-09-10

## Fixes

- Fixes the UI default-domain upgrade path for existing Manager installations.
- The Manager persists operator state under `KX_ROOT/shared/manager-ui-state.json`; that file could still contain the old `2.56.97.41.sslip.io` demo domain after installing v16-v18.
- On startup, v19 recognizes that exact historical Netcup default and migrates `domain`, `droplet_domain`, and a matching `public_host` to `konnaxion.com`.
- The migrated state is written back, so the old demo hostname does not reappear on the next restart.
- Migration is limited to the configured Netcup VPS (`2.56.97.41`) or state without an explicit Droplet host. Other VPS targets and arbitrary custom domains remain untouched.

## Preserved behavior

- `www.konnaxion.com` remains the automatic public alias at runtime.
- v18 Traefik security headers/HSTS remain unchanged.
- v14-v17 non-root Agent, capsule permissions, runtime-directory ownership handling, and PostgreSQL/Redis protections remain unchanged.
- Existing `.kxcap` artifacts are not modified.
