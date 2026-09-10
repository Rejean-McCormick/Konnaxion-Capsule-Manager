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

    if path.name in {".coverage"}:
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
    """Return the remote shell script used to bootstrap the Agent."""

    quoted_archive = shlex.quote(remote_archive)
    quoted_root = shlex.quote(remote_kx_root)
    quoted_manager = shlex.quote(remote_manager_dir)
    quoted_instance_id = shlex.quote(instance_id)

    return f"""set -e
export DEBIAN_FRONTEND=noninteractive

mkdir -p {quoted_root}/capsules {quoted_root}/instances {quoted_root}/backups {quoted_root}/shared {quoted_root}/releases {quoted_root}/agent {quoted_manager}

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

rm -rf {quoted_manager}/*
tar -xzf {quoted_archive} -C {quoted_manager}
rm -f {quoted_archive}

cd {quoted_manager}
/usr/local/bin/uv sync || /usr/local/bin/uv pip install -e .

cat >/etc/systemd/system/konnaxion-agent.service <<EOF
[Unit]
Description=Konnaxion Agent
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={remote_manager_dir}
Environment=KX_ROOT={remote_kx_root}
Environment=KX_AGENT_HOST=127.0.0.1
Environment=KX_AGENT_PORT=8765
Environment=KX_INSTANCE_ID={instance_id}
Environment=KX_NETWORK_PROFILE=public_vps
Environment=KX_EXPOSURE_MODE=public
Environment=KX_PUBLIC_MODE_ENABLED=false
Environment=KX_REQUIRE_SIGNED_CAPSULE=true
Environment=KX_GENERATE_SECRETS_ON_INSTALL=true
Environment=KX_ALLOW_UNKNOWN_IMAGES=false
Environment=KX_ALLOW_PRIVILEGED_CONTAINERS=false
Environment=KX_ALLOW_DOCKER_SOCKET_MOUNT=false
Environment=KX_ALLOW_HOST_NETWORK=false
Environment=KX_BACKUP_ENABLED=true
ExecStart=/usr/local/bin/uv run kx-agent run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable konnaxion-agent
# Bootstrap replaces the Agent source tree in-place.  ``enable --now`` does
# not restart an already-running service, so the old Python process can keep
# serving the previous API schema.  Always restart after syncing the files.
systemctl restart konnaxion-agent
sleep 5

systemctl --no-pager --full status konnaxion-agent || true
curl --fail-with-body --max-time 10 http://127.0.0.1:8765/v1/health

echo
echo "BOOTSTRAP_OK"
echo "remote_kx_root={remote_kx_root}"
echo "remote_manager_dir={remote_manager_dir}"
echo "instance_id={quoted_instance_id}"
echo "systemd_service=konnaxion-agent.service"
"""


__all__ = [
    "BOOTSTRAP_ARCHIVE_EXCLUDED_PARTS",
    "_make_manager_bootstrap_archive",
    "_remote_bootstrap_command",
    "_project_root",
    "_should_include_bootstrap_path",
]