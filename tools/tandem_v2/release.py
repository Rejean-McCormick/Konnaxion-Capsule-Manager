"""Tandem V2: source/identity preflight, *two-product* build, signed local release lock.

This builder NEVER deploys, uploads, provisions DNS, writes to databases, or imports seeds.
The remote Konnaxion Agent has no composite runtime support yet. Fail CLOSED.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

PREFIX = b"KOA-TANDEM-RELEASE-V2\n"
KEY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
SCHEMA = "koa.tandem-release/v2"


class TandemError(ValueError):
    pass


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf8")


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _preflight(capsule_root: Path):
    path = capsule_root / "tools" / "tandem" / "preflight.py"
    if not path.is_file():
        raise TandemError("Préflight V1 absent dans le Capsule Manager")
    spec = importlib.util.spec_from_file_location("koa_tandem_preflight_v1", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_url(value: str, *, what: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError()
        if parsed.hostname in ("localhost", "127.0.0.1", "host.docker.internal"):
            raise ValueError()
    except ValueError:
        raise TandemError(f"{what} doit être une origine HTTPS explicite sans secrets ni chemin")
    return value.rstrip("/")


def binding_info(binding_file: Path, universe: str) -> dict:
    """Validate explicit tenant binding; never assume organization_slug is a tenant ID."""
    if not binding_file.is_file():
        raise TandemError(f"Binding introuvable : {binding_file}")
    binding = json.loads(binding_file.read_text(encoding="utf-8"))
    if binding.get("contract") != "koa.tandem-identity/v1" or binding.get("universe_key") != universe:
        raise TandemError("Binding Universe/contrat divergent")
    if binding.get("orgo_organization_slug") != universe:
        raise TandemError("Slug Orgo != Universe")
    raw_uuid = str(binding.get("orgo_organization_id") or "")
    try:
        organization_id = str(uuid.UUID(raw_uuid))
    except ValueError:
        raise TandemError("orgo_organization_id : UUID réel Orgo requis (pas le slug)")
    if raw_uuid.lower() != organization_id:
        raise TandemError("UUID Orgo non canonique")
    if binding.get("ik_envelope") != "ik/1.1":
        raise TandemError("Contrat IK attendu: ik/1.1")
    if binding.get("orgo_workflow_version") in (None, "", "CHANGE_ME"):
        raise TandemError("WorkflowVersion Orgo publié à renseigner")
    kx_origin = _check_url(str(binding.get("konnaxion_origin", "")), what="konnaxion_origin")
    orgo_origin = _check_url(str(binding.get("orgo_origin", "")), what="orgo_origin")
    _check_url(str(binding.get("orgo_worlds_api_origin", "")), what="orgo_worlds_api_origin")
    if kx_origin == orgo_origin:
        raise TandemError("Les deux produits doivent avoir des origines distinctes")
    if binding.get("orgo_acl_reviewed") is not True or binding.get("konnaxion_acl_reviewed") is not True:
        raise TandemError("Les ACL doivent être vérifiées séparément")
    disallowed = {"token", "password", "secret", "credential", "database_url", "api_key", "private_key"}
    for name in binding:
        if any(word in name.lower() for word in disallowed):
            raise TandemError(f"Secrets interdits dans le binding: {name}")
    return binding


def inspect_sources(*, universe: str, kx_worlds: Path, orgo_worlds: Path, orgo: Path, capsule: Path, binding_file: Path | None = None) -> dict:
    if not KEY_RE.fullmatch(universe):
        raise TandemError("Clé Universe invalide")
    report = _preflight(capsule).inspect(universe, kx_worlds, orgo_worlds, orgo, capsule)
    # Lock ALL source files, not merely the top manifests, to detect scenario drift.
    kx_dir = (kx_worlds / "universes" / universe).resolve()
    orgo_pack = (orgo_worlds / "world-packs" / universe).resolve()
    source_roots = {"konnaxion_worlds": kx_dir, "orgo_worlds": orgo_pack}
    runtime_files = (
        "docker-compose.yml", "package.json", "apps/api/Dockerfile", "apps/web/Dockerfile",
        "apps/api/src/orgo/adapters/inbound/http/interaction-kernel.controller.ts",
        "apps/api/src/worker.ts", "apps/api/prisma/schema.prisma",
    )
    runtime_inventory = {}
    for relative in runtime_files:
        path = orgo / relative
        if not path.is_file() or path.is_symlink():
            raise TandemError(f"Composant Orgo/IK indispensable manquant: {relative}")
        runtime_inventory[relative] = file_hash(path)
    # SmartDumps may exclude package-lock.json; an actual Docker build cannot.
    lock_file = orgo / "package-lock.json"
    lock_present = lock_file.is_file() and not lock_file.is_symlink()
    if lock_present:
        runtime_inventory["package-lock.json"] = file_hash(lock_file)
    inventory = {}
    for kind, root in source_roots.items():
        rows = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise TandemError(f"Symlink interdit : {path}")
            if path.is_file():
                rows[path.relative_to(root).as_posix()] = file_hash(path)
        inventory[kind] = rows
    inventory["orgo_runtime"] = runtime_inventory
    binding = binding_info(binding_file, universe) if binding_file else None
    return {
        "ok": True,
        "schema": "koa.tandem-preflight/v2",
        "universe": universe,
        "world_count": report["konnaxion"]["world_count"],
        "checkpoint_count": report["orgo"]["checkpoint_count"],
        "sources": inventory,
        "source_lock_sha256": hashlib.sha256(canonical(inventory)).hexdigest(),
        "identity_bound": binding is not None,
        "binding": binding,
        "orgo_package_lock_present": lock_present,
        "ready_to_build": binding is not None and lock_present,
        "remote_deploy_supported": False,
        "go_live": "BLOCKED_COMPOSITE_AGENT_AND_IK_E2E",
    }


def _run(args: list[str], cwd: Path, timeout: int) -> str:
    try:
        completed = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TandemError(f"Commande impossible: {args[0]}: {exc}") from exc
    if completed.returncode:
        # Do not include arbitrary stdout/stderr: build output may contain secrets.
        raise TandemError(f"Commande échouée ({completed.returncode}): {args[0]} {args[1] if len(args)>1 else ''} — consulter les journaux du poste")
    return completed.stdout.strip()


def verify_konnaxion_capsule(capsule: Path, kxcap: Path, public_key: Path) -> None:
    if not kxcap.is_file() or kxcap.suffix != ".kxcap" or not public_key.is_file():
        raise TandemError("Capsule .kxcap signée et clé publique existantes requises")
    _run(["uv", "run", "--no-sync", "kx-builder", "capsule", "verify", str(kxcap), "--strict", "--public-key-file", str(public_key), "--require-signature-verifier"], capsule, 900)


def _docker_image_info(tag: str, cwd: Path) -> str:
    value = _run(["docker", "image", "inspect", "--format", "{{.Id}}", tag], cwd, 60)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise TandemError("Docker n'a pas rendu d'identité sha256 d'image")
    return value


def _validate_docker_tar(path: Path):
    with tarfile.open(path, "r:") as tf:
        member = tf.getmember("manifest.json")
        if member.size <= 0 or member.size > 8 * 1024 * 1024:
            raise TandemError("Archive Docker manifest.json invalide")
        manifests = json.load(tf.extractfile(member))
        if not isinstance(manifests, list) or len(manifests) != 1 or not manifests[0].get("Config"):
            raise TandemError("Archive Docker export invalide")


def _sign(doc: dict, private_key: Path, public_key: Path) -> dict:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    private = load_pem_private_key(private_key.read_bytes(), password=None)
    public = load_pem_public_key(public_key.read_bytes())
    if not isinstance(private, Ed25519PrivateKey) or not isinstance(public, Ed25519PublicKey):
        raise TandemError("Clés Ed25519 exclusivement")
    payload = PREFIX + canonical(doc)
    signature = private.sign(payload)
    try:
        public.verify(signature, payload)
    except Exception as exc:
        raise TandemError("Paire de clés Ed25519 non correspondante") from exc
    return {"algorithm": "ed25519", "purpose": SCHEMA, "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "signature_base64": base64.b64encode(signature).decode(), "public_key_sha256": file_hash(public_key)}


def verify_release(root: Path, public_key: Path, *, verify_kx: bool = False, capsule: Path | None = None) -> dict:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    doc = json.loads((root / "release.json").read_text(encoding="utf8"))
    signature = json.loads((root / "signature.json").read_text(encoding="utf8"))
    if doc.get("schema") != SCHEMA or doc.get("deployment", {}).get("go_live") != "blocked":
        raise TandemError("Contrat de release invalide ou gate GO LIVE modifié")
    if signature.get("algorithm") != "ed25519" or signature.get("purpose") != SCHEMA or signature.get("public_key_sha256") != file_hash(public_key):
        raise TandemError("Signature/clé publique incompatible")
    payload = PREFIX + canonical(doc)
    if signature.get("payload_sha256") != hashlib.sha256(payload).hexdigest():
        raise TandemError("Release modifiée depuis la signature")
    try:
        load_pem_public_key(public_key.read_bytes()).verify(base64.b64decode(signature["signature_base64"], validate=True), payload)
    except Exception as exc:
        raise TandemError("Signature Ed25519 invalide") from exc
    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"konnaxion", "orgo_api", "orgo_web"}:
        raise TandemError("La release doit contenir les 3 artefacts (Kx, Orgo API/Worker, Orgo Web)")
    for name, meta in artifacts.items():
        file = root / meta["filename"]
        if file.parent.resolve() != (root / "artifacts").resolve() or file.is_symlink() or not file.is_file() or file_hash(file) != meta["sha256"]:
            raise TandemError(f"Artefact {name} absent ou empreinte divergente")
        if name.startswith("orgo_"):
            _validate_docker_tar(file)
    if verify_kx:
        if capsule is None:
            raise TandemError("--capsule-manager requis pour vérifier la signature Konnaxion")
        verify_konnaxion_capsule(capsule, root / artifacts["konnaxion"]["filename"], public_key)
    return {"ok": True, "universe": doc["universe_key"], "release_id": doc["release_id"],
            "artifacts": list(artifacts), "signature_valid": True, "go_live": "BLOCKED"}


def build_release(*, preflight: dict, capsule_root: Path, orgo: Path, kxcap: Path, public_key: Path, private_key: Path, output: Path, release_id: str, orgo_worlds_api_origin: str) -> dict:
    if not preflight.get("ready_to_build") or not preflight.get("binding") or preflight["binding"]["universe_key"] != preflight["universe"]:
        raise TandemError("Mapping tenant/ACL manquant ou non validé")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,48}", release_id):
        raise TandemError("release_id invalide")
    orgo_worlds_api_origin = _check_url(orgo_worlds_api_origin, what="orgo_worlds_api_origin")
    if orgo_worlds_api_origin != preflight["binding"]["orgo_worlds_api_origin"].rstrip("/"):
        raise TandemError("URL Orgo_Worlds divergente du binding")
    if not private_key.is_file():
        raise TandemError("Clé privée Ed25519 absente")
    if (output / release_id).exists():
        raise TandemError("Release ID existe déjà (écrasement interdit)")
    if not (orgo / "package-lock.json").is_file():
        raise TandemError("Orgo/package-lock.json est requis pour le build npm ci (absent du snapshot ?)")
    for dockerfile in ("apps/api/Dockerfile", "apps/web/Dockerfile"):
        if not (orgo / dockerfile).is_file():
            raise TandemError(f"Dockerfile Orgo absent: {dockerfile}")
    verify_konnaxion_capsule(capsule_root, kxcap, public_key)
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{release_id}-", dir=output))
    try:
        artifacts = staging / "artifacts"
        artifacts.mkdir()
        kx_copy = artifacts / "konnaxion.kxcap"
        shutil.copyfile(kxcap, kx_copy)
        images = {}
        for short, dockerfile in (("orgo_api", "apps/api/Dockerfile"), ("orgo_web", "apps/web/Dockerfile")):
            tag = f"koa-tandem/{short.replace('_', '-')}:" + release_id
            cmd = ["docker", "build", "--file", dockerfile, "--tag", tag]
            if short == "orgo_web":
                cmd += ["--build-arg", "ORGO_API_URL=http://api:4000", "--build-arg", f"ORGO_WORLDS_API_URL={orgo_worlds_api_origin}/api"]
            cmd.append(".")
            _run(cmd, orgo, 2400)
            image_id = _docker_image_info(tag, orgo)
            archive = artifacts / f"{short}.oci.tar"
            _run(["docker", "save", "--output", str(archive), tag], orgo, 1200)
            _validate_docker_tar(archive)
            images[short] = {"filename": f"artifacts/{archive.name}", "sha256": file_hash(archive), "image_id": image_id, "service": short}
        lock = {
            "schema": SCHEMA, "release_id": release_id, "universe_key": preflight["universe"],
            "identity": {"orgo_organization_id": preflight["binding"]["orgo_organization_id"],
                         "orgo_organization_slug": preflight["binding"]["orgo_organization_slug"],
                         "orgo_workflow_version": preflight["binding"]["orgo_workflow_version"],
                         "ik_envelope": "ik/1.1", "orgo_acl_reviewed": True, "konnaxion_acl_reviewed": True},
            "sources_sha256": preflight["source_lock_sha256"],
            "source_file_count": {k: len(v) for k, v in preflight["sources"].items()},
            "artifacts": {"konnaxion": {"filename": "artifacts/konnaxion.kxcap", "sha256": file_hash(kx_copy)}, **images},
            "deployment": {"go_live": "blocked", "remote_agent_composite_supported": False,
                           "independent_databases": True, "seed_injection": "explicit-only", "tandem_worker": "orgo_api_image_alias",
                           "pending": ["agent_orchestration", "migrations_backups", "ik_credentials_scopes", "health_worker_e2e", "two_service_rollback", "dns_tls_sso"]},
        }
        (staging / "release.json").write_bytes(canonical(lock) + b"\n")
        (staging / "signature.json").write_bytes(canonical(_sign(lock, private_key, public_key)) + b"\n")
        verified = verify_release(staging, public_key)
        staging.rename(output / release_id)
        return verified | {"release_dir": str(output / release_id)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def deployment_plan(root: Path, public_key: Path) -> dict:
    """Read-only two-product deployment dependency graph. NOT executable by Agent V1."""
    verification = verify_release(root, public_key)
    doc = json.loads((root / "release.json").read_text(encoding="utf8"))
    return {
        "schema": "koa.tandem-deploy-plan/v2",
        "release_id": doc["release_id"], "universe_key": doc["universe_key"],
        "artifact_verification": verification,
        "status": "BLOCKED - no composite Konnaxion Agent support",
        "products": {
            "konnaxion": {"artifact": "konnaxion.kxcap", "runtime": "existing Kx Agent", "database": "separate Konnaxion PostgreSQL"},
            "orgo": {"images": ["orgo_api.oci.tar", "orgo_web.oci.tar"],
                     "worker_image_alias": "orgo_api.oci.tar", "database": "separate Orgo PostgreSQL", "migrate": "prisma migrate deploy"},
        },
        "preflight_gates": [
            "target_orgo_organization_uuid_proved_against_runtime_db",
            "signed_konnaxion_capsule_and_joint_release_verified",
            "orgo_images_and_worker_verified",
            "orgo_worlds_api_available_over_authorized_tls",
            "konnaxion_and_orgo_databases_backed_up_separately",
            "orgo_workflow_version_published_and_tenant_acl_verified",
            "oidc_and_service_token_scopes_validated_at_runtime",
            "tls_dns_both_hosts_and_callback_urls_provisioned",
            "konnaxion_ik_outbox_and_orgo_ik_worker_cross_system_e2e",
            "rollback_drill_konnaxion_success_orgo_failure_and_inverse",
        ],
        "sequence": [
            "VERIFY_AND_FREEZE_BOTH_PRODUCTS", "SNAPSHOT_BOTH_DATABASES", "STAGE_ORGO_IMAGES_NO_PUBLIC_PORTS",
            "RUN_ORGO_MIGRATIONS_ON_ORGO_DB_ONLY", "HEALTHCHECK_ORGO_API_WEB_WORKER_DB",
            "STAGE_KONNAXION_CAPSULE", "HEALTHCHECK_KONNAXION_AND_WORLD_CATALOG",
            "VERIFY_IK_TENANT_TOKEN_IDEMPOTENCE_RECEIPT", "SWITCH_BOTH_ROUTING_ENTRIES_ONLY_AFTER_ALL_GATES",
            "WATCH_RECEIPTS_AND_OUTBOX",
        ],
        "rollback": {"routing": "restore both prior routes and container image refs",
                     "data": "product-specific restore only, after an operator verifies compatibility; no cross-DB atomic rollback",
                     "inflight": "reconcile IK outboxes by idempotency key; never erase admitted work"},
        "operator_action": "NONE - PLANNING ONLY",
    }
