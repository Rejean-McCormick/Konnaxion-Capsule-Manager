# Netcup Progress v16 — runtime ownership + real domain/TLS

## Fixed

- Migrates only Agent-owned runtime bind directories (`logs`, service log dirs, and `media`) from legacy `root` ownership to the hardened `kx-agent` service identity.
- Deploy also performs the same narrow runtime permission repair over the privileged SSH bootstrap channel immediately before `/instances/create`, so an already-running instance can self-heal without touching PostgreSQL/Redis ownership.
- Existing PostgreSQL and Redis data roots are no longer chmodded by runtime rendering when container UIDs own them.
- Operator Netcup domain default is now `konnaxion.com` instead of the temporary `2.56.97.41.sslip.io` hostname.
- Custom apex domains automatically include their conventional `www` alias; the Manager sends `host_aliases` during instance creation and the Agent consumes them.
- Traefik therefore renders `Host(konnaxion.com) || Host(www.konnaxion.com)` and uses the existing `letsencrypt` resolver for TLS.
- The packaged `.kx-ui/manager-ui-state.json` was reset from test-generated values to the real Netcup operator target and existing 2026.09.09 capsule.
- `KX_Diagnose_Online.ps1` now targets `konnaxion.com`.

## Safety

- Runtime permission repair is constrained to `/opt/konnaxion/instances/<safe-instance-id>/{logs,media}` and immediate service log directories.
- PostgreSQL and Redis data ownership is explicitly excluded.
- No weakening of capsule signature, Security Gate, Agent auth, or systemd hardening.

## Validation

- Python compileall: PASS.
- v16 + v15/v14 regression tests: 24 PASS.
- public_vps host-alias profile contract checks: 2 PASS.
