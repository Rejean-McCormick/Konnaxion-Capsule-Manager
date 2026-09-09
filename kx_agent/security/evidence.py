"""Read-only runtime evidence bridge for Konnaxion Security Gate / SecurityDiag."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from kx_agent.audit import redact
from kx_agent.capsules.checksums import verify_capsule_checksums
from kx_agent.capsules.signature import verify_capsule_signature
from kx_shared.konnaxion_constants import KX_BACKUPS_ROOT, KX_ROOT
from kx_shared.paths import instance_security_gate_file, validate_safe_id

DEFAULT_CAPSULE_PUBLIC_KEY_FILE = Path(KX_ROOT) / 'agent' / 'keys' / 'capsule-signing-public.pem'
CAPSULE_PUBLIC_KEY_ENV = 'KX_CAPSULE_PUBLIC_KEY_FILE'

@dataclass(frozen=True)
class RuntimeSecurityEvidence:
    capsule_signature_verified: bool
    image_checksums_verified: bool
    firewall_enabled: bool
    backup_configured: bool
    admin_surface_private: bool
    postgres_public: bool
    redis_public: bool
    allowed_images: tuple[str, ...]
    details: Mapping[str, Any]

def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')

def _capsule_root(capsule_id: str | None) -> Path | None:
    if not capsule_id:
        return None
    safe = validate_safe_id(capsule_id, field_name='capsule_id')
    path = (KX_ROOT / 'shared' / 'capsules' / safe).resolve(strict=False)
    return path if path.exists() and path.is_dir() else None

def _public_key_file() -> Path:
    raw = os.getenv(CAPSULE_PUBLIC_KEY_ENV, '').strip()
    return Path(raw) if raw else DEFAULT_CAPSULE_PUBLIC_KEY_FILE

def _verify_capsule(capsule_id: str | None) -> tuple[bool, bool, dict[str, Any]]:
    root = _capsule_root(capsule_id)
    if root is None:
        return False, False, {'capsule': 'missing'}

    key_file = _public_key_file()
    signature_ok = False
    signature_error: str | None = None
    if not key_file.exists():
        signature_error = f'trusted public key missing: {key_file}'
    else:
        try:
            verify_capsule_signature(root, key_file)
            signature_ok = True
        except Exception as exc:  # noqa: BLE001
            signature_error = f'{type(exc).__name__}: {exc}'

    try:
        checksum_report = verify_capsule_checksums(root)
        checksums_ok = bool(checksum_report.ok)
        checksum_detail = {
            'checked': checksum_report.checked,
            'missing': list(checksum_report.missing),
            'extra': list(checksum_report.extra),
            'mismatched_count': len(checksum_report.mismatched),
            'malformed': list(checksum_report.malformed),
        }
    except Exception as exc:  # noqa: BLE001
        checksums_ok = False
        checksum_detail = {'error': f'{type(exc).__name__}: {exc}'}

    return signature_ok, checksums_ok, {
        'capsule_root': str(root),
        'public_key_file': str(key_file),
        'signature_error': signature_error,
        'checksums': checksum_detail,
    }

def _firewall_enabled() -> tuple[bool, dict[str, Any]]:
    probes = (
        (['ufw', 'status'], 'ufw', lambda text: 'status: active' in text.lower()),
        (['firewall-cmd', '--state'], 'firewalld', lambda text: text.strip().lower() == 'running'),
    )
    for argv, name, detector in probes:
        try:
            cp = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace', timeout=10, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if cp.returncode == 0:
            active = detector(cp.stdout or '')
            return bool(active), {'backend': name, 'active': bool(active)}
    return False, {'backend': 'unknown', 'active': False}

def _truthy(value: Any) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}

def _backup_configured(instance_id: str, env: Mapping[str, str]) -> tuple[bool, dict[str, Any]]:
    enabled = _truthy(env.get('KX_BACKUP_ENABLED', 'true'))
    root_value = str(env.get('KX_BACKUP_ROOT') or KX_BACKUPS_ROOT).strip()
    root = Path(root_value)
    instance_root = root / instance_id if root == KX_BACKUPS_ROOT else root
    exists = instance_root.exists() or root.exists()
    return bool(enabled and root_value and exists), {'enabled': enabled, 'root': root_value, 'root_exists': exists}

def _manifest_allowed_images(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Return runtime images integrity-bound by the signed capsule manifest.

    Current ``kx-capsule-manifest/v1`` stores image metadata under
    ``runtime.images``.  Older development manifests stored the same payload
    at the top-level ``images`` key, so keep that shape as a compatibility
    fallback.

    Security Gate separately verifies the capsule signature and image
    checksums before this allowlist is consumed.  Therefore these values are
    not arbitrary instance input: they are the image identities declared by
    the verified capsule.
    """

    runtime = manifest.get('runtime')
    raw: Any = runtime.get('images') if isinstance(runtime, Mapping) else None
    if raw is None:
        raw = manifest.get('images')

    allowed: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                image = str(item.get('image') or '').strip()
                if image:
                    allowed.append(image)
    elif isinstance(raw, Mapping):
        for value in raw.values():
            if isinstance(value, Mapping):
                image = str(value.get('image') or '').strip()
            else:
                image = str(value or '').strip()
            if image:
                allowed.append(image)

    return tuple(sorted(set(allowed)))

def _published_ports(compose: Mapping[str, Any], service_name: str) -> list[str]:
    services = compose.get('services', {})
    if not isinstance(services, Mapping):
        return []
    spec = services.get(service_name, {})
    if not isinstance(spec, Mapping):
        return []
    raw = spec.get('ports') or []
    return [str(item) for item in raw] if isinstance(raw, list) else []

def _runtime_exposure(compose: Mapping[str, Any]) -> tuple[bool, bool, bool, dict[str, Any]]:
    postgres_ports = _published_ports(compose, 'postgres')
    redis_ports = _published_ports(compose, 'redis')
    flower_ports = _published_ports(compose, 'flower')
    non_traefik_public: dict[str, list[str]] = {}
    services = compose.get('services', {})
    if isinstance(services, Mapping):
        for name, spec in services.items():
            if str(name) == 'traefik' or not isinstance(spec, Mapping):
                continue
            ports = spec.get('ports') or []
            if ports:
                non_traefik_public[str(name)] = [str(item) for item in ports]
    admin_private = not flower_ports and not non_traefik_public
    return bool(postgres_ports), bool(redis_ports), admin_private, {
        'postgres_ports': postgres_ports,
        'redis_ports': redis_ports,
        'flower_ports': flower_ports,
        'non_traefik_published_services': non_traefik_public,
    }

def collect_runtime_security_evidence(*, instance_id: str, capsule_id: str | None,
                                      compose: Mapping[str, Any], manifest: Mapping[str, Any],
                                      env: Mapping[str, str]) -> RuntimeSecurityEvidence:
    validate_safe_id(instance_id, field_name='instance_id')
    signature_ok, checksums_ok, capsule_detail = _verify_capsule(capsule_id)
    firewall_ok, firewall_detail = _firewall_enabled()
    backup_ok, backup_detail = _backup_configured(instance_id, env)
    postgres_public, redis_public, admin_private, exposure_detail = _runtime_exposure(compose)
    allowed_images = _manifest_allowed_images(manifest)
    return RuntimeSecurityEvidence(
        capsule_signature_verified=signature_ok,
        image_checksums_verified=checksums_ok,
        firewall_enabled=firewall_ok,
        backup_configured=backup_ok,
        admin_surface_private=admin_private,
        postgres_public=postgres_public,
        redis_public=redis_public,
        allowed_images=allowed_images,
        details={
            'capsule': capsule_detail,
            'firewall': firewall_detail,
            'backup': backup_detail,
            'exposure': exposure_detail,
            'allowed_image_count': len(allowed_images),
        },
    )

def write_security_gate_evidence(instance_id: str, payload: Mapping[str, Any]) -> Path:
    target = instance_security_gate_file(instance_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_document = {'schema': 'kx-security-gate-evidence/v1', 'generated_at': utc_now_iso(), **dict(payload)}
    document = redact(raw_document)
    encoded = (json.dumps(document, indent=2, sort_keys=True) + '\n').encode('utf-8')
    fd, temp_name = tempfile.mkstemp(prefix='.security-gate-', suffix='.json', dir=str(target.parent))
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)
    temp = Path(temp_name)
    try:
        temp.chmod(0o640)
        temp.replace(target)
        target.chmod(0o640)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
    return target

__all__ = ['CAPSULE_PUBLIC_KEY_ENV', 'DEFAULT_CAPSULE_PUBLIC_KEY_FILE',
           'RuntimeSecurityEvidence', 'collect_runtime_security_evidence',
           'write_security_gate_evidence']
