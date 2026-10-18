"""Offline tests; no Docker, Postgres, cloud, API or real seed writes."""
from __future__ import annotations
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.tandem_v2 import release as r


class TandemV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.kxw = self.root / "Konnaxion_Worlds"
        self.ow = self.root / "Orgo_Worlds"
        self.orgo = self.root / "Orgo"
        self.capsule = self.root / "Capsule"
        worlds = self.kxw / "universes/fakeplant"
        (worlds / "integrations").mkdir(parents=True)
        (worlds / "worlds/fp-1/scenarios").mkdir(parents=True)
        (self.ow / "world-packs/fakeplant/scenarios").mkdir(parents=True)
        (self.orgo / "apps/api").mkdir(parents=True)
        (self.orgo / "apps/web").mkdir(parents=True)
        (self.orgo / "tools/scenario-injector").mkdir(parents=True)
        (self.ow / "tools/world-scenario").mkdir(parents=True)
        (self.capsule / "kx_builder").mkdir(parents=True)
        (self.capsule / "tools/tandem").mkdir(parents=True)
        shutil.copyfile(ROOT / "tools/tandem/preflight.py", self.capsule / "tools/tandem/preflight.py")
        (self.capsule / "kx_builder/images.py").write_text("# fixture")
        (self.orgo / "apps/api/src/orgo/adapters/inbound/http").mkdir(parents=True)
        (self.orgo / "apps/api/src/worker.ts").parent.mkdir(parents=True,exist_ok=True)
        (self.orgo / "apps/api/prisma").mkdir(parents=True)
        (self.orgo / "docker-compose.yml").write_text("services: {}")
        (self.orgo / "package.json").write_text("{}")
        (self.orgo / "package-lock.json").write_text("{}")
        (self.orgo / "apps/api/prisma/schema.prisma").write_text("model Organization {}")
        (self.orgo / "apps/api/src/worker.ts").write_text("// worker fixture")
        (self.orgo / "apps/api/src/orgo/adapters/inbound/http/interaction-kernel.controller.ts").write_text("// IK fixture")
        (self.orgo / "apps/api/Dockerfile").write_text("FROM scratch\n")
        (self.orgo / "apps/web/Dockerfile").write_text("FROM scratch\n")
        (self.orgo / "tools/scenario-injector/cli.mjs").write_text("// fixture")
        (self.ow / "tools/world-scenario/cli.mjs").write_text("// fixture")
        self.spec = {"contract":"koa.tandem-universe/v1", "universe_key":"fakeplant", "synthetic": True,
                     "source_state_ref":"urn:kristal:state:fakeplant-organisation", "konnaxion":{"manifest":"universe.yaml","worlds_dir":"worlds"},
                     "orgo":{"organization_slug":"fakeplant","world_pack_key":"fakeplant","checkpoint_count":1,"injector_schema":"orgo.scenario.v1","apply_mode":"explicit-only"}}
        (worlds / "integrations/orgo.tandem.json").write_text(json.dumps(self.spec))
        (worlds / "universe.yaml").write_text("universe_contract: kx-universe-pack/v1\nuniverse_key: fakeplant\nworlds:\n- world_key: fp-1\n")
        (worlds / "worlds/fp-1/world.yaml").write_text("world_contract: kx-world-pack/v1\nworld_key: fp-1\nscenarios:\n- scenarios/demo.json\n")
        (worlds / "worlds/fp-1/scenarios/demo.json").write_text("{}")
        pack = {"format": "orgo.world.pack.v1", "world":{"key":"fakeplant"},
                "target":{"organization_key":"fakeplant","injector_schema":"orgo.scenario.v1"},
                "identifiers":{"source_ref":"urn:kristal:state:fakeplant-organisation"},
                "checkpoint_order":["FP-S01"],"checkpoints":{"FP-S01":{"scenario":"scenarios/FP-S01.scenario.json"}}}
        (self.ow / "world-packs/fakeplant/world-pack.json").write_text(json.dumps(pack))
        (self.ow / "world-packs/fakeplant/scenarios/FP-S01.scenario.json").write_text(json.dumps({"schema_version":"orgo.scenario.v1","scenario":{"synthetic":True}}))
        self.binding = {"contract":"koa.tandem-identity/v1", "universe_key":"fakeplant", "orgo_organization_slug":"fakeplant",
                        "orgo_organization_id":"12345678-1234-4234-8234-123456789abc", "konnaxion_origin":"https://fakeplant.konnaxion.com",
                        "orgo_origin":"https://fakeplant.orgo.konnaxion.com", "orgo_worlds_api_origin":"https://orgo-worlds.internal.example.com",
                        "ik_envelope":"ik/1.1","orgo_workflow_version":"published-version-1","orgo_acl_reviewed":True,"konnaxion_acl_reviewed":True}
        self.binding_file = self.root / "binding.json"
        self.write_binding()
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        private = Ed25519PrivateKey.generate()
        self.private = self.root / "key-private.pem"
        self.public = self.root / "key-public.pem"
        self.private.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        self.kxcap = self.root / "konnaxion.kxcap"
        self.kxcap.write_bytes(b"unit-test-capsule-binary")

    def write_binding(self):
        self.binding_file.write_text(json.dumps(self.binding))

    def inspect(self, binding=True):
        return r.inspect_sources(universe="fakeplant", kx_worlds=self.kxw, orgo_worlds=self.ow, orgo=self.orgo,
                                 capsule=self.capsule, binding_file=self.binding_file if binding else None)

    def test_preflight_readonly_without_binding(self):
        out=self.inspect(False)
        self.assertTrue(out["ok"])
        self.assertFalse(out["ready_to_build"])
        self.assertFalse(out["remote_deploy_supported"])
        self.assertEqual(out["world_count"],1)
        self.assertEqual(out["checkpoint_count"],1)

    def test_preflight_reject_missing_ik_controller(self):
        (self.orgo / "apps/api/src/orgo/adapters/inbound/http/interaction-kernel.controller.ts").unlink()
        with self.assertRaisesRegex(r.TandemError,"IK indispensable"):self.inspect()

    def test_missing_package_lock_blocks_build(self):
        (self.orgo / "package-lock.json").unlink()
        out=self.inspect()
        self.assertTrue(out["ok"])
        self.assertFalse(out["ready_to_build"])
        self.assertFalse(out["orgo_package_lock_present"])

    def test_preflight_with_binding(self):
        x=self.inspect()
        self.assertTrue(x["ready_to_build"])
        self.assertEqual(x["binding"]["orgo_organization_id"],self.binding["orgo_organization_id"])

    def test_preflight_reject_wrong_tenant(self):
        self.binding["orgo_organization_id"]="fakeplant"
        self.write_binding()
        with self.assertRaisesRegex(r.TandemError,"UUID réel"):self.inspect()

    def test_preflight_reject_mismatch_slug(self):
        self.binding["orgo_organization_slug"]="other"
        self.write_binding()
        with self.assertRaisesRegex(r.TandemError,"Slug"):self.inspect()

    def test_preflight_reject_token_in_binding(self):
        self.binding["api_token"]="supersecret"
        self.write_binding()
        with self.assertRaisesRegex(r.TandemError,"Secrets interdits"):self.inspect()

    def test_preflight_reject_http_origins(self):
        self.binding["orgo_origin"]="http://fakeplant.orgo.konnaxion.com"
        self.write_binding()
        with self.assertRaisesRegex(r.TandemError,"HTTPS"):self.inspect()

    def test_preflight_reject_local_worlds(self):
        self.binding["orgo_worlds_api_origin"]="https://host.docker.internal:4100"
        self.write_binding()
        with self.assertRaisesRegex(r.TandemError,"HTTPS"):self.inspect()

    def test_preflight_reject_unreviewed_acl(self):
        self.binding["orgo_acl_reviewed"]=False
        self.write_binding()
        with self.assertRaisesRegex(r.TandemError,"ACL"):self.inspect()

    def test_scenario_mutation_changes_source_lock(self):
        before=self.inspect()["source_lock_sha256"]
        path=self.ow/"world-packs/fakeplant/scenarios/FP-S01.scenario.json"
        obj=json.loads(path.read_text());obj["extra"]="modified";path.write_text(json.dumps(obj))
        self.assertNotEqual(before,self.inspect()["source_lock_sha256"])

    def test_unsafe_world_symlink_rejected(self):
        f=self.kxw/"universes/fakeplant/worlds/fp-1/scenarios/else.json"
        try:f.symlink_to(self.kxcap)
        except (OSError, NotImplementedError):self.skipTest("symlink unsupported")
        with self.assertRaisesRegex(r.TandemError,"Symlink"):self.inspect()

    def mock_run(self, args, cwd, timeout):
        if args[:2] == ["docker","save"]:
            file=Path(args[args.index("--output")+1]); file.parent.mkdir(parents=True,exist_ok=True)
            payload=json.dumps([{"Config":"0123.json","RepoTags":["image:test"],"Layers":[]}]).encode()
            with tarfile.open(file,"w") as tar:
                t=tarfile.TarInfo("manifest.json");t.size=len(payload);tar.addfile(t,io.BytesIO(payload))
        if args[:3] == ["docker","image","inspect"]:return "sha256:" + "a"*64
        return "ok"

    def build(self, release_id="fakeplant-v2-001"):
        return r.build_release(preflight=self.inspect(),capsule_root=self.capsule,orgo=self.orgo,
                               kxcap=self.kxcap,public_key=self.public,private_key=self.private,
                               output=self.root/"dist",release_id=release_id,
                               orgo_worlds_api_origin=self.binding["orgo_worlds_api_origin"])

    def test_build_release_signed_and_verify(self):
        with patch.object(r,"_run",self.mock_run):
            result=self.build()
        self.assertTrue(result["ok"])
        path=self.root/"dist/fakeplant-v2-001"
        doc=json.loads((path/"release.json").read_text())
        self.assertEqual(set(doc["artifacts"]),{"konnaxion","orgo_api","orgo_web"})
        self.assertEqual(doc["deployment"]["go_live"],"blocked")
        self.assertTrue(r.verify_release(path,self.public)["signature_valid"])

    def test_deployment_plan_blocked_and_two_databases(self):
        with patch.object(r,"_run",self.mock_run):self.build()
        plan=r.deployment_plan(self.root/"dist/fakeplant-v2-001",self.public)
        self.assertEqual(plan["operator_action"],"NONE - PLANNING ONLY")
        self.assertIn("BLOCKED",plan["status"])
        self.assertNotEqual(plan["products"]["konnaxion"]["database"],plan["products"]["orgo"]["database"])

    def test_tampering_release_manifest_rejected(self):
        with patch.object(r,"_run",self.mock_run):self.build()
        path=self.root/"dist/fakeplant-v2-001"
        doc=json.loads((path/"release.json").read_text());doc["identity"]["orgo_organization_id"]="changed"
        (path/"release.json").write_text(json.dumps(doc))
        with self.assertRaisesRegex(r.TandemError,"Release modifiée"):r.verify_release(path,self.public)

    def test_tampering_image_rejected(self):
        with patch.object(r,"_run",self.mock_run):self.build()
        path=self.root/"dist/fakeplant-v2-001"
        with (path/"artifacts/orgo_api.oci.tar").open("ab") as out:out.write(b"corruption")
        with self.assertRaisesRegex(r.TandemError,"empreinte divergente"):r.verify_release(path,self.public)

    def test_wrong_key_rejected(self):
        with patch.object(r,"_run",self.mock_run):self.build()
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        pk=Ed25519PrivateKey.generate().public_key()
        wrong=self.root/"wrong.pem";wrong.write_bytes(pk.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
        with self.assertRaisesRegex(r.TandemError,"Signature/clé"):r.verify_release(self.root/"dist/fakeplant-v2-001",wrong)

    def test_build_failure_cleans_staging(self):
        def fail_on_build(args,cwd,timeout):
            if args[:2]==["docker","build"]:raise r.TandemError("Docker failed")
            return "ok"
        with patch.object(r,"_run",fail_on_build):
            with self.assertRaisesRegex(r.TandemError,"Docker failed"):self.build()
        self.assertFalse((self.root/"dist/fakeplant-v2-001").exists())
        self.assertFalse(list((self.root/"dist").iterdir()))

    def test_existing_release_not_overwritten(self):
        (self.root/"dist/fakeplant-v2-001").mkdir(parents=True)
        with self.assertRaisesRegex(r.TandemError,"existe déjà"):self.build()

    def test_unmatched_signing_pair_rejected(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        pk=Ed25519PrivateKey.generate().public_key()
        self.public.write_bytes(pk.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
        with patch.object(r,"_run",self.mock_run):
            with self.assertRaisesRegex(r.TandemError,"Paire de clés"):self.build()


if __name__=="__main__":unittest.main(verbosity=2)
