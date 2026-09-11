# Konnaxion Capsule Manager — Netcup Progress v17

Date: 2026-09-10

## Fixes

- Fixes the v16 distribution packaging regression: the generated v16 ZIP omitted the entire `kx_agent/runtime/` package, so installing v16 as an overlay left the previous runtime code in place.
- Ships the complete Capsule Manager source tree, including `kx_agent/runtime/compose.py`.
- Preserves the v16 hardened-runtime fix: existing PostgreSQL and Redis bind-mount data directories are treated as container-owned data roots and are never chmod/chown-normalized by the non-root Agent.
- Preserves the v16 repair of Agent-owned `logs/` and `media/` directories before `/v1/instances/create`.
- Preserves custom-domain routing for `konnaxion.com` plus `www.konnaxion.com` with the Let's Encrypt resolver.

## Safety

- Existing PostgreSQL and Redis data directories are not modified by the Agent permission-normalization path.
- Capsule, Agent token, audit, and systemd hardening changes from v14-v16 remain intact.

## Distribution verification

The v17 archive is verified to contain:

- `kx_agent/runtime/compose.py`
- `kx_agent/runtime/docker.py`
- `kx_agent/runtime/healthchecks.py`
- `kx_agent/runtime/logs.py`
- `kx_agent/runtime/migrations.py`
- `kx_manager/ui/droplet_bootstrap.py`
- `kx_manager/ui/agent_execution_client.py`
