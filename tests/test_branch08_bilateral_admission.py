"""Branch 08: optional real Agent -> KonnaxionDiag PEP contract run.

Set KONNAXIONDIAG_SOURCE to an exact, separately supplied diagnostic checkout.
No external service, real host, release install, or private key is accessed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from kx_agent.security import release_admission as ra
from kx_shared.errors import SecurityGateBlockingError


@pytest.fixture
def producer(monkeypatch):
    source = os.environ.get("KONNAXIONDIAG_SOURCE")
    if not source or not (Path(source) / "assurance/pep.py").is_file():
        pytest.skip("KONNAXIONDIAG_SOURCE required for real two-repository test")
    pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    monkeypatch.syspath_prepend(source)
    from diagcore.signing import sign_payload
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
    return sign_payload, ed25519, serialization


class Runtime:
    _ref = "registry.invalid/stub@sha256:" + "b" * 64
    _digest = "sha256:" + "a" * 64

    def __init__(self):
        self.image_id = self._digest

    def _compose_args(self, args):
        assert args == ["config", "--format", "json"]
        return SimpleNamespace(ok=True, stdout=json.dumps({"services": {"app": {"image": self._ref}}}))

    def image_inspect(self, ref):
        return SimpleNamespace(image_id=self.image_id, repo_digests=(ref,))


def fixture(tmp_path, monkeypatch, producer):
    sign_payload, ed25519, serialization = producer
    base = tmp_path / "deployment"
    base.mkdir()
    src = base / "source"
    src.mkdir()
    (src / "marker").write_text("ok", encoding="utf8")
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run(["git", "-C", str(src), "add", "marker"], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.email=test@invalid",
                    "-c", "user.name=Test", "commit", "-qm", "base"], check=True)
    (base / "capsule.json").write_text("capsule", encoding="utf8")
    files = {}
    for component in ra._COMPONENTS:
        file = base / component / "component.txt"
        file.parent.mkdir()
        file.write_text(component, encoding="utf8")
        files[component] = [{"path": f"{component}/component.txt", "digest": ra._hash_file(file)}]

    secret = ed25519.Ed25519PrivateKey.generate()
    pub = tmp_path / "authority.pub"
    pub.write_bytes(secret.public_key().public_bytes(serialization.Encoding.PEM,
                                                      serialization.PublicFormat.SubjectPublicKeyInfo))
    priv = tmp_path / "authority.key"
    priv.write_bytes(secret.private_bytes(serialization.Encoding.PEM,
                                          serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    policy = {
        "schema": "koa.agent.release-admission.v1", "instance_id": "test-instance",
        "policy_version": "policy-v1", "deployment_root": str(base),
        "source_marker": "source/marker", "capsule_manifest": "capsule.json",
        "release_set": "release-set.json", "verdict": "verdict.json", "signature": "signature.json",
        "trusted_public_keys": [str(pub)],
    }
    runtime = Runtime()
    release = {"schema": "konnaxiondiag.release-set.v1", "component_files": files}
    release["identity"] = {key: None for key in (
        "schema", "source_commit_sha", "source_tree_digest", "capsule_digest", "capsule_digests",
        "image_digests", "runtime_pack_digest", "policy_bundle_digest", "infra_manifest_digest",
        "sbom_digest", "provenance_digest",
    )}
    release["identity"] = ra._measure_identity(base, policy, release, runtime)
    release["release_set_digest"] = ra._hash_json(release["identity"])
    release.update({
        "required_components": ["source", "capsule", "images", *ra._COMPONENTS],
        "security_evidence_set_digest": "sha256:" + "c" * 64,
        "qualification_policy_digest": "sha256:" + "d" * 64,
        "security_contract_registry_digest": "sha256:" + "e" * 64,
        "complete": True, "problems": [],
    })
    now = datetime.now(timezone.utc)
    verdict = {
        "schema": "konnaxiondiag.release-verdict.v5", "run_id": "test-run", "verdict": "PASS",
        "authorization": {
            "schema": "konnaxiondiag.release-authorization.v1",
            "subject": release["release_set_digest"], "verdict": "PASS",
            "policy_version": "policy-v1", "run_id": "test-run", "nonce": "0123456789abcdefghijkl",
            "issued_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        },
        "release_set": release,
        "subject_binding": {"required": True, "valid": True,
                            "release_set_digest": release["release_set_digest"]},
        "security": {"clean": True, "s14_verdict": "PASS"},
        "functional": {"clean": True}, "target_protection": {"verdict": "PASS"},
        "signature": {"status": "SIGNED"},
        "consumer_contract": {"caller_may_disable_gate": False},
    }
    signature = sign_payload(verdict, priv)
    for name, value in (("release-set.json", release), ("verdict.json", verdict),
                        ("signature.json", signature)):
        (base / name).write_text(json.dumps(value), encoding="utf8")
    monkeypatch.setattr(ra, "_policy", lambda instance: policy)
    return base, policy, release, verdict, runtime


def test_real_koa_agent_calls_real_konnaxiondiag_pep(tmp_path, monkeypatch, producer):
    _base, policy, release, _verdict, runtime = fixture(tmp_path, monkeypatch, producer)
    result = ra.check_activation("test-instance", runtime)
    assert result["admitted"] is True
    assert result["release_set_digest"] == release["release_set_digest"]
    assert result["policy_version"] == policy["policy_version"]


def test_real_pep_denies_changed_image_or_signed_verdict(tmp_path, monkeypatch, producer):
    base, _policy, _release, _verdict, runtime = fixture(tmp_path, monkeypatch, producer)
    runtime.image_id = "sha256:" + "f" * 64
    with pytest.raises(SecurityGateBlockingError):
        ra.check_activation("test-instance", runtime)
    runtime.image_id = Runtime._digest
    verdict_file = base / "verdict.json"
    verdict = json.loads(verdict_file.read_text(encoding="utf8"))
    verdict["authorization"]["policy_version"] = "attacker-changed"
    verdict_file.write_text(json.dumps(verdict), encoding="utf8")
    with pytest.raises(SecurityGateBlockingError, match="denied"):
        ra.check_activation("test-instance", runtime)
