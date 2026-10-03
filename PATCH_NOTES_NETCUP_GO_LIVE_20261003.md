# Netcup GO LIVE provisioning patch — 2026-10-03

Adds a fail-closed clean-rebuild workflow to Capsule Manager for Netcup production hosts.

- True disk format / OS reinstall remains in Netcup SCP.
- Host-key scan with independent SHA256 fingerprint verification.
- Creates `kx-admin`, strong generated password, trusted SSH key and sudo access.
- Verifies `kx-admin` before disabling root SSH and password SSH authentication.
- Stores the generated password only in the local gitignored Manager `.env` as `KX_NETCUP_KXADMIN_PASSWORD`.
- Persists host/user/fingerprint preparation markers and blocks GO LIVE in both UI and backend until they match the current target.
- Adds Netcup actions/forms/routes and a dedicated deploy-page workflow.
- Redacts URI credentials in command-failure diagnostics and rejects malformed PostgreSQL URLs containing literal backslashes.
- Feature/one-click/UI/security regression set: 121 passed, 2 skipped.
