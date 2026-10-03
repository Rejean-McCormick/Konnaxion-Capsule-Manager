# Netcup clean rebuild → GO LIVE

This workflow is designed for a fresh Netcup VPS after a suspected or confirmed compromise.

## 1. Format / reinstall in Netcup SCP

The destructive disk format and official OS image installation remain provider-side operations. Stop the server in Netcup SCP, format/reinstall the main disk with a supported Debian image, and select the trusted SSH public key that Capsule Manager will use.

Do **not** treat an SSH cleanup (`rm`, package removal, user deletion) as equivalent to a clean rebuild after compromise.

## 2. Verify the fresh SSH host key

In Capsule Manager → Deploy → **Netcup Clean Rebuild + kx-admin**:

1. Run **Scan Fresh VPS Host Key**.
2. In the independent Netcup console/VNC, run:

   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256`

3. Copy the verified `SHA256:...` fingerprint into the provisioning form.

Capsule Manager stores only the exact scanned key whose SHA256 fingerprint matches the independently verified value.

## 3. Provision `kx-admin`

**Provision Fresh Netcup VPS**:

- verifies root SSH using the selected public key;
- creates `kx-admin`;
- generates a strong random local password;
- installs the same public key for `kx-admin`;
- verifies non-interactive sudo before root SSH is disabled;
- disables SSH root login and SSH password authentication;
- verifies the hardened SSH configuration through `kx-admin`;
- writes the local Manager `.env` with mode `0600` where supported.

The generated password is saved as:

`KX_NETCUP_KXADMIN_PASSWORD`

It is intentionally **not** returned in the action result and is not written to operation logs. SSH authentication remains key-only; the password is retained locally for console/recovery use as requested by the operator.

The `.env` also records the prepared host, user, verified fingerprint, SSH key path, port, domain/instance paths, and a preparation timestamp.

## 4. GO LIVE gate

For a target named as Netcup, GO LIVE is fail-closed until all of these match the current target:

- `KX_NETCUP_VPS_PREPARED=true`
- prepared host equals the current VPS IP/host
- prepared user equals `kx-admin`
- a valid verified `KX_NETCUP_SSH_HOST_FINGERPRINT` exists

This check exists in both the Dashboard UI **and** the one-click release service, so direct/background calls cannot bypass the preflight.

## 5. Secret handling

The local `.env` is gitignored by this repository. Keep it on the trusted Manager workstation only, back it up as a secret, and never attach it to diagnostics or support bundles. After a compromise, rotate all old application, database, SMTP, API, signing, and provider credentials independently of this VPS account password.
