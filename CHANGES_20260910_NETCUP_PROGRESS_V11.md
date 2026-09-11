# Netcup Progress v11 — 2026-09-10

## Fixes

- Droplet Bootstrap now ensures Docker Engine and Docker Compose are installed before the Agent is restarted.
  - Installs `docker.io` when Docker is absent.
  - Tries Debian Compose package names in order: `docker-compose-v2`, `docker-compose-plugin`, then `docker-compose`.
  - Enables/starts Docker and verifies both Docker and Compose before reporting bootstrap success.
- Operational capsule selection no longer advances to a nonexistent capsule merely because the calendar date changed.
  - Explicit `KX_CAPSULE_*` environment overrides still win.
  - Persisted capsule selection is no longer overwritten by automatic date naming on Manager startup.
  - Droplet operations prefer the newest existing `.kxcap` in the configured capsule output folder when the selected path does not exist.
  - When a real capsule is selected, Droplet hidden `capsule_id` / `capsule_version` values are aligned with the artifact filename for the canonical dated local naming scheme.
  - Verify/Import capsule forms also suggest the newest existing capsule instead of a date-derived missing file.
  - Build Capsule keeps today's generated ID/version as the new-build default.

## Validation

Focused regression suite: 17 passed.
Python compileall for kx_manager, kx_agent, kx_builder, and kx_shared: passed.
