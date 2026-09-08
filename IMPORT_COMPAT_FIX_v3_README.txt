Konnaxion Capsule Manager - Local Import Compatibility Fix v3
Date: 2026-09-08

Fixes:
- Local Agent storage now honors KX_ROOT from the launcher.
  Local imports use C:\mycode\Konnaxion\runtime instead of C:\opt\konnaxion.
- Agent manifest verification accepts the current Builder kx-capsule-manifest/v1 contract.
- Agent env-template verification accepts canonical ${VAR} runtime references.
- Agent signature verification accepts and cryptographically verifies the Builder JSON Ed25519 signature envelope when the public key is configured.
- Capsule verification reports now include full structured issues/errors/warnings.
- Import errors now expose the verification report through the canonical KX_CAPSULE_IMPORT_ERROR payload.
- StartCapsuleManager.bat automatically exposes the local demo public key to the Agent when it exists.

This package includes the previously-added asynchronous Capsule Build Progress and persistent build logs.

After applying:
1. Close Konnaxion Agent and Capsule Manager terminals.
2. Extract the overlay into C:\mycode\Konnaxion and overwrite files.
3. Relaunch Capsule Manager.
4. Retry Import Capsule using the already-built konnaxion-v14-local-2026.09.08.kxcap.
   Rebuilding the capsule is not required for this compatibility fix.
