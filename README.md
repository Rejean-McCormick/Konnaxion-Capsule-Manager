# Konnaxion Capsule Manager — Netcup Clean Rebuild / GO LIVE

New pre-production workflow for a freshly reinstalled Netcup VPS:

- provider-side Format/Reinstall entry point and instructions;
- independent SSH host-key fingerprint scan/verification;
- automatic root-password bootstrap fallback via local `KX_NETCUP_ROOT_PASSWORD` when the fresh image did not receive the selected SSH key;
- automatic `kx-admin` creation with generated strong password;
- password saved only to the local Manager `.env` as `KX_NETCUP_KXADMIN_PASSWORD`;
- trusted SSH public key installation and key-only SSH;
- root SSH and SSH password authentication disabled after `kx-admin`/sudo verification;
- GO LIVE blocked in both UI and backend until the prepared host/user/fingerprint match;
- command failure diagnostics redact URI credentials before persistence.

See `NETCUP_GO_LIVE.md` for the operator workflow.

---

# Konnaxion Capsule Manager Patch v1.12.2

Purpose: make the Konnaxion Worlds data plane configuration persistent in Manager-generated runtime environments.

Changes:
- `DJANGO_ENV_DEFAULTS` now emits:
  - `KONNAXION_WORLDS_DATA_PLANE_ENABLED=true`
  - `KONNAXION_WORLDS_ENFORCE_SCOPED_API=true`
- Primary instance env generation writes those values into both `django.env` and `runtime.env`.
- The fallback compose env writer writes the same values, so bootstrap/recovery paths cannot silently disable Worlds.
- Regression tests cover both the primary and fallback env writers.

Validation performed:
- Python compilation succeeded for all modified source/test files.
- `pytest -q tests/test_runtime_django_env_v14.py tests/test_worlds_data_plane_runtime_v21.py`
- Result: 7 passed.

Apply by extracting this archive over the repository root:
`C:\mycode\Konnaxion\Konnaxion_Capsule_Manager`

This patch changes Manager/Agent source generation behavior. The production instance that was manually fixed already has the data plane enabled; no immediate prod env edit is required just to apply this source patch.
