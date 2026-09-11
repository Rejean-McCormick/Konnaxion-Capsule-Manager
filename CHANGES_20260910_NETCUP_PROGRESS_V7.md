# Netcup Progress v7 — 2026-09-10

## Fix: redundant remote-runtime SSH stall

- When `Deploy Droplet` is run with `copy_capsule=False` because the dedicated Copy Capsule step already succeeded, the deployment no longer opens an extra SSH connection only to repeat `mkdir -p` on the remote runtime tree.
- The skipped runtime-preparation step is recorded explicitly in the operation result and progress log, including the canonical remote capsule path.
- Required remote Agent health/contract/import/security/start steps still run normally; no security gate is bypassed.

## SSH hardening for automated Manager commands

- Automated SSH commands now explicitly disable TTY allocation (`-T`).
- Public-key authentication is forced; password and keyboard-interactive fallbacks are disabled.
- Non-streaming subprocess commands receive `stdin=DEVNULL`, preventing the GUI-launched OpenSSH process from inheriting the Manager console input handle and waiting on it.
- Streaming capsule upload keeps its dedicated stdin pipe and remains compatible with byte-level progress reporting.

## Validation

Focused regression suite: 11 passed.
