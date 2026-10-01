# Konnaxion Capsule Manager v0.1.3 hotfix

Fixes GO LIVE update deployments where the Windows-local `capsule_path` alias was forwarded to the Linux Droplet Agent.

Changes:
- Droplet update always prefers `remote_capsule_path` or derives `/opt/konnaxion/capsules/<filename>.kxcap`.
- Windows capsule filenames are parsed portably on any host OS.
- Agent capsule-id derivation normalizes both `\\` and `/` separators defensively.
- Retains v0.1.2 update-contract compatibility (`create_pre_update_backup`).

Validation:
- Python compileall: PASS
- Targeted update/path/deploy tests: 33 passed
