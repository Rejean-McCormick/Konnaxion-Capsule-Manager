"""Agent-owned, fail-closed ReleaseSet admission for the Linux/Docker VPS profile.

Trust boundary: a signed ReleaseSet selects what must be measured, but never
supplies the measurement. The Agent hashes actual files and inspects immutable
Docker images before calling KonnaxionDiag's strict Ed25519 PEP. No remote calls
or database writes occur in this module.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from kx_shared.errors import SecurityGateBlockingError

_HEX = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMMUTABLE_IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}\Z")
_INSTANCE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
_COMPONENTS = ("runtime_pack", "policy_bundle", "infra_manifest", "sbom", "provenance")
_POLICY_DIR = Path("/etc/konnaxion/release-admission")


def _deny(message: str) -> None:
    raise SecurityGateBlockingError("Release admission denied: " + message, {"reason": "release_admission_denied"})


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _hash_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json_bytes(value)).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1048576), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        _deny("required JSON file is missing, linked or oversized")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _deny("required JSON file cannot be decoded")
    if not isinstance(value, dict):
        _deny("required JSON document is not an object")
    return value


def _in_root(root: Path, raw: str) -> Path:
    """Resolve selection inside a trusted deployment root; reject linked paths."""
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
        _deny("an admission component path is not relative")
    candidate = root / raw
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        _deny("admission path is outside deployment root")
    if ".." in relative.parts or "." in relative.parts:
        _deny("admission component contains traversal")
    here = root
    for part in relative.parts:
        here = here / part
        if here.is_symlink():
            _deny("symlink in admission component")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve(strict=True)) or not resolved.is_file():
        _deny("component does not exist inside deployment root")
    return resolved


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=20,
                                check=False, shell=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        _deny("could not independently measure source Git identity")
    if result.returncode:
        _deny("could not independently measure source Git identity")
    return result.stdout.strip()


def _trusted_policy_file(instance_id: str) -> Path:
    if not _INSTANCE.fullmatch(instance_id):
        _deny("invalid instance identifier")
    return _POLICY_DIR / (instance_id + ".json")


def _policy(instance_id: str) -> dict[str, Any]:
    path = _trusted_policy_file(instance_id)
    if os.name != "nt":
        # The admission configuration is an Agent/host responsibility, never
        # writable by application containers or callers.
        for protected in (path.parent, path):
            if not protected.exists() or protected.is_symlink():
                _deny("Agent-owned release admission policy is missing or linked")
            st = protected.stat()
            if st.st_mode & 0o022:
                _deny("Agent-owned admission policy is group/other writable")
            if st.st_uid not in {0, os.geteuid()}:
                _deny("Agent-owned admission policy has an untrusted owner")
    doc = _read_json(path)
    if doc.get("schema") != "koa.agent.release-admission.v1" or doc.get("instance_id") != instance_id:
        _deny("Agent-owned admission policy or instance binding is invalid")
    if not str(doc.get("policy_version") or "").strip():
        _deny("expected release policy version missing")
    return doc


def _trusted_path(root: Path, rel: Any) -> Path:
    if not isinstance(rel, str):
        _deny("invalid admission document path")
    return _in_root(root, rel)


def _measure_components(root: Path, release_set: dict[str, Any]) -> dict[str, str]:
    selection = release_set.get("component_files")
    if not isinstance(selection, dict):
        _deny("signed component file inventory is missing")
    measurements: dict[str, str] = {}
    for name in _COMPONENTS:
        rows = selection.get(name)
        if not isinstance(rows, list) or not rows:
            _deny("missing required component file selection: " + name)
        actual = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                _deny("malformed component selection")
            actual.append({"path": row["path"], "digest": _hash_file(_in_root(root, row["path"]))})
        actual.sort(key=lambda value: value["path"])
        if actual != rows:  # reject tampering, duplicate/unsorted rows, drift
            _deny("component bytes differ from signed inventory: " + name)
        measurements[name] = _hash_json(actual)
    return measurements


def _measure_images(runtime: Any) -> list[str]:
    # Config is rendered by Docker Compose, not taken from Manager-supplied JSON.
    result = runtime._compose_args(["config", "--format", "json"])
    if not result.ok:
        _deny("Docker Compose config cannot be rendered as JSON")
    try:
        services = json.loads(result.stdout)["services"]
    except (ValueError, TypeError, KeyError):
        _deny("invalid Docker Compose service inventory")
    if not isinstance(services, dict) or not services:
        _deny("Docker Compose services are missing")
    image_ids: set[str] = set()
    for name, entry in sorted(services.items()):
        if not isinstance(entry, dict) or "build" in entry:
            _deny("build operations are forbidden in an admitted Compose deployment")
        image_ref = entry.get("image")
        if not isinstance(image_ref, str) or not _IMMUTABLE_IMAGE.fullmatch(image_ref):
            _deny("a Compose image is not pinned by an immutable registry digest")
        info = runtime.image_inspect(image_ref)
        if not isinstance(info.image_id, str) or not _HEX.fullmatch(info.image_id):
            _deny("Docker image does not have a valid immutable image ID")
        if image_ref not in info.repo_digests:
            _deny("Docker image does not match the pinned registry digest")
        image_ids.add(info.image_id)
    return sorted(image_ids)


def _measure_identity(root: Path, policy: dict[str, Any], release_set: dict[str, Any], runtime: Any) -> dict[str, Any]:
    signed_identity = release_set.get("identity")
    if not isinstance(signed_identity, dict) or set(signed_identity) != {
        "schema", "source_commit_sha", "source_tree_digest", "capsule_digest", "capsule_digests",
        "image_digests", "runtime_pack_digest", "policy_bundle_digest", "infra_manifest_digest",
        "sbom_digest", "provenance_digest",
    }:
        _deny("ReleaseSet identity schema is unsupported by the Agent")
    source_root = _in_root(root, str(policy.get("source_marker") or "")).parent
    if _git(source_root, "status", "--porcelain=v1", "--untracked-files=all"):
        _deny("Git source tree is not clean")
    source_commit = _git(source_root, "rev-parse", "HEAD")
    source_tree = _git(source_root, "rev-parse", "HEAD^{tree}")
    if not re.fullmatch(r"[a-f0-9]{40,64}", source_commit) or not re.fullmatch(r"[a-f0-9]{40,64}", source_tree):
        _deny("Git source identity is malformed")
    manifest = _trusted_path(root, policy.get("capsule_manifest"))
    capsule_digest = _hash_file(manifest)
    components = _measure_components(root, release_set)
    return {
        "schema": "konnaxiondiag.release-set.identity.v1",
        "source_commit_sha": source_commit,
        "source_tree_digest": "git-sha1:" + source_tree,
        "capsule_digest": capsule_digest,
        "capsule_digests": [capsule_digest],
        "image_digests": _measure_images(runtime),
        **{name + "_digest": components[name] for name in _COMPONENTS},
    }


def check_activation(instance_id: str, runtime: Any) -> dict[str, Any]:
    """Measure and verify the exact VPS release; raises on every uncertainty.

    Called after image loading and before down/migrations/up. Nothing from an
    HTTP request may select the policy, paths, keys, digest or PEP verdict.
    """
    policy = _policy(instance_id)
    deployment_root = Path(str(policy.get("deployment_root") or ""))
    if not deployment_root.is_absolute() or not deployment_root.is_dir() or deployment_root.is_symlink():
        _deny("trusted deployment root missing or invalid")
    root = deployment_root.resolve(strict=True)
    release_set = _read_json(_trusted_path(root, policy.get("release_set")))
    verdict = _read_json(_trusted_path(root, policy.get("verdict")))
    signature = _read_json(_trusted_path(root, policy.get("signature")))
    measured_identity = _measure_identity(root, policy, release_set, runtime)
    signed_identity = release_set.get("identity")
    if measured_identity != signed_identity:
        _deny("measured deployment identity differs from ReleaseSet")
    measured_digest = _hash_json(measured_identity)
    if measured_digest != release_set.get("release_set_digest"):
        _deny("measured ReleaseSet digest differs from signed release")
    keys = policy.get("trusted_public_keys")
    if not isinstance(keys, list) or not keys:
        _deny("no Agent-trusted release authority keys configured")
    trusted = []
    for raw in keys:
        key = Path(str(raw))
        if not key.is_absolute() or not key.is_file() or key.is_symlink():
            _deny("trusted release authority key missing or invalid")
        if os.name != "nt" and (key.stat().st_mode & 0o022):
            _deny("release authority key writable by untrusted actors")
        trusted.append(key)
    try:
        from assurance.pep import verify_activation_admission
    except ImportError:
        _deny("KonnaxionDiag 4.2.5 strict PEP is unavailable on this Agent")
    try:
        passed, detail = verify_activation_admission(
            verdict, signature,
            trusted_public_keys=trusted, active_release_set=release_set,
            measured_release_set_digest=measured_digest,
            expected_policy_version=policy["policy_version"],
        )
    except Exception:
        _deny("KonnaxionDiag strict verification failed unexpectedly")
    if not passed:
        _deny("KonnaxionDiag denied the measured release")
    return {"admitted": True, "release_set_digest": measured_digest,
            "policy_version": policy["policy_version"], "image_count": len(measured_identity["image_digests"])}


def admission_required(instance_id: str) -> bool:
    """Require strict admission for public VPS or Agent-owned enforced profile."""
    from kx_agent.config import get_agent_config
    from kx_agent.instances.secrets import read_instance_env_files

    config = get_agent_config()
    # Agent operator can enforce it on every instance independently of the
    # caller-supplied network profile. Never accept a request-side skip flag.
    mode = os.environ.get("KX_RELEASE_ACTIVATION_MODE", "").strip().lower()
    if mode not in {"", "enforce"}:
        _deny("unsupported Agent-owned admission mode; expected enforce or unset")
    if mode == "enforce" or config.is_public_mode:
        return True
    env = read_instance_env_files(instance_id)
    return (str(env.get("KX_NETWORK_PROFILE") or "").strip().lower()
            in {"public_vps", "public_temporary"} or
            str(env.get("KX_PUBLIC_MODE_ENABLED") or "").strip().lower() in {"true", "1"})


def enforce_for_start(instance_id: str, runtime: Any) -> dict[str, Any] | None:
    if not admission_required(instance_id):
        return None
    return check_activation(instance_id, runtime)
