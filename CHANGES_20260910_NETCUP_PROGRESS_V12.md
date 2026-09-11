# Netcup Progress v12

- Bootstrap now uploads the trusted capsule signing **public** key only to the Droplet.
- Installs it at `/opt/konnaxion/agent/keys/capsule-signing-public.pem`.
- Exports `KX_CAPSULE_PUBLIC_KEY_FILE` in the systemd Agent service.
- Bootstrap fails clearly if the local public key is missing or cannot be copied.
- Deploy Security Gate failures now list blocking check ids when the Agent returns them.
- No Security Gate checks are bypassed.
