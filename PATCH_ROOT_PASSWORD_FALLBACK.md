# Netcup root-password bootstrap fallback

Capsule Manager now supports the fresh-VPS case where Netcup did not inject the selected SSH public key.

1. Put the temporary/root VPS password in the local Manager `.env` as `KX_NETCUP_ROOT_PASSWORD`.
2. Run **Provision Fresh Netcup VPS** again.
3. Manager first tries root key auth. If that fails, it uses the `.env` password through Paramiko with the already-verified SSH host key, installs the selected public key in `/root/.ssh/authorized_keys`, verifies key login, then continues the existing `kx-admin` provisioning and SSH hardening workflow.

The root password is never put on a command line or returned in action payloads. The repository already gitignores `.env` and `.env.*` (except example files).

## Fix 2

Fixed the local SSH runner so scripted SSH input uses `subprocess.run(input=...)` without also passing `stdin=PIPE`. This removes the Windows/Python error `stdin and input arguments may not both be used.`
