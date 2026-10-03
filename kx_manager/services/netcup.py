"""Netcup fresh-VPS provisioning for Capsule Manager.

The destructive disk format / OS reinstall remains a provider-side SCP action.
This module starts only after the operator confirms a fresh Netcup image and
verifies the new SSH host-key fingerprint.  It then creates the canonical
``kx-admin`` operator account, installs the selected SSH key, generates a strong
console/sudo password, hardens SSH, and persists GO LIVE connection details in
the local Manager .env without exposing the generated password in action logs.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import shlex
import string
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dotenv import find_dotenv, set_key


NETCUP_ADMIN_USER = "kx-admin"
NETCUP_SCP_URL = "https://www.servercontrolpanel.de/scp-ui/"
NETCUP_MEDIA_DOC_URL = "https://www.netcup.com/en/helpcenter/documentation/server/media"
_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/=]+$")
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class NetcupProvisionError(RuntimeError):
    """Raised when a fresh Netcup VPS cannot be provisioned safely."""


def _hidden_process_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"creationflags": flags} if flags else {}


def _run(
    argv: list[str],
    *,
    timeout_seconds: int = 30,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        input=input_bytes,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
        shell=False,
        **_hidden_process_kwargs(),
    )


def _text(value: bytes | None) -> str:
    return (value or b"").decode("utf-8", errors="replace").strip()


def _require_executable(name: str) -> None:
    import shutil

    if shutil.which(name) is None:
        raise NetcupProvisionError(f"Required OpenSSH tool is missing: {name}")


def _normalize_fingerprint(value: Any) -> str:
    text = str(value or "").strip()
    if not _FINGERPRINT_RE.fullmatch(text):
        raise NetcupProvisionError(
            "SSH host fingerprint must use the SHA256:... format shown by ssh-keygen."
        )
    return text


def _validate_username(value: Any) -> str:
    user = str(value or NETCUP_ADMIN_USER).strip()
    if not _USERNAME_RE.fullmatch(user):
        raise NetcupProvisionError("Admin username is invalid.")
    if user in {"root", "kx-agent"}:
        raise NetcupProvisionError("Admin username must be a dedicated non-root operator account.")
    return user


def _ssh_scan(host: str, port: int) -> str:
    _require_executable("ssh-keyscan")
    result = _run(
        ["ssh-keyscan", "-T", "10", "-p", str(port), host],
        timeout_seconds=20,
    )
    output = "\n".join(
        line for line in _text(result.stdout).splitlines() if line and not line.startswith("#")
    ).strip()
    if result.returncode not in {0, 1} or not output:
        raise NetcupProvisionError(
            "Could not read the fresh VPS SSH host key. Confirm the Netcup image install is finished and SSH is reachable."
        )
    return output + "\n"


def _fingerprints_from_keyscan(keyscan_text: str) -> list[dict[str, str]]:
    _require_executable("ssh-keygen")
    fd, name = tempfile.mkstemp(prefix="kx-netcup-host-", suffix=".pub")
    os.close(fd)
    path = Path(name)
    try:
        path.write_text(keyscan_text, encoding="utf-8")
        result = _run(
            ["ssh-keygen", "-E", "sha256", "-lf", str(path)],
            timeout_seconds=10,
        )
        if result.returncode != 0:
            raise NetcupProvisionError(
                f"Could not calculate SSH host fingerprint: {_text(result.stderr)}"
            )
        values: list[dict[str, str]] = []
        for line in _text(result.stdout).splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1].startswith("SHA256:"):
                values.append(
                    {
                        "bits": parts[0],
                        "fingerprint": parts[1],
                        "key_type": parts[-1].strip("()"),
                    }
                )
        if not values:
            raise NetcupProvisionError("No SSH SHA256 fingerprint was returned.")
        return values
    finally:
        path.unlink(missing_ok=True)


def scan_host_fingerprints(payload: Mapping[str, Any]) -> dict[str, Any]:
    host = str(payload.get("droplet_host") or payload.get("host") or "").strip()
    if not host:
        raise NetcupProvisionError("Droplet Host / IP is required.")
    port = int(payload.get("ssh_port") or 22)
    keyscan = _ssh_scan(host, port)
    fingerprints = _fingerprints_from_keyscan(keyscan)
    return {
        "ok": True,
        "message": "Netcup VPS host key scanned. Verify one fingerprint in the Netcup console before provisioning.",
        "data": {
            "droplet_host": host,
            "ssh_port": port,
            "fingerprints": fingerprints,
            "verification_command": "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256",
        },
    }


def _known_hosts_file() -> Path:
    path = Path.home() / ".ssh" / "known_hosts"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _trust_verified_host(host: str, port: int, keyscan_text: str, expected: str) -> Path:
    # Trust only the exact key line whose SHA256 fingerprint the operator
    # independently verified. Do not add every key returned by ssh-keyscan: an
    # attacker who could inject an additional host key must not gain trust just
    # because one legitimate public key was also observable.
    matching_lines: list[str] = []
    for line in keyscan_text.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        try:
            fingerprints = _fingerprints_from_keyscan(candidate + "\n")
        except NetcupProvisionError:
            continue
        if expected in {item["fingerprint"] for item in fingerprints}:
            matching_lines.append(candidate)

    if not matching_lines:
        raise NetcupProvisionError(
            "Fresh VPS SSH fingerprint does not match the fingerprint confirmed in Netcup SCP/VNC."
        )

    known_hosts = _known_hosts_file()
    target = host if port == 22 else f"[{host}]:{port}"
    _run(["ssh-keygen", "-R", target, "-f", str(known_hosts)], timeout_seconds=10)
    with known_hosts.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(matching_lines) + "\n")
    return known_hosts


def _ssh_argv(
    *,
    host: str,
    user: str,
    key_path: Path,
    port: int,
    known_hosts: Path,
) -> list[str]:
    return [
        "ssh",
        "-T",
        "-i",
        str(key_path),
        "-p",
        str(port),
        "-o",
        "BatchMode=yes",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        f"{user}@{host}",
    ]


def _ssh_script(
    *,
    host: str,
    user: str,
    key_path: Path,
    port: int,
    known_hosts: Path,
    script: str,
    timeout_seconds: int = 180,
) -> subprocess.CompletedProcess[bytes]:
    argv = _ssh_argv(
        host=host,
        user=user,
        key_path=key_path,
        port=port,
        known_hosts=known_hosts,
    ) + ["bash -s"]
    return _run(argv, timeout_seconds=timeout_seconds, input_bytes=script.encode("utf-8"))


def _ssh_command(
    *,
    host: str,
    user: str,
    key_path: Path,
    port: int,
    known_hosts: Path,
    command: str,
    timeout_seconds: int = 30,
) -> subprocess.CompletedProcess[bytes]:
    argv = _ssh_argv(
        host=host,
        user=user,
        key_path=key_path,
        port=port,
        known_hosts=known_hosts,
    ) + [command]
    return _run(argv, timeout_seconds=timeout_seconds)


def _public_key_for_private_key(private_key: Path) -> str:
    public_path = Path(str(private_key) + ".pub")
    if public_path.is_file():
        value = public_path.read_text(encoding="utf-8").strip()
        if value:
            return value

    _require_executable("ssh-keygen")
    result = _run(["ssh-keygen", "-y", "-f", str(private_key)], timeout_seconds=15)
    if result.returncode != 0:
        raise NetcupProvisionError(
            "Could not derive the SSH public key. If the private key is encrypted, create the matching .pub file first."
        )
    value = _text(result.stdout)
    if not value:
        raise NetcupProvisionError("SSH public key is empty.")
    return value


def _new_admin_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#%+=_-"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in value)
            and any(c.isupper() for c in value)
            and any(c.isdigit() for c in value)
            and any(c in "!@#%+=_-" for c in value)
        ):
            return value


def _operator_env_path() -> Path:
    explicit = os.getenv("KX_MANAGER_ENV_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    try:
        import kx_manager

        loaded = str(getattr(kx_manager, "_LOADED_OPERATOR_ENV_FILE", "") or "").strip()
    except Exception:
        loaded = ""
    if loaded:
        return Path(loaded).expanduser().resolve()

    discovered = find_dotenv(filename=".env", usecwd=True)
    if discovered:
        return Path(discovered).resolve()

    return (Path.cwd() / ".env").resolve()


def _persist_operator_env(values: Mapping[str, Any]) -> Path:
    path = _operator_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# Konnaxion Capsule Manager operator environment\n", encoding="utf-8")
    for key, value in values.items():
        text = str(value)
        set_key(str(path), key, text, quote_mode="always")
        os.environ[key] = text
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def provision_fresh_vps(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Provision a freshly reinstalled Netcup Debian VPS for GO LIVE.

    Preconditions:
    - the destructive format/reinstall has already been completed in Netcup SCP;
    - the selected local SSH public key was injected into the fresh image;
    - the operator independently verified the SSH host-key fingerprint in SCP/VNC.
    """

    if str(payload.get("reinstalled_confirmed") or "").strip().lower() not in {"1", "true", "yes", "on", "checked"}:
        raise NetcupProvisionError("Confirm that the Netcup disk was formatted/reinstalled before provisioning.")
    if str(payload.get("confirmed") or "").strip().lower() not in {"1", "true", "yes", "on", "checked"}:
        raise NetcupProvisionError("Explicit provisioning confirmation is required.")

    _require_executable("ssh")
    host = str(payload.get("droplet_host") or payload.get("host") or "").strip()
    if not host:
        raise NetcupProvisionError("Droplet Host / IP is required.")
    port = int(payload.get("ssh_port") or 22)
    key_path = Path(str(payload.get("ssh_key_path") or "")).expanduser().resolve()
    if not key_path.is_file():
        raise NetcupProvisionError(f"SSH private key not found: {key_path}")
    admin_user = _validate_username(payload.get("admin_user") or NETCUP_ADMIN_USER)
    expected_fingerprint = _normalize_fingerprint(payload.get("ssh_host_fingerprint"))
    domain = str(payload.get("domain") or "").strip()

    keyscan = _ssh_scan(host, port)
    known_hosts = _trust_verified_host(host, port, keyscan, expected_fingerprint)

    root_probe = _ssh_command(
        host=host,
        user="root",
        key_path=key_path,
        port=port,
        known_hosts=known_hosts,
        command="id -u && test \"$(id -u)\" = 0",
        timeout_seconds=30,
    )
    if root_probe.returncode != 0:
        raise NetcupProvisionError(
            "Fresh root SSH login with the selected key failed. During Netcup image installation, select/import this SSH public key."
        )

    public_key = _public_key_for_private_key(key_path)
    password = _new_admin_password()
    password_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
    public_key_b64 = base64.b64encode(public_key.encode("utf-8")).decode("ascii")
    admin_q = shlex.quote(admin_user)

    create_script = f"""set -euo pipefail
ADMIN={admin_q}
PASSWORD_B64={shlex.quote(password_b64)}
PUBKEY_B64={shlex.quote(public_key_b64)}

if ! command -v sudo >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y sudo ca-certificates
fi

if ! id "$ADMIN" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$ADMIN"
fi
usermod -aG sudo "$ADMIN"
PASSWORD="$(printf '%s' "$PASSWORD_B64" | base64 -d)"
printf '%s:%s\\n' "$ADMIN" "$PASSWORD" | chpasswd
unset PASSWORD PASSWORD_B64

HOME_DIR="$(getent passwd "$ADMIN" | cut -d: -f6)"
install -d -m 0700 -o "$ADMIN" -g "$ADMIN" "$HOME_DIR/.ssh"
printf '%s' "$PUBKEY_B64" | base64 -d > "$HOME_DIR/.ssh/authorized_keys"
printf '\\n' >> "$HOME_DIR/.ssh/authorized_keys"
chown "$ADMIN:$ADMIN" "$HOME_DIR/.ssh/authorized_keys"
chmod 0600 "$HOME_DIR/.ssh/authorized_keys"

printf '%s ALL=(ALL:ALL) NOPASSWD: ALL\\n' "$ADMIN" > /etc/sudoers.d/90-konnaxion-admin
chmod 0440 /etc/sudoers.d/90-konnaxion-admin
visudo -cf /etc/sudoers.d/90-konnaxion-admin >/dev/null

echo KX_ADMIN_CREATED
"""
    created = _ssh_script(
        host=host,
        user="root",
        key_path=key_path,
        port=port,
        known_hosts=known_hosts,
        script=create_script,
        timeout_seconds=300,
    )
    if created.returncode != 0:
        raise NetcupProvisionError(f"kx-admin creation failed: {_text(created.stderr)[-1200:]}")

    admin_probe = _ssh_command(
        host=host,
        user=admin_user,
        key_path=key_path,
        port=port,
        known_hosts=known_hosts,
        command="sudo -n sh -c 'test \"$(id -u)\" = 0'",
        timeout_seconds=30,
    )
    if admin_probe.returncode != 0:
        raise NetcupProvisionError(
            "kx-admin SSH/sudo verification failed; root SSH has NOT been disabled."
        )

    harden_script = f"""set -euo pipefail
ADMIN={admin_q}
install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/90-konnaxion-hardening.conf <<EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers $ADMIN
EOF
sshd -t
if systemctl list-unit-files ssh.service >/dev/null 2>&1; then
  systemctl reload ssh
else
  systemctl reload sshd
fi
echo SSH_HARDENED
"""
    hardened = _ssh_script(
        host=host,
        user="root",
        key_path=key_path,
        port=port,
        known_hosts=known_hosts,
        script=harden_script,
        timeout_seconds=60,
    )
    if hardened.returncode != 0:
        raise NetcupProvisionError(
            f"SSH hardening failed after kx-admin creation: {_text(hardened.stderr)[-1200:]}"
        )

    final_probe = _ssh_command(
        host=host,
        user=admin_user,
        key_path=key_path,
        port=port,
        known_hosts=known_hosts,
        command=(
            "sudo -n sh -c '"
            "sshd -T | grep -q \"^permitrootlogin no$\" && "
            "sshd -T | grep -q \"^passwordauthentication no$\" && "
            "id -u >/dev/null'"
        ),
        timeout_seconds=30,
    )
    if final_probe.returncode != 0:
        raise NetcupProvisionError(
            "Post-hardening kx-admin verification failed. Use Netcup SCP/VNC before closing the console."
        )

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    env_values: dict[str, Any] = {
        "KX_DROPLET_NAME": str(payload.get("droplet_name") or "netcup-vps"),
        "KX_DROPLET_HOST": host,
        "KX_DROPLET_USER": admin_user,
        "KX_DROPLET_SSH_KEY_PATH": str(key_path),
        "KX_DROPLET_SSH_PORT": str(port),
        "KX_NETCUP_KXADMIN_PASSWORD": password,
        "KX_NETCUP_VPS_PREPARED": "true",
        "KX_NETCUP_VPS_PREPARED_HOST": host,
        "KX_NETCUP_VPS_PREPARED_USER": admin_user,
        "KX_NETCUP_SSH_HOST_FINGERPRINT": expected_fingerprint,
        "KX_NETCUP_VPS_PREPARED_AT": now,
    }
    if domain:
        env_values["KX_DROPLET_DOMAIN"] = domain
    for source, env_name in (
        ("instance_id", "KX_DROPLET_INSTANCE_ID"),
        ("remote_kx_root", "KX_DROPLET_KX_ROOT"),
        ("remote_capsule_dir", "KX_DROPLET_CAPSULE_DIR"),
    ):
        value = str(payload.get(source) or "").strip()
        if value:
            env_values[env_name] = value

    env_path = _persist_operator_env(env_values)

    return {
        "ok": True,
        "message": "Fresh Netcup VPS provisioned for GO LIVE. kx-admin password was saved to the local Manager .env and was not written to logs.",
        "data": {
            "droplet_host": host,
            "droplet_user": admin_user,
            "ssh_port": port,
            "ssh_host_fingerprint": expected_fingerprint,
            "operator_env_file": str(env_path),
            "password_env_key": "KX_NETCUP_KXADMIN_PASSWORD",
            "root_ssh_disabled": True,
            "ssh_password_auth_disabled": True,
            "password_value_exposed": False,
            "go_live_ready": True,
            "prepared_at": now,
        },
    }


def netcup_go_live_ready(*, host: str, user: str) -> tuple[bool, str]:
    prepared = os.getenv("KX_NETCUP_VPS_PREPARED", "").strip().lower() in {"1", "true", "yes", "on"}
    prepared_host = os.getenv("KX_NETCUP_VPS_PREPARED_HOST", "").strip()
    prepared_user = os.getenv("KX_NETCUP_VPS_PREPARED_USER", "").strip()
    fingerprint = os.getenv("KX_NETCUP_SSH_HOST_FINGERPRINT", "").strip()
    if not prepared:
        return False, "Fresh Netcup VPS has not been provisioned by Capsule Manager."
    if prepared_host != str(host).strip():
        return False, "Netcup provisioning marker belongs to a different VPS host."
    if prepared_user != str(user).strip():
        return False, "Netcup provisioning marker belongs to a different SSH user."
    if not _FINGERPRINT_RE.fullmatch(fingerprint):
        return False, "Verified Netcup SSH host fingerprint is missing."
    return True, "Fresh Netcup VPS provisioning verified."


__all__ = [
    "NETCUP_ADMIN_USER",
    "NETCUP_MEDIA_DOC_URL",
    "NETCUP_SCP_URL",
    "NetcupProvisionError",
    "netcup_go_live_ready",
    "provision_fresh_vps",
    "scan_host_fingerprints",
]
