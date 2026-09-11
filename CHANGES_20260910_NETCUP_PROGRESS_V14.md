# Netcup Progress v14 - Agent service boundary hardening

This release hardens the remote Konnaxion Agent after the first real SecurityDiag VPS run.

## Dedicated service identity

- Bootstrap creates the non-login system account/group `kx-agent`.
- `konnaxion-agent.service` now runs with `User=kx-agent` and `Group=kx-agent`.
- Docker access is granted only to the systemd service with `SupplementaryGroups=docker`; `kx-agent` is not persistently added to the Docker group.
- The service starts the installed virtualenv entry point directly instead of depending on root's `uv` binary.
- Existing instance migration changes ownership only for Agent-owned `env`/`state` control paths; PostgreSQL/Redis runtime data is not recursively chowned.

## Local Agent authentication

- Bootstrap generates a strong Manager-Agent bearer token at `/opt/konnaxion/manager/agent.token` when absent and preserves it across Agent refreshes.
- Token mode is `0600`, owned by `kx-agent:kx-agent`.
- Production Agent write operations require the token; unauthenticated writes return HTTP 401.
- SSH-local Manager calls read the token only on the Droplet and pass it to curl through a temporary root-only header file. The token value is never copied back to Windows or embedded in the SSH command text.
- Bootstrap self-tests the boundary: an unauthenticated write must return 401 and an authenticated malformed write must reach validation (422).
- Local Manager development remains compatible because token enforcement is enabled by the remote systemd environment, not globally by default.

## Audit evidence

- Bootstrap provisions `/opt/konnaxion/agent/audit/agent-audit.jsonl` as `0600`, owned by `kx-agent:kx-agent`.
- Authenticated privileged API writes append redacted `started`/`succeeded`/`failed` events; rejected authentication attempts append `blocked` events.
- If mandatory audit logging is unavailable before a write, the Agent refuses the operation.

## systemd hardening

The remote service now declares:

- `NoNewPrivileges=yes`
- `PrivateTmp=yes`
- `ProtectHome=yes`
- `ProtectSystem=full`
- `RestrictSUIDSGID=yes`
- `LockPersonality=yes`
- `UMask=0077`
- explicit writable Konnaxion/Docker-socket paths

Bootstrap fails closed if the expected service user, hardening flags, token permissions, audit permissions, Docker access, health endpoint, or authentication boundary is not confirmed.

## Scope

This release targets SecurityDiag S09 Agent-boundary findings. It intentionally does not disable root SSH or enable UFW automatically; SSH/firewall hardening is a separate step so the operator cannot be locked out during migration.
