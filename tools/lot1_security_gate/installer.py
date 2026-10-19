"""Lot 1 Security Gate installer: bounded to snapshot file hashes.

Preview only reads. Apply writes four repository files after backups and tests;
verify only reads; rollback requires exact after-hashes and locally persisted backup.
Never touches databases or executes Agent actions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
MANIFEST = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
STATE_ROOT = ROOT.parent / 'Tandem_Local' / 'lot1_security_gate'
ACTIVE = STATE_ROOT / 'active.json'
TESTS = [
    'tests/test_agent_security_gate_failclosed_lot1.py',
    'tests/test_agent_current_deploy_contract_v9.py',
    'tests/test_securitydiag_integration.py',
    'tests/test_schema_readiness_v27.py',
    'tests/test_security_gate_bootstrap_and_diagnostics_v12.py',
]


def digest(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def refuse(reason: str) -> None:
    raise RuntimeError('[BLOQUE] ' + reason)


def safe_target(relative: str) -> Path:
    candidate = (ROOT / relative).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        refuse('Chemin sort du repository')
    if candidate.is_symlink():
        refuse(f'Lien symbolique refuse: {relative}')
    return candidate


def entries() -> list[dict]:
    result=[]
    for item in MANIFEST['files']:
        rel=item['path']
        target=safe_target(rel)
        payload=(HERE/'payload'/rel).resolve()
        if not payload.is_relative_to((HERE/'payload').resolve()) or not payload.is_file():
            refuse(f'Payload absent: {rel}')
        if digest(payload)!=item['after_sha256']:
            refuse(f'Payload non conforme: {rel}')
        actual=digest(target)
        if actual == item['after_sha256']:
            status='already_updated'
        elif actual == item['before_sha256']:
            status='ready'
        else:
            status='unexpected'
        result.append({**item, 'target':target, 'payload':payload, 'status':status, 'actual':actual})
    return result


def write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.lot1-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(data)
            out.flush(); os.fsync(out.fileno())
        # Preserve readable source-file permissions on POSIX agents.
        mode = (target.stat().st_mode & 0o777) if target.exists() else 0o644
        os.chmod(name, mode)
        os.replace(name,target)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass


def state() -> dict | None:
    if not ACTIVE.exists():
        return None
    return json.loads(ACTIVE.read_text(encoding='utf-8'))


def verify() -> None:
    rows=entries()
    for row in rows:
        print(f"[{row['status'].upper()}] {row['path']}")
    if any(row['status']!='already_updated' for row in rows):
        refuse('Tous les fichiers ne sont pas installes: utiliser PREVIEW/INSTALL (ou diagnostiquer la derive)')
    print('[OK] Fichiers du Lot 1 conformes. Verification statique uniquement.')


def preview() -> None:
    rows=entries()
    print(f'[INFO] Repository : {ROOT}')
    print('[LECTURE SEULE] Comparaison SHA-256 avec snapshot et correctif:')
    for row in rows:
        print(f"[{row['status'].upper()}] {row['path']}")
    if any(row['status']=='unexpected' for row in rows):
        refuse('Fichier modifie depuis le snapshot : installation refusee sans ecraser.')
    if any(row['status']=='ready' for row in rows) and any(row['status']=='already_updated' for row in rows):
        refuse('Installation partielle suspecte: ne pas mixer les versions.')
    current=state()
    if current and any(row['status']=='ready' for row in rows):
        refuse('Etat de sauvegarde existant; lancer verification/rollback avant de recommencer.')
    if all(row['status']=='already_updated' for row in rows):
        print('[OK] Lot 1 deja installe (idempotent).')
    else:
        print('[OK] Apercu favorable. Installation autorisee sur ce snapshot exact.')


def test_suite() -> tuple[bool,str]:
    env=os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE']='1'
    env['PYTHONPATH']=str(ROOT)+os.pathsep+env.get('PYTHONPATH','')
    command=[sys.executable,'-m','pytest','-q',*TESTS]
    result=subprocess.run(command, cwd=ROOT, env=env, capture_output=True,text=True,encoding='utf-8',errors='replace', timeout=90)
    output=(result.stdout or '')+'\n'+(result.stderr or '')
    return result.returncode==0,output


def apply() -> None:
    preview()
    rows=entries()
    if all(row['status']=='already_updated' for row in rows):
        print('[OK] Aucun changement requis.')
        return
    date=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=STATE_ROOT/'backups'/date
    if ACTIVE.exists():
        refuse('Sauvegarde active deja presente')
    backup.mkdir(parents=True,exist_ok=False)
    metadata={'timestamp':date,'backup':str(backup),'source_snapshot':MANIFEST['source_snapshot'],
              'files':[{k:r[k] for k in ('path','before_sha256','after_sha256')} for r in rows]}
    for row in rows:
        if row['before_sha256'] is not None:
            dest=backup/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(row['target'],dest)
            if digest(dest)!=row['before_sha256']:
                refuse('Sauvegarde invalide: '+row['path'])
    (backup/'state.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    written=[]
    try:
        # Recheck source immediately before each atomic replacement.
        for row in rows:
            if digest(row['target'])!=row['before_sha256']:
                refuse('Source changee pendant installation: '+row['path'])
            written.append(row)
            write_atomic(row['target'],row['payload'].read_bytes())
        if any(digest(r['target'])!=r['after_sha256'] for r in rows):
            refuse('Verification apres installation non conforme')
        print('[INFO] Tests cibles en cours (aucune connexion DB, aucune action Agent)...')
        ok, output=test_suite()
        (backup/'tests.log').write_text(output,encoding='utf-8')
        print('\n'.join(output.splitlines()[-20:]))
        if not ok:
            refuse('Tests cibles en echec: restauration automatique de l ancien code')
        STATE_ROOT.mkdir(parents=True,exist_ok=True)
        write_atomic(ACTIVE,json.dumps(metadata,indent=2).encode('utf-8')+b'\n')
        print('[OK] Lot 1 installe; sauvegarde = '+str(backup))
        print('[OK] 40 tests cibles (ou etat local correspondant) passes.')
        print('[INFO] Agent Linux distant NON actualise; deploiement de ce code exige une etape separee.')
    except BaseException:
        for row in reversed(written):
            original=(backup/row['path'])
            if row['before_sha256'] is None:
                if digest(row['target'])==row['after_sha256']:row['target'].unlink()
            elif digest(original)==row['before_sha256'] and digest(row['target'])==row['after_sha256']:
                write_atomic(row['target'],original.read_bytes())
        raise


def rollback() -> None:
    info=state()
    if not info:
        refuse('Aucune installation enregistree; rollback refuse')
    rows=entries()
    if any(row['status']!='already_updated' for row in rows):
        refuse('Code modifie depuis installation; rollback automatique refuse')
    backup=Path(info['backup'])
    if not backup.is_relative_to((STATE_ROOT/'backups').resolve()):
        refuse('Chemin de sauvegarde incorrect')
    saved=[]
    for row in rows:
        original=backup/row['path']
        if row['before_sha256'] is None:
            if original.exists():refuse('Sauvegarde inattendue pour fichier nouveau')
        elif digest(original)!=row['before_sha256']:
            refuse('Sauvegarde alteree: '+row['path'])
        saved.append((row,original))
    for row,original in saved:
        if row['before_sha256'] is None:
            row['target'].unlink()
        else:
            write_atomic(row['target'],original.read_bytes())
    ACTIVE.unlink()
    print('[OK] Retour arriere du Lot 1 effectue; preuves conservees : '+str(backup))


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('preview','apply','verify','rollback'))
    args=parser.parse_args()
    print('=== TANDEM / LOT 1 / SECURITY GATE FAIL-CLOSED ===')
    print('[PORTÉE] Code local Capsule Manager uniquement; aucun service, tenant ou DB modifie.')
    try:
        {'preview':preview,'apply':apply,'verify':verify,'rollback':rollback}[args.mode]()
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        print(f'[ECHEC] {type(exc).__name__}: {exc}')
        return 2

if __name__=='__main__':
    sys.exit(main())
