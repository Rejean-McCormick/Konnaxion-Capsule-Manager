"""Configure only non-secret Konnaxion -> Orgo IK endpoint / tenant values.

No DB/network, secrets, workflow, token or Docker operations.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

IK_VALUES = {
    "IK_ORGO_INTERACTIONS_URL": "http://127.0.0.1:4000/api/v3/ik/interactions",
    "IK_ORGO_TARGET_ORGANIZATION": "0c2d7244-a0c0-4a10-8f11-0389f275f133",
}


def transform(original: bytes) -> tuple[bytes, dict[str, str]]:
    # Decode/encode losslessly. Do not serialize unrelated .env values or strip comments.
    text = original.decode("utf-8-sig")
    bom = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
    newline = "\r\n" if b"\r\n" in original else "\n"
    lines = text.splitlines(keepends=True)
    statuses: dict[str, str] = {}
    for key, expected in IK_VALUES.items():
        pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=")
        matches = [i for i, line in enumerate(lines) if pattern.match(line)]
        if len(matches) > 1:
            raise ValueError(f"{key}: definitions multiples, aucun fichier modifie")
        if not matches:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(f"{key}={expected}{newline}")
            statuses[key] = "ajoute"
        else:
            idx = matches[0]
            rhs = lines[idx].split("=", 1)[1].strip()
            # Surrounding quotes permitted; inline comments or unusual syntax fail closed.
            current = rhs.strip('"\'')
            if current == expected:
                statuses[key] = "deja conforme"
            elif not rhs or rhs.startswith("#"):
                lines[idx] = f"{key}={expected}{newline}"
                statuses[key] = "valeur vide remplie"
            else:
                raise ValueError(f"{key}: deja configure avec une autre valeur; aucun ecrasement")
    output = bom + "".join(lines).encode("utf-8")
    return output, statuses


def configure(env_path: Path, apply: bool) -> dict:
    if not env_path.is_file():
        raise FileNotFoundError(f"backend/.env introuvable : {env_path}")
    original = env_path.read_bytes()
    updated, statuses = transform(original)
    changed = updated != original
    if apply and changed:
        fd, tmp = tempfile.mkstemp(prefix=".koa-ik-", dir=str(env_path.parent))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, env_path.stat().st_mode)
            os.replace(tmp, env_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return {
        "ok": True,
        "mode": "apply" if apply else "preview",
        "configuration": statuses,
        "changed": changed if apply else False,
        "secrets_not_displayed": True,
        "unrelated_env_keys_preserved": True,
        "database_writes": False,
        "ik_outbound_token_configured_by_this_script": False,
        "ik_inbound_token_configured_by_this_script": False,
        "requires_konnaxion_restart_if_changed": bool(apply and changed),
        "next_blockers": ["IK tokens dedies", "workflow publie FakePlant", "test E2E IK"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not (args.backend / "manage.py").is_file() or not (args.backend / "config/settings/base.py").is_file():
        parser.error("Konnaxion backend invalide; aucune modification")
    try:
        result = configure(args.backend / ".env", args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "modified": False}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
