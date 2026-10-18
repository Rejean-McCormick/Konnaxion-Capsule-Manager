"""Capsule Manager Tandem V2 command line (explicitly NO remote deploy)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from .release import TandemError, build_release, inspect_sources, verify_release, deployment_plan


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Konnaxion + Orgo: vérification et construction d'une release jumelée (NO GO LIVE)")
    p.add_argument("--universe", default="fakeplant")
    p.add_argument("--konnaxion-worlds", type=Path, default=Path(r"C:\mycode\Konnaxion\Konnaxion_Worlds"))
    p.add_argument("--orgo-worlds", type=Path, default=Path(r"C:\mycode\Orgo\Orgo_Worlds"))
    p.add_argument("--orgo", type=Path, default=Path(r"C:\mycode\Orgo\Orgo"))
    p.add_argument("--capsule-manager", type=Path, default=Path.cwd())
    sub = p.add_subparsers(dest="operation", required=True)
    inspect = sub.add_parser("preflight", help="Lecture seule: packs, Worlds, scénarios, mapping optionnel")
    inspect.add_argument("--binding", type=Path)
    inspect.add_argument("--strict-binding", action="store_true")
    build = sub.add_parser("build", help="Construire les 2 images Orgo + verrouiller une capsule Konnaxion signée")
    build.add_argument("--binding", type=Path, required=True)
    build.add_argument("--kxcap", type=Path, required=True, help="Capsule Konnaxion signée, générée par le Builder existant")
    build.add_argument("--public-key", type=Path, required=True)
    build.add_argument("--private-key", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--release-id", required=True)
    verify = sub.add_parser("verify", help="Vérifier signatures + SHA256 de tous les artefacts")
    verify.add_argument("--release-dir", type=Path, required=True)
    verify.add_argument("--public-key", type=Path, required=True)
    verify.add_argument("--verify-konnaxion-capsule", action="store_true")
    plan = sub.add_parser("plan", help="Plan d'Agent jumelé (lecture seule, GO LIVE bloqué)")
    plan.add_argument("--release-dir", type=Path, required=True)
    plan.add_argument("--public-key", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.operation == "plan":
            report = deployment_plan(args.release_dir, args.public_key)
        elif args.operation == "verify":
            report = verify_release(args.release_dir, args.public_key, verify_kx=args.verify_konnaxion_capsule, capsule=args.capsule_manager)
        else:
            report = inspect_sources(universe=args.universe, kx_worlds=args.konnaxion_worlds, orgo_worlds=args.orgo_worlds,
                                     orgo=args.orgo, capsule=args.capsule_manager, binding_file=args.binding)
            if args.operation == "preflight":
                if args.strict_binding and not report["ready_to_build"]:
                    raise TandemError("Binding Orgo UUID/ACL manquant. Préflight strict non satisfait.")
                report = {key: value for key, value in report.items() if key not in ("sources", "binding")}
            else:
                report = build_release(preflight=report, capsule_root=args.capsule_manager, orgo=args.orgo,
                                       kxcap=args.kxcap, public_key=args.public_key, private_key=args.private_key,
                                       output=args.out, release_id=args.release_id,
                                       orgo_worlds_api_origin=report["binding"]["orgo_worlds_api_origin"])
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (TandemError, OSError, ValueError, KeyError, json.JSONDecodeError, ImportError) as exc:
        print("TANDEM V2 ÉCHEC : " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
