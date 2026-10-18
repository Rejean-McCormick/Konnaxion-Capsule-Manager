#!/usr/bin/env python3
"""Tandem Konnaxion + Orgo : préflight strictement read-only.

Ne contient volontairement aucune commande build/deploy/import/poste/DNS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ALLOWED_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def scalar(text: str, key: str) -> str | None:
    match = re.search(r"(?m)^" + re.escape(key) + r":\s*([^\n#]+)", text)
    return match.group(1).strip().strip("\"'") if match else None


def entry(base: Path, path: str, what: str) -> Path:
    if not isinstance(path, str) or not path or Path(path).is_absolute() or "\\" in path:
        raise ValueError(f"{what}: chemin non relatif")
    if any(part in ("", ".", "..") for part in Path(path).parts):
        raise ValueError(f"{what}: traversée interdite")
    root = base.resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{what}: chemin hors racine")
    if not target.is_file():
        raise ValueError(f"{what}: fichier manquant: {target}")
    return target


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(universe: str, worlds: Path, orgo_worlds: Path, orgo: Path, capsule: Path) -> dict:
    if not ALLOWED_KEY.fullmatch(universe):
        raise ValueError("Clé Universe invalide")
    universe_dir = (worlds / "universes" / universe).resolve()
    if not universe_dir.is_relative_to(worlds.resolve()):
        raise ValueError("Universe hors catalogue")
    tandem_file = entry(universe_dir, "integrations/orgo.tandem.json", "liaison")
    spec = json.loads(tandem_file.read_text(encoding="utf-8"))
    if spec.get("contract") != "koa.tandem-universe/v1" or spec.get("universe_key") != universe:
        raise ValueError("Contrat / Universe divergent")
    if spec.get("synthetic") is not True:
        raise ValueError("Préflight V1 réservé aux données synthétiques")
    kx = spec.get("konnaxion", {})
    kx_manifest = entry(universe_dir, kx.get("manifest", ""), "Universe YAML")
    kx_text = kx_manifest.read_text(encoding="utf-8-sig")
    if scalar(kx_text, "universe_key") != universe:
        raise ValueError("Clé du YAML Konnaxion divergente")
    if scalar(kx_text, "universe_contract") != "kx-universe-pack/v1":
        raise ValueError("Contrat du YAML Konnaxion incorrect")
    worlds_dir = kx.get("worlds_dir")
    if worlds_dir != "worlds":
        raise ValueError("La structure canonique attend worlds/")
    world_keys = re.findall(r"(?m)^\s*-\s*world_key:\s*([a-z0-9-]+)\s*$", kx_text)
    if not world_keys or len(set(world_keys)) != len(world_keys):
        raise ValueError("Worlds absents / clés dupliquées")
    kx_checks=[]
    for world_key in world_keys:
        if not ALLOWED_KEY.fullmatch(world_key):
            raise ValueError(f"Clé World invalide : {world_key}")
        world_file = entry(universe_dir,"worlds/"+world_key+"/world.yaml","World YAML")
        wt=world_file.read_text(encoding="utf-8-sig")
        if scalar(wt,"world_key") != world_key or scalar(wt,"world_contract") != "kx-world-pack/v1":
            raise ValueError(f"World incohérent : {world_key}")
        scenarios = re.findall(r"(?m)^\s*-\s*(scenarios/[^\s#]+\.json|scenario\.json)\s*$",wt)
        if not scenarios: raise ValueError(f"World sans scénario : {world_key}")
        for sc in scenarios:
            sp=entry(world_file.parent,sc,"Konnaxion scénario")
            json.loads(sp.read_text(encoding="utf-8-sig"))
        kx_checks.append({"key":world_key,"yaml_sha256":digest(world_file),"scenario_count":len(scenarios)})
    orgo_spec=spec.get("orgo",{})
    slug=orgo_spec.get("organization_slug")
    key=orgo_spec.get("world_pack_key")
    if slug!=universe or key!=universe or not ALLOWED_KEY.fullmatch(key or ""):
        raise ValueError("Clé Orgo organization / World divergente")
    if orgo_spec.get("injector_schema")!="orgo.scenario.v1" or orgo_spec.get("apply_mode")!="explicit-only":
        raise ValueError("Boundary d'injection Orgo invalide")
    pack_dir=(orgo_worlds / "world-packs" / key).resolve()
    if not pack_dir.is_relative_to(orgo_worlds.resolve()):raise ValueError("Orgo pack hors catalogue")
    pack_file=entry(pack_dir,"world-pack.json","Orgo World Pack")
    pack=json.loads(pack_file.read_text(encoding="utf-8"))
    if pack.get("format")!="orgo.world.pack.v1" or pack.get("world",{}).get("key")!=key:
        raise ValueError("Contrat Orgo invalide")
    target=pack.get("target",{})
    if target.get("organization_key")!=slug or target.get("injector_schema")!="orgo.scenario.v1":
        raise ValueError("Organisation ou schéma Orgo divergent")
    if pack.get("identifiers",{}).get("source_ref")!=spec.get("source_state_ref"):
        raise ValueError("Référence Kristal divergente")
    cps=pack.get("checkpoint_order",[])
    rows=pack.get("checkpoints",{})
    if not isinstance(cps,list) or len(cps)!=len(set(cps)) or set(cps)!=set(rows):
        raise ValueError("Checkpoints Orgo absents / dupliqués")
    if len(cps)!=orgo_spec.get("checkpoint_count"):
        raise ValueError("Nombre de checkpoints Orgo divergent")
    checks=[]
    for cp in cps:
        source=entry(pack_dir,rows[cp]["scenario"],"Orgo scénario")
        doc=json.loads(source.read_text(encoding="utf-8-sig"))
        if doc.get("schema_version")!="orgo.scenario.v1" or doc.get("scenario",{}).get("synthetic") is not True:
            raise ValueError(f"Scénario Orgo non synthétique/invalide: {cp}")
        checks.append({"checkpoint":cp,"sha256":digest(source)})
    injector=entry(orgo,"tools/scenario-injector/cli.mjs","Injecteur Orgo")
    bridge=entry(orgo_worlds,"tools/world-scenario/cli.mjs","Bridge Orgo_Worlds")
    builder=entry(capsule,"kx_builder/images.py","Builder Capsule")
    # builder: runtime Konnaxion seulement; Orgo doit être supporté avant GO LIVE.
    return {
        "ok":True,"mode":"read-only","universe":universe,"source_state_ref":spec.get("source_state_ref"),
        "konnaxion":{"manifest":str(kx_manifest),"manifest_sha256":digest(kx_manifest),"world_count":len(world_keys),"worlds":kx_checks},
        "orgo":{"organization_slug":slug,"pack":str(pack_file),"pack_sha256":digest(pack_file),"checkpoint_count":len(cps),"checkpoints":checks,"injector":str(injector),"bridge":str(bridge)},
        "capsule":{"builder":str(builder),"two_product_build_supported":False,"two_product_upload_supported":False,"dns_tls_configured":False},
        "next_gates":["Builder Orgo images", "manifest release conjoint", "deployment readiness et rollback", "DNS/TLS/SSO", "E2E API/worker + autorisations"],
        "warning":"AUCUN seed appliqué; aucun déploiement; aucune publication DNS"
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Préflight tandem, lecture seule")
    p.add_argument("--universe",default="fakeplant")
    p.add_argument("--konnaxion-worlds",type=Path,required=True)
    p.add_argument("--orgo-worlds",type=Path,required=True)
    p.add_argument("--orgo",type=Path,required=True)
    p.add_argument("--capsule",type=Path,required=True)
    p.add_argument("--output",type=Path,help="Sortie explicite JSON (facultative)")
    args=p.parse_args(argv)
    try:
        report=inspect(args.universe,args.konnaxion_worlds,args.orgo_worlds,args.orgo,args.capsule)
    except (ValueError,KeyError,TypeError,FileNotFoundError,json.JSONDecodeError) as exc:
        print("TANDEM PREFLIGHT FAIL: "+str(exc),file=sys.stderr)
        return 1
    result=json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:
        args.output.write_text(result+"\n",encoding="utf-8")
        print("TANDEM PREFLIGHT PASS: %s : %d Worlds + %d scénarios Orgo. Rapport %s" % (args.universe,report['konnaxion']['world_count'],report['orgo']['checkpoint_count'],args.output))
    else: print(result)
    return 0

if __name__=="__main__":raise SystemExit(main())
