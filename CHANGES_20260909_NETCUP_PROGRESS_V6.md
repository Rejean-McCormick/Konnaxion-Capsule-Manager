# Netcup Progress v6 — 2026-09-09

## Fix: remote capsule path when upload is skipped

- `Deploy Droplet` now computes and persists the canonical remote capsule path before the copy decision.
- When `copy_capsule=False`, the remote Agent receives `/opt/konnaxion/capsules/<capsule>.kxcap` instead of the local Windows path.
- Remote import has a defensive fallback that derives a POSIX remote capsule path if deployment state is missing it.
- The skipped-copy progress step now records the exact remote capsule path.
- Added regression test for the exact Netcup failure where Linux interpreted `C:\...` under `/opt/konnaxion/manager`.

## Validation

Focused regression suite: 16 passed.
