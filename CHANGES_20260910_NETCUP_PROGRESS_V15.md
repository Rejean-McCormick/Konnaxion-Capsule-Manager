# Netcup Progress v15 - hardened Agent capsule handoff permissions

This release fixes the first deployment regression exposed after v14 moved the
remote Agent from root to the dedicated `kx-agent` service identity.

## Root cause

Older Manager releases could leave a staged `.kxcap` in `/opt/konnaxion/capsules`
as a root-only file. v14 correctly starts the Agent as `kx-agent`, but the
bootstrap migrated the capsule directory without normalizing permissions on
already-existing capsule files. The Agent therefore reached `/capsules/import`
and failed with `EACCES` while opening the staged capsule.

## Fix

- Bootstrap migrates existing top-level `.kxcap` transfer artifacts to
  `root:kx-agent` mode `0640` while keeping the capsule directory Agent-owned.
- Droplet import now performs a narrow SSH preflight immediately before the
  Agent API call, so an already-copied legacy capsule is repaired even when
  `copy_capsule=false`.
- A freshly uploaded capsule is normalized immediately after transfer.
- The permission helper refuses paths outside the configured remote capsule
  directory and only accepts direct `.kxcap` children.
- The preflight proves `kx-agent` can read the capsule before import proceeds.
- No PostgreSQL, Redis, instance data, signing private key, or unrelated remote
  path permissions are changed.

## Validation

31 targeted deployment/bootstrap/auth/security tests pass, including new v15
coverage for legacy root-only capsules, path confinement, skipped-copy deploys,
and post-upload permission normalization.
