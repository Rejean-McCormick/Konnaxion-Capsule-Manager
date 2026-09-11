# kx_manager/ui/droplet_bootstrap.py

"""Droplet bootstrap helpers for Konnaxion Capsule Manager GUI actions.

This module owns the local Manager/Agent archive creation and the remote shell
script used to install or refresh the Konnaxion Agent on a Droplet.

It intentionally does not execute SSH itself. SSH/SCP execution remains owned
by the Agent execution client.
"""

from __future__ import annotations

import os
import shlex
import tarfile
import tempfile
from pathlib import Path


BOOTSTRAP_ARCHIVE_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".kx-ui",
        "runtime",
        "dist",
        "build",
        "htmlcov",
    }
)


def _project_root() -> Path:
    """Return local Konnaxion Capsule Manager repository root."""

    return Path(__file__).resolve().parents[2]


def _should_include_bootstrap_path(path: Path, root: Path) -> bool:
    """Return whether a local path should be copied to the Droplet."""

    try:
        relative = path.relative_to(root)
    except ValueError:
        return False

    if set(relative.parts) & BOOTSTRAP_ARCHIVE_EXCLUDED_PARTS:
        return False

    if path.name.endswith((".pyc", ".pyo", ".log")):
        return False

    if path.name in {".coverage", "agent.token"}:
        return False

    return True


def _make_manager_bootstrap_archive() -> Path:
    """Create a temporary tar.gz archive of the Manager/Agent repository."""

    root = _project_root()

    fd, raw_path = tempfile.mkstemp(
        prefix="konnaxion-manager-bootstrap-",
        suffix=".tar.gz",
    )
    os.close(fd)

    archive_path = Path(raw_path)

    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(root.rglob("*")):
            if not _should_include_bootstrap_path(path, root):
                continue

            relative = path.relative_to(root)
            archive.add(path, arcname=str(relative))

    return archive_path


def _remote_bootstrap_command(
    *,
    remote_archive: str,
    remote_kx_root: str,
    remote_manager_dir: str,
    instance_id: str,
) -> str:
    """Return the remote shell script used to bootstrap the Agent.

    The remote bootstrap keeps the SSH/bootstrap boundary as root for package
    installation and systemd management, but the long-running Agent itself is
    installed as the dedicated non-login ``kx-agent`` service identity.
    """

    quoted_archive = shlex.quote(remote_archive)
    quoted_root = shlex.quote(remote_kx_root)
    quoted_manager = shlex.quote(remote_manager_dir)
    quoted_instance_id = shlex.quote(instance_id)

    return f"""set -e
export DEBIAN_FRONTEND=noninteractive

KX_ROOT={quoted_root}
KX_MANAGER_DIR={quoted_manager}
KX_INSTANCE_ID={quoted_instance_id}
KX_AGENT_USER=kx-agent
KX_AGENT_GROUP=kx-agent
KX_TOKEN_PATH="$KX_MANAGER_DIR/agent.token"
KX_AUDIT_DIR="$KX_ROOT/agent/audit"
KX_AUDIT_FILE="$KX_AUDIT_DIR/agent-audit.jsonl"
KX_PUBLIC_KEY="$KX_ROOT/agent/keys/capsule-signing-public.pem"

# Dedicated service identity. It is a non-login system account. Docker access
# is granted only to the systemd service through SupplementaryGroups=docker;
# the account is deliberately NOT persisted as a member of the docker group.
if ! getent group "$KX_AGENT_GROUP" >/dev/null 2>&1; then
  groupadd --system "$KX_AGENT_GROUP"
fi
if ! id "$KX_AGENT_USER" >/dev/null 2>&1; then
  useradd --system --gid "$KX_AGENT_GROUP" --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "$KX_AGENT_USER"
fi

install -d -m 0750 -o "$KX_AGENT_USER" -g "$KX_AGENT_GROUP" \
  "$KX_ROOT/capsules" "$KX_ROOT/instances" "$KX_ROOT/backups" \
  "$KX_ROOT/shared" "$KX_ROOT/releases" "$KX_ROOT/agent"
install -d -m 0750 -o root -g "$KX_AGENT_GROUP" "$KX_ROOT/agent/keys"
install -d -m 0700 -o "$KX_AGENT_USER" -g "$KX_AGENT_GROUP" "$KX_AUDIT_DIR"
mkdir -p "$KX_MANAGER_DIR"

# Existing v13 deployments created runtime state as root. Migrate only the
# Agent-owned control/config paths. Never recursively chown postgres/redis data.
if [ -d "$KX_ROOT/instances/$KX_INSTANCE_ID" ]; then
  chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/instances/$KX_INSTANCE_ID"
  for owned_subdir in env state; do
    if [ -e "$KX_ROOT/instances/$KX_INSTANCE_ID/$owned_subdir" ]; then
      chown -R "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/instances/$KX_INSTANCE_ID/$owned_subdir"
    fi
  done

  # Runtime bind-mount directories may have been created by the old root-based
  # Agent/Docker workflow. The non-root Agent must own the directory inodes so
  # it can safely normalize their modes on every render. Do not recursively
  # chown container data: files inside these paths may legitimately be written
  # by container UIDs. PostgreSQL and Redis data are intentionally excluded.
  if [ -d "$KX_ROOT/instances/$KX_INSTANCE_ID/logs" ]; then
    chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/instances/$KX_INSTANCE_ID/logs"
    chmod 0750 "$KX_ROOT/instances/$KX_INSTANCE_ID/logs"
    find "$KX_ROOT/instances/$KX_INSTANCE_ID/logs" -mindepth 1 -maxdepth 1 -type d \
      -exec chown "$KX_AGENT_USER:$KX_AGENT_GROUP" {{}} + \
      -exec chmod 0750 {{}} +
  fi
  if [ -d "$KX_ROOT/instances/$KX_INSTANCE_ID/media" ]; then
    chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/instances/$KX_INSTANCE_ID/media"
    chmod 0750 "$KX_ROOT/instances/$KX_INSTANCE_ID/media"
  fi
fi
if [ -d "$KX_ROOT/backups" ]; then
  chown -R "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/backups"
fi

# Capsules copied by older root-based Managers can be mode 0600 root:root.
# The hardened Agent runs as kx-agent, so migrate only the signed .kxcap
# transfer artifacts to a root-owned, Agent-readable mode. The capsule
# directory itself remains Agent-owned because import uses atomic temp files.
if [ -d "$KX_ROOT/capsules" ]; then
  chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/capsules"
  chmod 0750 "$KX_ROOT/capsules"
  find "$KX_ROOT/capsules" -maxdepth 1 -type f -name '*.kxcap' \
    -exec chown root:"$KX_AGENT_GROUP" {{}} + \
    -exec chmod 0640 {{}} +
fi

for shared_control in "$KX_ROOT/shared/capsules" "$KX_ROOT/shared/registry"; do
  if [ -e "$shared_control" ]; then
    chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$shared_control"
  fi
done
if [ -d "$KX_ROOT/shared/registry" ]; then
  chown -R "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_ROOT/shared/registry"
fi

# The Security Gate cryptographically verifies the imported capsule with this
# trusted public key. Bootstrap receives the PUBLIC key only (never the private
# signing key) through /tmp and installs it at the canonical Agent path.
BOOTSTRAP_PUBLIC_KEY=/tmp/konnaxion-capsule-signing-public.pem
if [ ! -s "$BOOTSTRAP_PUBLIC_KEY" ]; then
  echo "Trusted capsule public key was not uploaded by the Manager" >&2
  exit 66
fi
install -m 0644 -o root -g "$KX_AGENT_GROUP" "$BOOTSTRAP_PUBLIC_KEY" "$KX_PUBLIC_KEY"
rm -f "$BOOTSTRAP_PUBLIC_KEY"

apt-get update
apt-get install -y curl ca-certificates python3 python3-venv python3-pip tar

# Public-VPS runtime prerequisite: Docker Engine plus Compose. Prefer Debian's
# native packages and tolerate the Compose package name used by different
# Debian releases.
if ! command -v docker >/dev/null 2>&1; then
  apt-get install -y docker.io
fi

if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
    apt-get install -y docker-compose-v2
  elif apt-cache show docker-compose-plugin >/dev/null 2>&1; then
    apt-get install -y docker-compose-plugin
  elif apt-cache show docker-compose >/dev/null 2>&1; then
    apt-get install -y docker-compose
  else
    echo "No Docker Compose package is available from configured APT repositories" >&2
    exit 127
  fi
fi

systemctl enable --now docker
docker --version
if docker compose version >/dev/null 2>&1; then
  docker compose version
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose --version
else
  echo "Neither docker compose nor docker-compose is available after bootstrap" >&2
  exit 127
fi

if [ -L /usr/local/bin/uv ] && [ "$(readlink /usr/local/bin/uv)" = "/usr/local/bin/uv" ]; then
  rm -f /usr/local/bin/uv
fi

if [ ! -x /root/.local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

UV_BIN=""
if [ -x /root/.local/bin/uv ]; then
  UV_BIN="/root/.local/bin/uv"
else
  UV_BIN="$(command -v uv || true)"
fi

if [ -z "$UV_BIN" ]; then
  echo "uv was not installed or not found" >&2
  exit 127
fi

if [ "$UV_BIN" = "/usr/local/bin/uv" ]; then
  if [ -L /usr/local/bin/uv ]; then
    UV_REAL="$(readlink -f /usr/local/bin/uv || true)"
    if [ -n "$UV_REAL" ] && [ "$UV_REAL" != "/usr/local/bin/uv" ]; then
      UV_BIN="$UV_REAL"
    else
      rm -f /usr/local/bin/uv
      UV_BIN="/root/.local/bin/uv"
    fi
  fi
fi

mkdir -p /usr/local/bin
if [ "$UV_BIN" != "/usr/local/bin/uv" ]; then
  ln -sf "$UV_BIN" /usr/local/bin/uv
fi

/usr/local/bin/uv --version

# Preserve the local Manager-Agent pairing token across Agent refreshes.
TOKEN_BACKUP="$(mktemp /tmp/konnaxion-agent-token.XXXXXX)"
chmod 0600 "$TOKEN_BACKUP"
if [ -s "$KX_TOKEN_PATH" ]; then
  cp "$KX_TOKEN_PATH" "$TOKEN_BACKUP"
fi

# Keep the venv cache but replace the shipped source tree. Hidden .venv is not
# removed by this glob. The token is restored/generated immediately afterwards.
rm -rf "$KX_MANAGER_DIR"/*
tar -xzf {quoted_archive} -C "$KX_MANAGER_DIR"
rm -f {quoted_archive}

cd "$KX_MANAGER_DIR"
/usr/local/bin/uv sync || /usr/local/bin/uv pip install -e .

if [ -s "$TOKEN_BACKUP" ]; then
  install -m 0600 -o "$KX_AGENT_USER" -g "$KX_AGENT_GROUP" "$TOKEN_BACKUP" "$KX_TOKEN_PATH"
else
  umask 077
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$KX_TOKEN_PATH"
  chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_TOKEN_PATH"
  chmod 0600 "$KX_TOKEN_PATH"
fi
rm -f "$TOKEN_BACKUP"

# Audit evidence exists before the first privileged request and is owned by the
# Agent. The API appends redacted JSONL events for authenticated write actions.
touch "$KX_AUDIT_FILE"
chown "$KX_AGENT_USER:$KX_AGENT_GROUP" "$KX_AUDIT_FILE"
chmod 0600 "$KX_AUDIT_FILE"
chmod 0700 "$KX_AUDIT_DIR"

# The source/venv is installed by root and remains immutable to the service.
# The Agent executes the venv entry point directly so ProtectHome can hide
# /root and the service never depends on root's uv installation at runtime.
chown root:"$KX_AGENT_GROUP" "$KX_MANAGER_DIR"
chmod 0750 "$KX_MANAGER_DIR"
find "$KX_MANAGER_DIR/.venv" -type d -exec chmod 0755 {{}} + 2>/dev/null || true

cat >/etc/systemd/system/konnaxion-agent.service <<EOF
[Unit]
Description=Konnaxion Agent
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
User=kx-agent
Group=kx-agent
SupplementaryGroups=docker
WorkingDirectory={remote_manager_dir}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=KX_ROOT={remote_kx_root}
Environment=KX_AGENT_HOST=127.0.0.1
Environment=KX_AGENT_PORT=8765
Environment=KX_AGENT_TOKEN_PATH={remote_manager_dir}/agent.token
Environment=KX_REQUIRE_AGENT_TOKEN=true
Environment=KX_AGENT_AUDIT_FILE={remote_kx_root}/agent/audit/agent-audit.jsonl
Environment=KX_REQUIRE_AGENT_AUDIT=true
Environment=KX_INSTANCE_ID={instance_id}
Environment=KX_NETWORK_PROFILE=public_vps
Environment=KX_EXPOSURE_MODE=public
Environment=KX_PUBLIC_MODE_ENABLED=false
Environment=KX_REQUIRE_SIGNED_CAPSULE=true
Environment=KX_CAPSULE_PUBLIC_KEY_FILE={remote_kx_root}/agent/keys/capsule-signing-public.pem
Environment=KX_GENERATE_SECRETS_ON_INSTALL=true
Environment=KX_ALLOW_UNKNOWN_IMAGES=false
Environment=KX_ALLOW_PRIVILEGED_CONTAINERS=false
Environment=KX_ALLOW_DOCKER_SOCKET_MOUNT=false
Environment=KX_ALLOW_HOST_NETWORK=false
Environment=KX_BACKUP_ENABLED=true
ExecStart={remote_manager_dir}/.venv/bin/kx-agent run
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=full
RestrictSUIDSGID=yes
LockPersonality=yes
ReadWritePaths={remote_kx_root} /var/run/docker.sock

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable konnaxion-agent
# Bootstrap replaces the Agent source tree in-place. Always restart after sync.
systemctl restart konnaxion-agent
sleep 5

# Fail closed if the service identity/hardening or private credentials are not
# what the Manager expects.
[ "$(systemctl show konnaxion-agent -p User --value)" = "$KX_AGENT_USER" ]
[ "$(systemctl show konnaxion-agent -p NoNewPrivileges --value)" = "yes" ]
[ "$(systemctl show konnaxion-agent -p PrivateTmp --value)" = "yes" ]
[ "$(stat -c '%a' "$KX_TOKEN_PATH")" = "600" ]
[ "$(stat -c '%U:%G' "$KX_TOKEN_PATH")" = "$KX_AGENT_USER:$KX_AGENT_GROUP" ]
[ "$(stat -c '%a' "$KX_AUDIT_FILE")" = "600" ]
[ "$(stat -c '%U:%G' "$KX_AUDIT_FILE")" = "$KX_AGENT_USER:$KX_AGENT_GROUP" ]
runuser -u "$KX_AGENT_USER" -g "$KX_AGENT_GROUP" -G docker -- docker version --format '{{{{.Server.Version}}}}' >/dev/null

systemctl --no-pager --full status konnaxion-agent || true
curl --fail-with-body --max-time 10 http://127.0.0.1:8765/v1/health

# Prove the production auth boundary is actually active. An unauthenticated
# write must be rejected with 401; the same deliberately-invalid request with
# the local token must pass authentication and reach request validation (422).
AUTH_FILE="$(mktemp /tmp/konnaxion-agent-auth.XXXXXX)"
chmod 0600 "$AUTH_FILE"
printf 'Authorization: Bearer %s\n' "$(cat "$KX_TOKEN_PATH")" > "$AUTH_FILE"
UNAUTH_STATUS="$(curl -sS -o /dev/null -w '%{{http_code}}' -H 'Content-Type: application/json' -X POST --data '{{}}' http://127.0.0.1:8765/v1/capsules/verify)"
AUTH_STATUS="$(curl -sS -o /dev/null -w '%{{http_code}}' -H 'Content-Type: application/json' -H "@$AUTH_FILE" -X POST --data '{{}}' http://127.0.0.1:8765/v1/capsules/verify)"
rm -f "$AUTH_FILE"
[ "$UNAUTH_STATUS" = "401" ]
[ "$AUTH_STATUS" = "422" ]
[ -s "$KX_AUDIT_FILE" ]

echo
echo "BOOTSTRAP_OK"
echo "remote_kx_root={remote_kx_root}"
echo "remote_manager_dir={remote_manager_dir}"
echo "instance_id={quoted_instance_id}"
echo "systemd_service=konnaxion-agent.service"
echo "agent_service_user=kx-agent"
echo "agent_token={remote_manager_dir}/agent.token"
echo "agent_audit={remote_kx_root}/agent/audit/agent-audit.jsonl"
echo "capsule_public_key={remote_kx_root}/agent/keys/capsule-signing-public.pem"
"""


__all__ = [
    "BOOTSTRAP_ARCHIVE_EXCLUDED_PARTS",
    "_make_manager_bootstrap_archive",
    "_remote_bootstrap_command",
    "_project_root",
    "_should_include_bootstrap_path",
]